"""Catálogo GPN persistente, parcial e consultável por CPE."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from acs.api.app import create_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.queries import HuaweiQueryPolicy
from acs.domain.cpe.inform import Inform
from acs.domain.operations.query import ParameterInfo, QueryReply
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.queries import PostgresQueryRepository
from auth_support import PASSWORD
from integration.test_inventory_provenance import (
    accept,
    cwmp_database,
    huawei,
    migrate_to,
)
from integration.test_read_queries import QueryAuth, pytestmark

__all__ = ["cwmp_database", "huawei", "pytestmark"]
ROOT = "InternetGatewayDevice.DeviceInfo."


@pytest.mark.asyncio
async def test_catalog_upsert_partial_replay_and_api(
    cwmp_database: Database, huawei: Inform
) -> None:
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now, token="first")
    first = await repo.create(
        initial.cpe_id, "get_parameter_names", uuid4(), None, "local", now
    )
    _, command = await repo.exchange("first", None, now)
    assert command is not None
    reply = QueryReply(
        command.rpc_id,
        "get_parameter_names",
        names=(
            ParameterInfo(ROOT + "ModelName", False),
            ParameterInfo(ROOT + "VendorObject.", True),
        ),
    )
    await repo.exchange("first", reply, now + timedelta(seconds=1))
    await repo.exchange("first", reply, now + timedelta(seconds=2))
    rows = await repo.catalog(initial.cpe_id, ROOT, 100, 0)
    assert len(rows) == 2
    assert rows[0].observed_at == now + timedelta(seconds=1)
    assert rows[0].operation_id == first.id and not rows[0].is_object
    assert rows[1].is_object and rows[1].writable
    later = now + timedelta(seconds=3)
    await accept(cwmp_database, huawei, later, token="second")
    second = await repo.create(
        initial.cpe_id, "get_parameter_names", uuid4(), None, "local", later
    )
    _, command = await repo.exchange("second", None, later)
    assert command is not None
    await repo.exchange(
        "second",
        QueryReply(
            command.rpc_id,
            "get_parameter_names",
            names=(ParameterInfo(ROOT + "ModelName", True),),
        ),
        later,
    )
    rows = await repo.catalog(initial.cpe_id, "", 100, 0)
    assert len(rows) == 2
    assert rows[0].writable and rows[0].operation_id == second.id
    assert rows[0].observed_at == later
    assert rows[1].operation_id == first.id  # absent in partial response: retained
    app = create_app(
        CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
        QueryAuth(huawei),
        queries=QueryService(repo),
    )
    path = f"/api/v1/cpes/{initial.cpe_id}/queries/catalog"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (await client.get(path)).status_code == 401
        client.auth = ("support", PASSWORD)
        page = await client.get(path, params={"prefix": ROOT, "limit": 1})
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        data = page.json()
        assert data["has_more"] and len(data["items"]) == 1
        assert set(data["items"][0]) == {
            "cpe_id",
            "path",
            "is_object",
            "writable",
            "observed_at",
            "operation_id",
        }
        last = (await client.get(path, params={"limit": 1, "offset": 1})).json()
        assert not last["has_more"] and last["items"][0]["is_object"]
        assert (await client.get(path, params={"prefix": ROOT + "Model"})).json()[
            "items"
        ][0]["writable"]
        assert (await client.get(path, params={"prefix": "%"})).json()["items"] == []
        assert (await client.get(path, params={"prefix": "_"})).json()["items"] == []
        assert (await client.get(path, params={"limit": 101})).status_code == 422
        assert (await client.get(path, params={"offset": -1})).status_code == 422
        assert (
            await client.get(f"/api/v1/cpes/{uuid4()}/queries/catalog")
        ).status_code == 404
    # Rebuild from historical projected results; latest observation wins.
    async with cwmp_database.engine.begin() as connection:
        await connection.run_sync(
            lambda c: migrate_to(c, "0008_query_evidence", downgrade=True)
        )
        await connection.run_sync(lambda c: migrate_to(c, "head"))
    restored = await repo.catalog(initial.cpe_id, "", 100, 0)
    assert len(restored) == 1  # old operation results filtered VendorObject
    assert restored[0].operation_id == second.id and restored[0].writable


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["fault", "timeout", "unexpected", "empty"])
async def test_catalog_ignores_unsuccessful_or_empty_responses(
    cwmp_database: Database, huawei: Inform, outcome: str
) -> None:
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now, token="session")
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    await repo.create(
        initial.cpe_id, "get_parameter_names", uuid4(), None, "local", now
    )
    _, command = await repo.exchange("session", None, now)
    assert command is not None
    reply = QueryReply(
        command.rpc_id,
        "get_parameter_names",
        names=(ParameterInfo(ROOT + "ModelName", False),),
    )
    if outcome == "fault":
        reply = QueryReply(command.rpc_id, "fault", fault_code=9005)
    elif outcome == "unexpected":
        reply = QueryReply(
            command.rpc_id,
            "get_parameter_names",
            names=(ParameterInfo("Device.Other", False),),
        )
    elif outcome == "empty":
        reply = QueryReply(command.rpc_id, "get_parameter_names")
    await repo.exchange(
        "session", reply, now + timedelta(seconds=61 if outcome == "timeout" else 1)
    )
    assert await repo.catalog(initial.cpe_id, "", 100, 0) == ()
