"""Real PostgreSQL tests in disposable schemas migrated by Alembic."""

import argparse
import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from httpx import ASGITransport, BasicAuth
from httpx import AsyncClient as RealClient
from pydantic import PostgresDsn
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import Connection, make_url

from acs.api.app import create_app as real_admin_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.inform import InformService
from acs.cwmp.http.app import create_app as real_cwmp_app
from acs.cwmp.soap.inform import parse_inform
from acs.domain.cpe.inform import Event
from acs.domain.events.session import InvalidSession, StorageUnavailable
from acs.domain.security import AccessDenied, AuthenticationFailed
from acs.infrastructure.database.auth_models import AuthAccount
from acs.infrastructure.database.base import Base
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.cwmp_models import Cpe, CwmpEvent, CwmpSessionRow
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import PostgresInformRepository
from acs.infrastructure.security import accounts as account_cli
from acs.infrastructure.security.authentication import PostgresAuthenticator
from acs.infrastructure.security.passwords import hash_password, verify_password
from acs.settings import get_settings
from auth_support import PASSWORD, principal_for
from auth_support import admin_app as create_admin_app
from auth_support import authorized_client as AsyncClient
from auth_support import cwmp_app as create_app
from cwmp_simulator import simulate_session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ACS_RUN_INTEGRATION_TESTS") != "1",
        reason="PostgreSQL integration is opt-in",
    ),
]


def migrate(connection: Connection, direction: str = "up") -> None:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.attributes["connection"] = connection
    if direction == "up":
        command.upgrade(config, "head")
    else:
        command.downgrade(config, "base")


@pytest_asyncio.fixture
async def cwmp_database() -> AsyncGenerator[Database]:
    schema = f"test_cwmp_{uuid4().hex}"
    settings = get_settings()
    admin = Database(settings)
    url = make_url(str(settings.database_url)).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    database = Database(
        settings.model_copy(
            update={
                "database_url": PostgresDsn(url.render_as_string(hide_password=False))
            }
        )
    )
    try:
        async with admin.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with database.engine.begin() as connection:
            await connection.run_sync(migrate)
        yield database
    finally:
        await database.close()
        async with admin.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.close()


