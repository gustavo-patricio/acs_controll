"""Read operations through HTTP and durable CWMP session transitions."""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, BasicAuth
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from acs.api.app import create_app as admin_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.inform import InformService
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.evidence import anonymized_inform
from acs.cwmp.compatibility.profiles import ProfileInventoryNormalizer
from acs.cwmp.compatibility.queries import NAMES, HuaweiQueryPolicy
from acs.cwmp.http.app import COOKIE
from acs.cwmp.http.app import create_app as cwmp_app
from acs.cwmp.soap.inform import parse_envelope
from acs.cwmp.soap.queries import parse_query_reply
from acs.domain.cpe.inform import Inform
from acs.domain.cpe.inventory import NormalizedInventory
from acs.domain.events.session import InvalidSession, StorageUnavailable
from acs.domain.operations.query import QueryConflict, QueryKind
from acs.domain.security import AuthenticationFailed, Principal
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.cwmp_models import (
    Cpe,
    CpeInventoryField,
    CwmpSessionRow,
)
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import PostgresInformRepository
from acs.infrastructure.database.queries import PostgresQueryRepository
from acs.infrastructure.database.query_models import QueryAttemptRow, QueryOperationRow
from auth_support import PASSWORD, principal_for
from integration.test_inventory_provenance import (
    accept,
    cwmp_database,
    huawei,
    migrate_to,
)
from query_support import reply_xml

__all__ = ["cwmp_database", "huawei"]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ACS_RUN_INTEGRATION_TESTS") != "1",
        reason="PostgreSQL integration is opt-in",
    ),
]


class QueryAuth:
    def __init__(self, inform: Inform) -> None:
        self.device = principal_for(inform)
        self.admin = Principal(
            uuid4(), "admin", frozenset({"inventory:read", "cpe:query"})
        )

    async def authenticate(
        self, username: str, password: str, kind: Literal["admin", "cpe"]
    ) -> Principal:
        if password != PASSWORD:
            raise AuthenticationFailed
        if kind == "cpe":
            return (
                self.device
                if username == "device"
                else replace(self.device, id=uuid4())
            )
        return (
            self.admin
            if username == "admin"
            else replace(self.admin, permissions=frozenset({"inventory:read"}))
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["get_parameter_values", "get_parameter_names"])
async def test_authenticated_api_and_real_cwmp_read_flow(
    cwmp_database: Database, huawei: Inform, kind: QueryKind
) -> None:
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now)
    auth = QueryAuth(huawei)
    repository = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    queries = QueryService(repository)
    app = admin_app(
        CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
        auth,
        queries=queries,
    )
    receiver = cwmp_app(
        InformService(
            PostgresInformRepository(cwmp_database), ProfileInventoryNormalizer()
        ),
        auth,
        queries,
    )
    path = f"/api/v1/cpes/{initial.cpe_id}/queries"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        body = {"kind": kind, "idempotency_key": str(uuid4())}
        assert (await client.post(path, json=body)).status_code == 401
        assert (
            await client.post(path, json=body, auth=("support", PASSWORD))
        ).status_code == 403
        response = await client.post(path, json=body, auth=("admin", PASSWORD))
        assert response.status_code == 202
        operation_id = response.json()["id"]
        assert (await client.post(path, json=body, auth=("admin", PASSWORD))).json()[
            "id"
        ] == operation_id
        assert (
            await client.post(
                path,
                json={**body, "idempotency_key": str(uuid4())},
                auth=("admin", PASSWORD),
            )
        ).status_code == 409
        invalid = await client.post(
            path,
            json={**body, "parameter_names": ["SYNTHETIC-PRIVATE-PASSWORD"]},
            auth=("admin", PASSWORD),
        )
        assert invalid.status_code == 422 and "PRIVATE" not in invalid.text
        schema = (await client.get("/openapi.json")).json()
        assert schema["paths"]["/api/v1/cpes/{cpe_id}/queries"]["post"]["security"] == [
            {"AdminBasic": []}
        ]
        async with AsyncClient(
            transport=ASGITransport(app=receiver),
            base_url="https://test",
            auth=("device", PASSWORD),
        ) as cpe:
            assert (
                await cpe.post(
                    "/cwmp",
                    content=anonymized_inform(huawei, now),
                    headers={"Content-Type": "text/xml"},
                )
            ).status_code == 200
            cookie = cpe.cookies[COOKIE]
            wrong = await cpe.post(
                "/cwmp", content=b"", auth=BasicAuth("other", PASSWORD)
            )
            assert wrong.status_code == 400
            dispatched = await cpe.post("/cwmp", content=b"")
            assert dispatched.status_code == 200
            rpc_id, _ = parse_envelope(dispatched.content)
            assert (await cpe.post("/cwmp", content=b"")).content == dispatched.content
            for invalid_payload in (
                b"<broken",
                reply_xml(
                    rpc_id,
                    "get_parameter_names"
                    if kind == "get_parameter_values"
                    else "get_parameter_values",
                ),
            ):
                assert (
                    await cpe.post(
                        "/cwmp",
                        content=invalid_payload,
                        headers={"Content-Type": "text/xml"},
                    )
                ).status_code == 400
            assert (
                await client.get(path + "/" + operation_id, auth=("support", PASSWORD))
            ).json()["state"] == "dispatched"
            assert (
                await cpe.post(
                    "/cwmp",
                    content=reply_xml("wrong", kind),
                    headers={"Content-Type": "text/xml"},
                )
            ).status_code == 400
            reply = reply_xml(rpc_id, kind)
            result = await cpe.post(
                "/cwmp", content=reply, headers={"Content-Type": "text/xml"}
            )
            assert result.status_code == 204
            duplicate = await cpe.post(
                "/cwmp",
                content=reply,
                headers={"Content-Type": "text/xml", "Cookie": f"{COOKIE}={cookie}"},
            )
            assert duplicate.status_code == 204
        observed = await client.get(
            path + "/" + operation_id, auth=("support", PASSWORD)
        )
        assert observed.status_code == 200 and observed.json()["state"] == "succeeded"
        assert observed.json()["attempt_count"] == 1
        assert {item["name"] for item in observed.json()["result"]} == set(NAMES)
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(QueryAttemptRow)) == 1
        assert await db.scalar(select(func.count()).select_from(Cpe)) == 1
        source = await db.get(CpeInventoryField, (initial.cpe_id, "model"))
        assert source is not None
        assert source.value_source_method == (
            "GetParameterValues" if kind == "get_parameter_values" else "Inform"
        )
        if kind == "get_parameter_values":
            assert str(source.value_operation_id) == operation_id
            assert source.value_source_parameter == NAMES[0]


