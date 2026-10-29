"""Persistent bounded traversal, partial progress and explicitly selected batches."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from acs.api.app import create_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.queries import NAMES, HuaweiQueryPolicy
from acs.domain.cpe.inform import Inform, Parameter
from acs.domain.operations.collection import CollectionSpec
from acs.domain.operations.query import ParameterInfo, QueryConflict, QueryReply
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.queries import PostgresQueryRepository
from auth_support import PASSWORD
from integration.test_inventory_provenance import accept, cwmp_database, huawei
from integration.test_read_queries import QueryAuth, pytestmark

__all__ = ["cwmp_database", "huawei", "pytestmark"]
ROOT = "InternetGatewayDevice.WANDevice."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit", ["none", "depth", "rpcs", "entries", "fault", "placeholder", "timeout"]
)
async def test_bounded_navigation(
    cwmp_database: Database, huawei: Inform, limit: str
) -> None:
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now)
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    spec = CollectionSpec(
        "discovery",
        ROOT,
        max_depth=0 if limit == "depth" else 3,
        max_rpcs=1 if limit == "rpcs" else 10,
        max_entries=1 if limit == "entries" else 100,
    )
    key = uuid4()
    job = await repo.create_collection(initial.cpe_id, spec, key, uuid4(), now)
    assert (
        await repo.create_collection(initial.cpe_id, spec, key, uuid4(), now)
    ).id == job.id
    with pytest.raises(QueryConflict):
        await repo.create(
            initial.cpe_id, "get_parameter_names", uuid4(), None, "local", now
        )
    paths: list[str] = []
    for index in range(4):
        # New repository instance simulates restart; progress lives in PostgreSQL.
        repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
        at = now + timedelta(seconds=index * 65)
        job = await repo.get_collection(initial.cpe_id, job.id, at)
        if job.state != "running":
            break
        token = f"step-{index}"
        await accept(cwmp_database, huawei, at, token=token)
        _, command = await repo.exchange(token, None, at)
        assert (
            command is not None
            and command.kind == "get_parameter_names"
            and command.plan.next_level
        )
        path = command.plan.parameter_path
        assert "{" not in path
        paths.append(path)
        if path == ROOT:
            names = (
                ParameterInfo(ROOT + "1.", False),
                ParameterInfo(ROOT + "2.", False),
            )
        else:
            names = (ParameterInfo(path + "Enable", True),)
        if index == 1 and limit == "fault":
            reply = QueryReply(command.rpc_id, "fault", fault_code=9005)
        elif index == 1 and limit == "placeholder":
            reply = QueryReply(
                command.rpc_id,
                "get_parameter_names",
                names=(ParameterInfo(path + "{i}.", False),),
            )
        else:
            reply = QueryReply(command.rpc_id, "get_parameter_names", names=names)
        at += timedelta(seconds=61 if index == 1 and limit == "timeout" else 1)
        session, following = await repo.exchange(token, reply, at)
        assert session.state == "completed" and following is None
        # Duplicate replies do not advance the job twice.
        await repo.exchange(token, reply, at)
    job = await repo.get_collection(initial.cpe_id, job.id, now + timedelta(minutes=5))
    assert job.state == ("succeeded" if limit == "none" else "partial")
    assert job.rpc_count <= spec.max_rpcs and job.entry_count <= spec.max_entries
    assert job.completed_count == job.rpc_count
    if limit in ("none", "fault", "placeholder", "timeout"):
        assert paths == [ROOT, ROOT + "1.", ROOT + "2."]
    else:
        assert paths == [ROOT]
    if limit in ("fault", "placeholder", "timeout"):
        assert len(job.failures) == 1
        assert (
            job.failures[0]["reason"]
            == {
                "fault": "cpe_fault",
                "placeholder": "unexpected_parameters",
                "timeout": "deadline_exceeded",
            }[limit]
        )
    catalog = await repo.catalog(initial.cpe_id, ROOT, 100, 0)
    assert len(catalog) == job.entry_count
    assert all("{" not in p.path for p in catalog)
    # A subsequent incomplete discovery must not erase the previous catalog.
    before = {p.path for p in catalog}
    at = now + timedelta(minutes=6)
    second = await repo.create_collection(
        initial.cpe_id,
        CollectionSpec("discovery", ROOT, max_depth=0),
        uuid4(),
        uuid4(),
        at,
    )
    await accept(cwmp_database, huawei, at, token="empty")
    _, command = await repo.exchange("empty", None, at)
    assert command is not None
    await repo.exchange("empty", QueryReply(command.rpc_id, "get_parameter_names"), at)
    assert (
        await repo.get_collection(initial.cpe_id, second.id, at)
    ).state == "succeeded"
    assert {p.path for p in await repo.catalog(initial.cpe_id, ROOT, 100, 0)} == before


@pytest.mark.asyncio
async def test_selected_catalog_batches_and_http(
    cwmp_database: Database, huawei: Inform
) -> None:
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now, token="catalog")
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    await repo.create(
        initial.cpe_id, "get_parameter_names", uuid4(), None, "local", now
    )
    _, command = await repo.exchange("catalog", None, now)
    assert command is not None
    await repo.exchange(
        "catalog",
        QueryReply(
            command.rpc_id,
            "get_parameter_names",
            names=tuple(ParameterInfo(n, False) for n in NAMES),
        ),
        now,
    )
    auth = QueryAuth(huawei)
    app = create_app(
        CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
        auth,
        queries=QueryService(repo),
    )
    path = f"/api/v1/cpes/{initial.cpe_id}/queries/collections"
    body = {
        "kind": "values",
        "parameter_names": list(NAMES),
        "batch_size": 2,
        "idempotency_key": str(uuid4()),
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (await client.post(path, json=body)).status_code == 401
        assert (
            await client.post(path, json=body, auth=("support", PASSWORD))
        ).status_code == 403
        client.auth = ("admin", PASSWORD)
        for changes in (
            {"batch_size": 17},
            {"parameter_names": ["InternetGatewayDevice.DeviceInfo."]},
            {
                "kind": "discovery",
                "parameter_names": list[str](),
                "root": ROOT + "{i}.",
            },
        ):
            assert (await client.post(path, json={**body, **changes})).status_code in (
                409,
                422,
            )
        created = await client.post(path, json=body)
        assert created.status_code == 202
        job_id = created.json()["id"]
        assert (await client.get(f"{path}/{uuid4()}")).status_code == 404
        batches: list[tuple[str, ...]] = []
        for index in range(2):
            at = now + timedelta(seconds=index + 1)
            await accept(cwmp_database, huawei, at, token=f"batch-{index}")
            _, command = await repo.exchange(f"batch-{index}", None, at)
            assert command is not None and command.kind == "get_parameter_values"
            batches.append(command.plan.names)
            reply = (
                QueryReply(command.rpc_id, "fault", fault_code=9005)
                if index == 0
                else QueryReply(
                    command.rpc_id,
                    "get_parameter_values",
                    parameters=tuple(
                        Parameter(n, "", "xsd:string") for n in command.plan.names
                    ),
                )
            )
            await repo.exchange(f"batch-{index}", reply, at)
        assert [len(batch) for batch in batches] == [2, 1]
        assert {n for batch in batches for n in batch} == set(NAMES)
        result = await client.get(f"{path}/{job_id}", auth=("support", PASSWORD))
        assert result.status_code == 200 and result.json()["state"] == "partial"
        assert (
            result.json()["completed_count"] == 2
            and len(result.json()["failures"]) == 1
        )
        assert result.json()["entry_count"] == 1
        assert result.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "batch_size,max_rpcs,max_entries", [(1, 2, 100), (3, 10, 2), (2, 10, 100)]
)
async def test_value_batch_limits(
    cwmp_database: Database,
    huawei: Inform,
    batch_size: int,
    max_rpcs: int,
    max_entries: int,
) -> None:
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now, token="seed")
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    with pytest.raises(QueryConflict, match="selection_requires_catalog_leaves"):
        await repo.create_collection(
            initial.cpe_id,
            CollectionSpec("values", parameter_names=NAMES),
            uuid4(),
            uuid4(),
            now,
        )
    await repo.create(
        initial.cpe_id, "get_parameter_names", uuid4(), None, "local", now
    )
    _, command = await repo.exchange("seed", None, now)
    assert command is not None
    await repo.exchange(
        "seed",
        QueryReply(
            command.rpc_id,
            "get_parameter_names",
            names=tuple(ParameterInfo(n, False) for n in NAMES),
        ),
        now,
    )
    job = await repo.create_collection(
        initial.cpe_id,
        CollectionSpec(
            "values",
            parameter_names=NAMES,
            batch_size=batch_size,
            max_rpcs=max_rpcs,
            max_entries=max_entries,
        ),
        uuid4(),
        uuid4(),
        now,
    )
    sent: list[str] = []
    for index in range(4):
        at = now + timedelta(seconds=index + 1)
        job = await repo.get_collection(initial.cpe_id, job.id, at)
        if job.state != "running":
            break
        await accept(cwmp_database, huawei, at, token=f"values-{index}")
        _, command = await repo.exchange(f"values-{index}", None, at)
        assert command is not None and len(command.plan.names) <= batch_size
        sent.extend(command.plan.names)
        await repo.exchange(
            f"values-{index}",
            QueryReply(
                command.rpc_id,
                "get_parameter_values",
                parameters=tuple(
                    Parameter(n, "", "xsd:string") for n in command.plan.names
                ),
            ),
            at,
        )
    assert len(sent) == len(set(sent)) == job.entry_count
    assert job.rpc_count <= max_rpcs and job.entry_count <= max_entries
    assert job.state == (
        "succeeded" if max_entries == 100 and max_rpcs == 10 else "partial"
    )