@pytest.mark.asyncio
async def test_authenticated_flow_identity_isolation_revocation_and_admin_access(
    cwmp_database: Database,
    inform_xml: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="acs.cwmp")
    caplog.set_level(logging.INFO, logger="acs.security")
    encoded = hash_password(PASSWORD)
    accounts = [
        AuthAccount(
            id=uuid4(),
            kind="cpe",
            username="device-one",
            password_hash=encoded,
            enabled=True,
            role="device",
            oui="A1B2C3",
            product_class="LAB-ONT",
            serial_number="LAB000001",
        ),
        AuthAccount(
            id=uuid4(),
            kind="cpe",
            username="device-two",
            password_hash=encoded,
            enabled=True,
            role="device",
            oui="A1B2C3",
            product_class="LAB-ONT",
            serial_number="OTHER",
        ),
        AuthAccount(
            id=uuid4(),
            kind="admin",
            username="reader",
            password_hash=encoded,
            enabled=True,
            role="support",
        ),
        AuthAccount(
            id=uuid4(),
            kind="admin",
            username="pending",
            password_hash=encoded,
            enabled=True,
            role="pending",
        ),
    ]
    async with cwmp_database.session() as db:
        db.add_all(accounts)
        await db.commit()
    authenticator = PostgresAuthenticator(cwmp_database)
    app = real_cwmp_app(
        InformService(PostgresInformRepository(cwmp_database)), authenticator
    )
    async with RealClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (await client.post("/cwmp", content=inform_xml)).status_code == 401
        for auth in (
            ("device-one", "wrong-password"),
            ("unknown", PASSWORD),
            ("reader", PASSWORD),
        ):
            response = await client.post(
                "/cwmp",
                content=inform_xml,
                headers={"Content-Type": "text/xml"},
                auth=auth,
            )
            assert response.status_code == 401
            assert "set-cookie" not in response.headers
        async with cwmp_database.session() as db:
            assert await db.scalar(select(func.count()).select_from(Cpe)) == 0
            assert (
                await db.scalar(select(func.count()).select_from(CwmpSessionRow)) == 0
            )
        mismatch = await client.post(
            "/cwmp",
            content=inform_xml,
            headers={"Content-Type": "text/xml"},
            auth=("device-two", PASSWORD),
        )
        assert mismatch.status_code == 403
        response = await client.post(
            "/cwmp",
            content=inform_xml,
            headers={"Content-Type": "text/xml"},
            auth=("device-one", PASSWORD),
        )
        assert response.status_code == 200
        token = client.cookies["acs_cwmp_session"]
        assert "Secure" in response.headers["set-cookie"]
        assert (await client.post("/cwmp", content=b"")).status_code == 401
        assert (
            await client.post("/cwmp", content=b"", auth=("device-two", PASSWORD))
        ).status_code == 400
        async with cwmp_database.session() as db:
            row = (await db.scalars(select(CwmpSessionRow))).one()
            assert row.state == "awaiting_empty"
            assert await db.scalar(select(func.count()).select_from(Cpe)) == 1
        async with cwmp_database.session() as db:
            await db.execute(
                update(AuthAccount)
                .where(AuthAccount.id == accounts[0].id)
                .values(enabled=False)
            )
            await db.commit()
        assert (
            await client.post("/cwmp", content=b"", auth=("device-one", PASSWORD))
        ).status_code == 401
        rotated = "another-synthetic-password"
        async with cwmp_database.session() as db:
            await db.execute(
                update(AuthAccount)
                .where(AuthAccount.id == accounts[0].id)
                .values(enabled=True, password_hash=hash_password(rotated))
            )
            await db.commit()
        assert (
            await client.post("/cwmp", content=b"", auth=("device-one", PASSWORD))
        ).status_code == 401
        assert (
            await client.post("/cwmp", content=b"", auth=("device-one", rotated))
        ).status_code == 204
        assert "acs_cwmp_session" not in client.cookies
        assert all(
            secret not in caplog.text for secret in (PASSWORD, rotated, encoded, token)
        )
    inventory = CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database))
    admin = real_admin_app(inventory, authenticator)
    async with RealClient(
        transport=ASGITransport(app=admin), base_url="https://test"
    ) as client:
        path = f"/api/v1/cpes/{row.cpe_id}"
        for route in ("/api/v1/cpes", path, path + "/sessions", path + "/events"):
            assert (await client.get(route)).status_code == 401
            assert (
                await client.get(route, auth=("pending", PASSWORD))
            ).status_code == 403
            assert (
                await client.get(route, auth=("device-two", PASSWORD))
            ).status_code == 401
            response = await client.get(route, auth=("reader", PASSWORD))
            assert response.status_code == 200
            assert encoded not in response.text and "password_hash" not in response.text
            assert response.headers["x-request-id"] in caplog.text
        async with cwmp_database.session() as db:
            await db.execute(
                update(AuthAccount)
                .where(AuthAccount.id == accounts[2].id)
                .values(enabled=False)
            )
            await db.commit()
        assert (await client.get(path, auth=("reader", PASSWORD))).status_code == 401
        assert all(
            secret not in caplog.text
            for secret in (PASSWORD, encoded, "device-one", "reader")
        )
    async with cwmp_database.session() as db:
        session = (await db.scalars(select(CwmpSessionRow))).one()
        assert session.state == "completed"