@pytest.mark.asyncio
async def test_query_concurrency_expiration_and_changed_profile(
    cwmp_database: Database, huawei: Inform
) -> None:
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now, token="session")
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    key = uuid4()
    operations = await asyncio.gather(
        *(
            repo.create(initial.cpe_id, "get_parameter_values", key, None, "local", now)
            for _ in range(3)
        )
    )
    assert len({operation.id for operation in operations}) == 1
    row = operations[0]
    with pytest.raises(QueryConflict):
        await repo.create(
            initial.cpe_id, "get_parameter_names", key, None, "local", now
        )
    commands = await asyncio.gather(
        *(repo.exchange("session", None, now) for _ in range(2))
    )
    assert commands[0][1] == commands[1][1]
    command = commands[0][1]
    assert command is not None
    expired = await repo.get(initial.cpe_id, row.id, now + timedelta(seconds=61))
    assert expired.state == "expired"
    session, _ = await repo.exchange(
        "session",
        parse_query_reply(reply_xml(command.rpc_id, "get_parameter_values")),
        now + timedelta(seconds=62),
    )
    assert session.state == "completed"
    assert (
        await repo.get(initial.cpe_id, row.id, now + timedelta(seconds=63))
    ).state == "expired"
    pending = await repo.create(
        initial.cpe_id,
        "get_parameter_values",
        uuid4(),
        None,
        "local",
        now + timedelta(minutes=2),
    )
    assert (
        await repo.get(initial.cpe_id, pending.id, now + timedelta(minutes=13))
    ).state == "expired"
    changed = await repo.create(
        initial.cpe_id,
        "get_parameter_values",
        uuid4(),
        None,
        "local",
        now + timedelta(minutes=14),
    )
    await accept(
        cwmp_database, huawei, now + timedelta(minutes=14), token="new-session"
    )
    async with cwmp_database.session() as db, db.begin():
        await db.execute(
            update(Cpe).where(Cpe.id == initial.cpe_id).values(firmware="NEW")
        )
    _, command = await repo.exchange("new-session", None, now + timedelta(minutes=14))
    assert command is None
    assert (
        await repo.get(initial.cpe_id, changed.id, now + timedelta(minutes=14))
    ).failure_reason == "profile_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["fault", "unexpected", "invalid_value", "firmware_changed", "names_filtered"],
)
async def test_read_results_are_bounded_and_failures_preserve_inventory(
    cwmp_database: Database, huawei: Inform, case: str
) -> None:
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now, token="session")
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    kind: QueryKind = (
        "get_parameter_names" if case == "names_filtered" else "get_parameter_values"
    )
    operation = await repo.create(initial.cpe_id, kind, uuid4(), None, "local", now)
    _, command = await repo.exchange("session", None, now)
    assert command is not None
    if case == "fault":
        payload = reply_xml(command.rpc_id, kind, fault=9005)
    elif case == "unexpected":
        payload = reply_xml(
            command.rpc_id, kind, names=(NAMES[0], NAMES[1], "Device.Password")
        )
    elif case == "invalid_value":
        payload = reply_xml(
            command.rpc_id,
            kind,
            values=("PRIVATE-INVALID" * 30, "V5R022C00S364", "343D.D"),
        )
    elif case == "firmware_changed":
        payload = reply_xml(
            command.rpc_id, kind, values=("EG8145X6-10", "NEW-FW", "343D.D")
        )
    else:
        payload = reply_xml(
            command.rpc_id,
            kind,
            names=NAMES + ("InternetGatewayDevice.DeviceInfo.X_PRIVATE",),
        )
    reply = parse_query_reply(payload)
    await repo.exchange("session", reply, now + timedelta(seconds=1))
    state = await repo.get(initial.cpe_id, operation.id, now + timedelta(seconds=2))
    assert state.state == (
        "succeeded" if case in ("firmware_changed", "names_filtered") else "failed"
    )
    assert "PRIVATE" not in repr(state)
    await repo.exchange("session", reply, now + timedelta(seconds=3))
    with pytest.raises(InvalidSession):
        await repo.exchange(
            "session", replace(reply, rpc_id="other"), now + timedelta(seconds=4)
        )
    async with cwmp_database.session() as db:
        cpe = await db.get(Cpe, initial.cpe_id)
        assert cpe is not None and cpe.model == "EG8145X6-10"
        row = await db.get(QueryOperationRow, operation.id)
        assert row is not None and "PRIVATE" not in str(row.result)
        assert row.fault_code == (9005 if case == "fault" else None)
        if case == "firmware_changed":
            assert cpe.firmware == "NEW-FW"
            provenance = await db.get(CpeInventoryField, (initial.cpe_id, "firmware"))
            assert (
                provenance is not None
                and provenance.value_profile_id == "generic-device-info"
            )