@pytest.mark.asyncio
async def test_shared_profile_multiple_devices_and_access_controls(
    cwmp_database: Database,
    inform_xml: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(
        update={
            "database_url": PostgresDsn(
                cwmp_database.engine.url.render_as_string(hide_password=False)
            ),
        }
    )
    monkeypatch.setattr(account_cli, "get_settings", lambda: settings)
    args = argparse.Namespace(
        action="create",
        kind="cpe",
        username="shared-lab",
        shared=True,
        role="device",
        oui=None,
        product_class=None,
        serial_number=None,
    )
    await account_cli.execute(args, hash_password(PASSWORD))
    denied = PostgresAuthenticator(cwmp_database)
    with pytest.raises(AuthenticationFailed):
        await denied.authenticate("shared-lab", PASSWORD, "cpe")
    authenticator = PostgresAuthenticator(cwmp_database, allow_shared_cpe=True)
    with pytest.raises(AuthenticationFailed):
        await authenticator.authenticate("shared-lab", PASSWORD, "admin")
    principal = await authenticator.authenticate("shared-lab", PASSWORD, "cpe")
    assert principal.shared_cpe and principal.serial_number is None
    with pytest.raises(AccessDenied):
        principal.require("inventory:read")
    app = real_cwmp_app(
        InformService(PostgresInformRepository(cwmp_database)), authenticator
    )
    async with RealClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        auth=("shared-lab", PASSWORD),
    ) as client:
        assert (
            await client.post(
                "/cwmp", content=inform_xml, auth=("shared-lab", "incorrect")
            )
        ).status_code == 401
        assert (
            await client.post("http://test/cwmp", content=inform_xml)
        ).status_code == 403
        second = inform_xml.replace(b"LAB000001", b"LAB000002")
        for payload in (inform_xml, second, inform_xml):
            response = await client.post(
                "/cwmp", content=payload, headers={"Content-Type": "text/xml"}
            )
            assert response.status_code == 200
            other = second if payload == inform_xml else inform_xml
            assert (
                await client.post(
                    "/cwmp", content=other, headers={"Content-Type": "text/xml"}
                )
            ).status_code == 400
            assert (await client.post("/cwmp", content=b"")).status_code == 204
        async with cwmp_database.session() as db:
            assert await db.scalar(select(func.count()).select_from(Cpe)) == 2
            sessions = (await db.scalars(select(CwmpSessionRow))).all()
            assert len(sessions) == 3
            assert all(session.state == "completed" for session in sessions)
            assert await db.scalar(select(func.count()).select_from(CwmpEvent)) == 6
        args.action, args.shared = "disable", False
        await account_cli.execute(args, None)
        assert (await client.post("/cwmp", content=inform_xml)).status_code == 401


@pytest.mark.asyncio
async def test_shared_profile_prevents_destructive_downgrade(
    cwmp_database: Database,
) -> None:
    async with cwmp_database.session() as db:
        db.add(
            AuthAccount(
                id=uuid4(),
                kind="cpe",
                username="shared",
                role="device",
                shared_cpe=True,
                password_hash=hash_password(PASSWORD),
                enabled=True,
            )
        )
        await db.commit()
    with pytest.raises(RuntimeError, match="Shared CPE accounts"):
        async with cwmp_database.engine.begin() as connection:
            await connection.run_sync(lambda c: migrate(c, "down"))
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(AuthAccount)) == 1


@pytest.mark.asyncio
async def test_account_cli_create_rotate_disable_in_isolated_schema(
    cwmp_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = get_settings().model_copy(
        update={
            "database_url": PostgresDsn(
                cwmp_database.engine.url.render_as_string(hide_password=False)
            ),
        }
    )
    monkeypatch.setattr(account_cli, "get_settings", lambda: settings)
    args = argparse.Namespace(
        action="create",
        kind="admin",
        username="bootstrap",
        role="support",
        oui=None,
        product_class=None,
        serial_number=None,
    )
    encoded = hash_password(PASSWORD)
    await account_cli.execute(args, encoded)
    with pytest.raises(ValueError, match="already exists"):
        await account_cli.execute(args, encoded)
    args.action = "disable"
    await account_cli.execute(args, None)
    args.action = "rotate"
    rotated = hash_password("synthetic-rotated-password")
    await account_cli.execute(args, rotated)
    async with cwmp_database.session() as db:
        account = (await db.scalars(select(AuthAccount))).one()
        assert not account.enabled
        assert verify_password("synthetic-rotated-password", account.password_hash)
        assert not verify_password(PASSWORD, account.password_hash)
    output = capsys.readouterr().out
    assert (
        "account_create" in output
        and "account_rotate" in output
        and "account_disable" in output
    )
    assert PASSWORD not in output and encoded not in output and rotated not in output


@pytest.mark.asyncio
async def test_missing_authentication_table_fails_closed(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    async with cwmp_database.engine.begin() as connection:
        await connection.execute(text("DROP TABLE auth_accounts"))
    authenticator = PostgresAuthenticator(cwmp_database)
    apps = (
        (
            real_admin_app(
                CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
                authenticator,
            ),
            "/api/v1/cpes",
            "GET",
        ),
        (
            real_cwmp_app(
                InformService(PostgresInformRepository(cwmp_database)), authenticator
            ),
            "/cwmp",
            "POST",
        ),
    )
    for app, path, method in apps:
        async with RealClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.request(
                method, path, auth=("test", PASSWORD), content=inform_xml
            )
            assert response.status_code == 503
            assert "SELECT" not in response.text and PASSWORD not in response.text
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(Cpe)) == 0


@pytest.mark.asyncio
async def test_registered_cpes_are_visible_in_paginated_inventory(
    cwmp_database: Database,
    inform_xml: bytes,
) -> None:
    inventory = CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database))
    async with AsyncClient(
        transport=ASGITransport(app=create_admin_app(inventory)),
        base_url="http://test",
    ) as client:
        assert (await client.get("/api/v1/cpes")).json() == {
            "items": [],
            "total": 0,
            "limit": 50,
            "offset": 0,
        }
        inform = parse_inform(inform_xml)
        service = InformService(PostgresInformRepository(cwmp_database))
        await service.receive(inform, None, principal=principal_for(inform))
        await service.receive(inform, None, principal=principal_for(inform))
        other = replace(inform, device=replace(inform.device, serial_number="SECOND"))
        await service.receive(other, None, principal=principal_for(other))
        first = (await client.get("/api/v1/cpes?limit=1")).json()
        second = (await client.get("/api/v1/cpes?limit=1&offset=1")).json()
        assert first["total"] == second["total"] == 2
        assert first["items"][0]["serial_number"] == "SECOND"
        assert second["items"][0]["serial_number"] == inform.device.serial_number
        filtered = (
            await client.get(
                "/api/v1/cpes",
                params={
                    "manufacturer": inform.device.manufacturer,
                    "serial_number": inform.device.serial_number,
                },
            )
        ).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["firmware"] == "LAB-1.0"
        assert (await client.get("/api/v1/cpes?manufacturer=missing")).json()[
            "total"
        ] == 0
        beyond = (await client.get("/api/v1/cpes?offset=100")).json()
        assert beyond["total"] == 2 and beyond["items"] == []


@pytest.mark.asyncio
async def test_cpe_detail_and_history_are_scoped_paginated_and_read_only(
    cwmp_database: Database,
    inform_xml: bytes,
) -> None:
    inform = parse_inform(inform_xml)
    service = InformService(PostgresInformRepository(cwmp_database))
    first, cookie = await service.receive(inform, None, principal=principal_for(inform))
    await service.finish(cookie, principal=principal_for(inform))
    second, _ = await service.receive(inform, None, principal=principal_for(inform))
    other_inform = replace(
        inform, device=replace(inform.device, serial_number="OTHER-CPE")
    )
    other, _ = await service.receive(
        other_inform, None, principal=principal_for(other_inform)
    )
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    empty_id = uuid4()
    async with cwmp_database.session() as db:
        # Equal timestamps exercise the deterministic UUID/position tie breakers.
        await db.execute(
            update(CwmpSessionRow).values(
                started_at=timestamp,
                expires_at=timestamp + timedelta(seconds=60),
            )
        )
        await db.execute(update(CwmpEvent).values(command_key="DO-NOT-EXPOSE"))
        db.add(
            Cpe(
                id=empty_id,
                manufacturer="Example",
                oui="A1B2C3",
                product_class="LAB",
                serial_number="EMPTY-HISTORY",
                first_seen=timestamp,
                last_seen=timestamp,
            )
        )
        await db.commit()
    inventory = CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database))
    async with AsyncClient(
        transport=ASGITransport(app=create_admin_app(inventory)),
        base_url="http://test",
    ) as client:
        path = f"/api/v1/cpes/{first.cpe_id}"
        detail = await client.get(path)
        assert detail.status_code == 200
        data = detail.json()
        assert data["serial_number"] == inform.device.serial_number
        assert data["firmware"] == "LAB-1.0" and data["hardware"] is None
        assert datetime.fromisoformat(data["last_seen"]).tzinfo is not None
        sessions = (await client.get(path + "/sessions")).json()
        expected_ids = sorted([str(first.id), str(second.id)])
        assert sessions["total"] == 2
        assert [s["id"] for s in sessions["items"]] == expected_ids
        by_id = {s["id"]: s for s in sessions["items"]}
        assert by_id[str(first.id)]["state"] == "completed"
        assert by_id[str(first.id)]["completed_at"] is not None
        assert by_id[str(second.id)]["state"] == "awaiting_empty"
        assert by_id[str(second.id)]["completed_at"] is None
        for offset, session_id in enumerate(expected_ids):
            page = (
                await client.get(path + f"/sessions?limit=1&offset={offset}")
            ).json()
            assert page["total"] == 2 and page["items"][0]["id"] == session_id
        event_response = await client.get(path + "/events")
        events = event_response.json()
        assert events["total"] == 4
        assert [(e["session_id"], e["position"]) for e in events["items"]] == [
            (session_id, position) for session_id in expected_ids for position in (0, 1)
        ]
        assert [e["code"] for e in events["items"]] == ["0 BOOTSTRAP", "1 BOOT"] * 2
        assert all(
            datetime.fromisoformat(e["received_at"]) == timestamp
            for e in events["items"]
        )
        for offset, event in enumerate(events["items"]):
            page = (await client.get(path + f"/events?limit=1&offset={offset}")).json()
            assert page["total"] == 4 and page["items"] == [event]
        assert "DO-NOT-EXPOSE" not in event_response.text
        for item in sessions["items"]:
            assert "token_hash" not in item and "rpc_id" not in item
        for kind, total in (("sessions", 2), ("events", 4)):
            beyond = (await client.get(path + f"/{kind}?offset=100")).json()
            assert beyond["items"] == [] and beyond["total"] == total
            empty = await client.get(f"/api/v1/cpes/{empty_id}/{kind}")
            assert empty.status_code == 200
            assert empty.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
        for suffix in ("", "/sessions", "/events"):
            missing = await client.get(f"/api/v1/cpes/{uuid4()}{suffix}")
            assert missing.status_code == 404
            assert missing.json()["detail"]["code"] == "cpe_not_found"
        other_sessions = (
            await client.get(f"/api/v1/cpes/{other.cpe_id}/sessions")
        ).json()
        assert other_sessions["total"] == 1
        assert other_sessions["items"][0]["id"] == str(other.id)
    async with cwmp_database.session() as db:
        row = await db.get(CwmpSessionRow, second.id)
        assert (
            row is not None and row.state == "awaiting_empty"
        )  # GET does not expire sessions.
        assert await db.scalar(select(func.count()).select_from(CwmpSessionRow)) == 3
        assert await db.scalar(select(func.count()).select_from(CwmpEvent)) == 6