@pytest.mark.asyncio
async def test_query_result_and_inventory_rollback_atomically(
    cwmp_database: Database, huawei: Inform, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now, token="session")
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    operation = await repo.create(
        initial.cpe_id, "get_parameter_values", uuid4(), None, "local", now
    )
    _, command = await repo.exchange("session", None, now)
    assert command is not None
    original = PostgresInformRepository.update_inventory

    async def fail_after_update(
        db: AsyncSession, cpe_id: UUID, inventory: NormalizedInventory
    ) -> None:
        await original(db, cpe_id, inventory)
        raise IntegrityError("synthetic", {}, Exception())

    with monkeypatch.context() as scoped:
        scoped.setattr(PostgresInformRepository, "update_inventory", fail_after_update)
        with pytest.raises(StorageUnavailable):
            await repo.exchange(
                "session",
                parse_query_reply(reply_xml(command.rpc_id, "get_parameter_values")),
                now + timedelta(seconds=1),
            )
    async with cwmp_database.session() as db:
        field = await db.get(CpeInventoryField, (initial.cpe_id, "model"))
        attempt = await db.get(QueryAttemptRow, operation.id)
        session = await db.get(CwmpSessionRow, initial.id)
        assert (
            field is not None
            and field.source_method == "Inform"
            and field.operation_id is None
        )
        assert attempt is not None and attempt.response_digest is None
        assert session is not None and session.state == "awaiting_response"
    assert (await repo.get(initial.cpe_id, operation.id, now)).state == "dispatched"
    await repo.exchange(
        "session",
        parse_query_reply(reply_xml(command.rpc_id, "get_parameter_values")),
        now + timedelta(seconds=2),
    )
    with pytest.raises(InvalidSession):
        await repo.exchange(
            "session",
            parse_query_reply(
                reply_xml(
                    command.rpc_id,
                    "get_parameter_values",
                    values=("DIFFERENT", "V5R022C00S364", "343D.D"),
                )
            ),
            now + timedelta(seconds=3),
        )


@pytest.mark.asyncio
async def test_query_migration_preserves_inform_metadata_and_audit_history(
    cwmp_database: Database, huawei: Inform
) -> None:
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now)
    async with cwmp_database.engine.begin() as connection:
        await connection.run_sync(
            lambda c: migrate_to(c, "0004_inventory_provenance", downgrade=True)
        )
        await connection.run_sync(lambda c: migrate_to(c, "head"))
    async with cwmp_database.session() as db:
        field = await db.get(CpeInventoryField, (initial.cpe_id, "model"))
        assert (
            field is not None
            and field.value_source_method == field.source_method == "Inform"
        )
        assert field.value_operation_id is None and field.value_received_at == now
    with pytest.raises(IntegrityError):
        async with cwmp_database.session() as db, db.begin():
            await db.execute(update(CpeInventoryField).values(value_source_method=None))
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    operation = await repo.create(
        initial.cpe_id, "get_parameter_names", uuid4(), None, "local", now
    )
    with pytest.raises(RuntimeError, match="archived"):
        async with cwmp_database.engine.begin() as connection:
            await connection.run_sync(
                lambda c: migrate_to(c, "0004_inventory_provenance", downgrade=True)
            )
    assert (await repo.get(initial.cpe_id, operation.id, now)).state == "pending"
    async with cwmp_database.session() as db:
        row = await db.get(QueryOperationRow, operation.id)
        assert (
            row is not None
            and row.local_actor == f"uid:{os.getuid()}"
            and row.requested_by is None
        )