@pytest.mark.asyncio
async def test_history_database_errors_return_safe_503(cwmp_database: Database) -> None:
    # Only the fixture's disposable schema is modified, never the lab inventory.
    async with cwmp_database.engine.begin() as connection:
        await connection.execute(text("DROP TABLE cpes CASCADE"))
    inventory = CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database))
    async with AsyncClient(
        transport=ASGITransport(app=create_admin_app(inventory)),
        base_url="http://test",
    ) as client:
        for suffix in ("", "/sessions", "/events"):
            response = await client.get(f"/api/v1/cpes/{uuid4()}{suffix}")
            assert response.status_code == 503
            assert response.json()["detail"] == {
                "code": "inventory_unavailable",
                "request_id": response.headers["x-request-id"],
            }
            assert "SELECT" not in response.text


@pytest.mark.asyncio
async def test_upsert_sessions_events_and_repeated_inform(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    inform = parse_inform(inform_xml)
    service = InformService(PostgresInformRepository(cwmp_database))
    first, cookie = await service.receive(inform, None, principal=principal_for(inform))
    duplicate, _ = await service.receive(
        inform, cookie, principal=principal_for(inform)
    )
    second, _ = await service.receive(
        replace(inform, retry_count=1), None, principal=principal_for(inform)
    )
    assert first.id == duplicate.id
    assert first.id != second.id and first.cpe_id == second.cpe_id
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(Cpe)) == 1
        assert await db.scalar(select(func.count()).select_from(CwmpSessionRow)) == 2
        events = (
            await db.scalars(
                select(CwmpEvent)
                .where(CwmpEvent.session_id == first.id)
                .order_by(CwmpEvent.position)
            )
        ).all()
        assert [(e.code, e.command_key) for e in events] == [
            (e.code, e.command_key) for e in inform.events
        ]
        row = await db.get(CwmpSessionRow, first.id)
        assert row is not None
        assert (
            row.version,
            row.rpc_id,
            row.parameter_count,
            row.retry_count,
            row.max_envelopes,
        ) == ("1.0", inform.rpc_id, 3, 0, 1)
        assert row.current_time == inform.current_time
        assert row.token_hash != cookie
        cpe = await db.get(Cpe, first.cpe_id)
        assert cpe is not None and cpe.firmware == "LAB-1.0"
        assert cpe.last_seen >= cpe.first_seen


@pytest.mark.asyncio
async def test_concurrent_upserts_do_not_duplicate_cpe(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    service = InformService(PostgresInformRepository(cwmp_database))
    inform = parse_inform(inform_xml)
    results = await asyncio.gather(
        *(
            service.receive(inform, None, principal=principal_for(inform))
            for _ in range(4)
        )
    )
    assert len({session.cpe_id for session, _ in results}) == 1
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(Cpe)) == 1
        assert await db.scalar(select(func.count()).select_from(CwmpSessionRow)) == 4


@pytest.mark.asyncio
async def test_failed_events_roll_back_entire_inform(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    service = InformService(PostgresInformRepository(cwmp_database))
    invalid = replace(parse_inform(inform_xml), events=(Event("x" * 65, ""),))
    with pytest.raises(StorageUnavailable):
        await service.receive(invalid, None, principal=principal_for(invalid))
    async with cwmp_database.session() as db:
        for model in (Cpe, CwmpSessionRow, CwmpEvent):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_real_http_flow_persists_completion(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    service = InformService(PostgresInformRepository(cwmp_database))
    async with AsyncClient(
        transport=ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/cwmp", content=inform_xml, headers={"Content-Type": "text/xml"}
        )
        assert response.status_code == 200
        assert b"InformResponse" in response.content
        assert (await client.post("/cwmp", content=b"")).status_code == 204
    async with cwmp_database.session() as db:
        session = (await db.scalars(select(CwmpSessionRow))).one()
        assert session.state == "completed" and session.completed_at is not None
        assert await db.scalar(select(func.count()).select_from(CwmpEvent)) == 2


@pytest.mark.asyncio
async def test_simulated_cpe_reuses_identity_across_completed_sessions(
    cwmp_database: Database,
) -> None:
    service = InformService(PostgresInformRepository(cwmp_database))
    async with AsyncClient(
        transport=ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        for _ in range(2):
            client.auth = BasicAuth("simulator", PASSWORD)
            await simulate_session(client, "/cwmp", "SIM-INTEGRATION")
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(Cpe)) == 1
        sessions = (await db.scalars(select(CwmpSessionRow))).all()
        assert len(sessions) == 2
        assert all(s.state == "completed" and s.completed_at for s in sessions)
        assert await db.scalar(select(func.count()).select_from(CwmpEvent)) == 2


@pytest.mark.asyncio
async def test_session_cannot_be_reused_or_hijacked(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    service = InformService(PostgresInformRepository(cwmp_database))
    inform = parse_inform(inform_xml)
    _, cookie = await service.receive(inform, None, principal=principal_for(inform))
    other = replace(inform, device=replace(inform.device, serial_number="OTHER"))
    with pytest.raises(AccessDenied):
        await service.receive(other, cookie, principal=principal_for(inform))
    with pytest.raises(InvalidSession):
        await service.finish("unknown", principal=principal_for(inform))
    await service.finish(cookie, principal=principal_for(inform))
    with pytest.raises(InvalidSession):
        await service.finish(cookie, principal=principal_for(inform))


@pytest.mark.asyncio
async def test_expired_session_cannot_continue(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    repo = PostgresInformRepository(cwmp_database)
    now = datetime.now(UTC)
    await repo.accept(
        parse_inform(inform_xml),
        "expired",
        now - timedelta(minutes=2),
        now - timedelta(seconds=1),
        False,
    )
    with pytest.raises(InvalidSession):
        await repo.complete("expired", now)


@pytest.mark.asyncio
async def test_migration_matches_models_and_can_be_reapplied(
    cwmp_database: Database,
) -> None:
    async with cwmp_database.engine.begin() as connection:
        differences = await connection.run_sync(
            lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata)
        )
        assert differences == []
        await connection.run_sync(lambda c: migrate(c, "down"))
        await connection.run_sync(migrate)
        count = await connection.scalar(select(func.count()).select_from(Cpe))
        assert count == 0
