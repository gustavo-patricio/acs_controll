"""PostgreSQL discovery, audited collection, replay and canonical telemetry."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from acs.api.app import create_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.queries import HuaweiQueryPolicy
from acs.cwmp.compatibility.query_evidence import verify_query_evidence
from acs.cwmp.soap.inform import parse_inform
from acs.cwmp.soap.queries import parse_query_reply
from acs.domain.events.session import InvalidSession
from acs.domain.operations.query import QueryConflict, QueryOperation, QueryScope
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.queries import PostgresQueryRepository
from acs.infrastructure.database.telemetry_models import CpeTelemetryField
from auth_support import PASSWORD
from integration.test_cwmp_persistence import cwmp_database
from integration.test_inventory_provenance import accept
from integration.test_read_queries import QueryAuth
from query_support import reply_xml
from unit.test_huawei_telemetry import OPTICAL, WAN

__all__ = ["cwmp_database"]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ACS_RUN_INTEGRATION_TESTS") != "1",
        reason="PostgreSQL integration is opt-in",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_name",
    ["huawei_eg8145v5_v2_v5r021c00s210.xml", "huawei_eg8145x6_10_v5r022c00s364.xml"],
)
async def test_discovery_collection_api_and_last_valid(
    cwmp_database: Database, fixture_name: str
) -> None:
    inform = parse_inform(
        (Path(__file__).parents[1] / "fixtures/cwmp" / fixture_name).read_bytes()
    )
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, inform, now)
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    inventory = PostgresCpeInventoryRepository(cwmp_database)
    suffixes = (
        "ExternalIPAddress",
        "MACAddress",
        "DNSServers",
        "ConnectionStatus",
        "TransportType",
        "X_HW_SERVICELIST",
    )
    names = tuple(WAN + s for s in suffixes)
    values = (
        "192.0.2.10",
        "02:aa:bb:cc:dd:ee",
        "192.0.2.53,2001:db8::53",
        "Connected",
        "PPPoE",
        "TR069_INTERNET" if "x6_10" in fixture_name else "INTERNET",
    )

    async def exchange(
        scope: QueryScope,
        path: str,
        response_names: tuple[str, ...],
        response_values: tuple[str, ...] = (),
    ) -> QueryOperation:
        nonlocal now
        now += timedelta(seconds=3)
        token = uuid4().hex
        await accept(cwmp_database, inform, now, token=token)
        kind = "get_parameter_names" if scope == "discovery" else "get_parameter_values"
        key = uuid4()
        operation = await repo.create(
            initial.cpe_id,
            kind,
            key,
            None,
            "local",
            now,
            scope=scope,
            parameter_path=path,
            capture_evidence=True,
        )
        assert (
            await repo.create(
                initial.cpe_id,
                kind,
                key,
                None,
                "local",
                now,
                scope=scope,
                parameter_path=path,
                capture_evidence=True,
            )
        ).id == operation.id
        _, command = await repo.exchange(token, None, now)
        assert command is not None
        reply = parse_query_reply(
            reply_xml(command.rpc_id, kind, response_values, names=response_names)
        )
        session, followup = await repo.exchange(
            token, reply, now + timedelta(seconds=1)
        )
        assert session.state == "completed" and followup is None
        await repo.exchange(token, reply, now + timedelta(seconds=2))
        if reply.parameters and response_values[-1] != "TR069":
            with pytest.raises(InvalidSession):
                await repo.exchange(
                    token,
                    replace(
                        reply,
                        parameters=(
                            replace(reply.parameters[0], value="192.0.2.99"),
                            *reply.parameters[1:],
                        ),
                    ),
                    now + timedelta(seconds=2),
                )
        return await repo.get(initial.cpe_id, operation.id, now + timedelta(seconds=2))

    with pytest.raises(QueryConflict, match="discovery_required"):
        await repo.create(
            initial.cpe_id,
            "get_parameter_values",
            uuid4(),
            None,
            "local",
            now,
            scope="wan",
            parameter_path=WAN,
        )
    await exchange("discovery", WAN, names + (WAN + "Password",))
    first = await exchange("wan", WAN, names, values)
    assert first.state == "succeeded"
    view = await inventory.get_telemetry(initial.cpe_id)
    field = view.wan[0].fields.ipv4
    assert field.value == ("192.0.2.10",) and field.provenance is not None
    assert field.provenance.operation_id == first.id
    assert field.provenance.source_parameters == (names[0],)
    assert view.wan[0].fields.ipv6.state == "not_collected"

    invalid = await exchange(
        "wan", WAN, names, ("sensitive-invalid-value", *values[1:])
    )
    assert invalid.state == "failed" and invalid.failure_reason == "invalid_values"
    assert "sensitive-invalid-value" not in str(invalid.result)
    field = (await inventory.get_telemetry(initial.cpe_id)).wan[0].fields.ipv4
    assert field.value == ("192.0.2.10",) and field.state == "invalid"
    assert field.provenance is not None and field.provenance.operation_id == first.id
    assert field.operation_id == invalid.id
    empty = await exchange("wan", WAN, names, ("0.0.0.0", *values[1:]))
    assert empty.state == "succeeded"
    field = (await inventory.get_telemetry(initial.cpe_id)).wan[0].fields.ipv4
    assert field.state == "unavailable" and field.value == ("192.0.2.10",)
    other = await exchange("wan", WAN, names, (*values[:-1], "TR069"))
    assert other.failure_reason == "not_internet_pppoe" and not other.result
    assert (await inventory.get_telemetry(initial.cpe_id)).wan[
        0
    ].fields.service_list.operation_id == empty.id

    optical_names = (OPTICAL + "RXPower", OPTICAL + "TXPower")
    await exchange("discovery", OPTICAL, optical_names)
    optical = await exchange("optical", OPTICAL, optical_names, ("-2050", "210"))
    assert optical.failure_reason == "unverified_units"
    assert optical.evidence_available
    evidence = verify_query_evidence(
        (await repo.get_evidence(initial.cpe_id, optical.id)).encode()
    )
    assert evidence.manifest.optical_unit == "unverified"
    wan_evidence = await repo.get_evidence(initial.cpe_id, first.id)
    assert "02:aa:bb:cc:dd:ee" not in wan_evidence and "192.0.2.10" not in wan_evidence
    verify_query_evidence(wan_evidence.encode())
    optical_field = (
        (await inventory.get_telemetry(initial.cpe_id)).optical[0].fields.rx_power
    )
    assert (
        optical_field.value is None
        and optical_field.unit is None
        and optical_field.state == "unverified"
    )
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(CpeTelemetryField)) == 8

    app = create_app(CpeInventoryService(inventory), QueryAuth(inform))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        path = f"/api/v1/cpes/{initial.cpe_id}/telemetry"
        assert (await client.get(path)).status_code == 401
        response = await client.get(path, auth=("admin", PASSWORD))
        assert response.status_code == 200
        assert response.json()["wan"][0]["fields"]["dns"]["value"] == [
            "192.0.2.53",
            "2001:db8::53",
        ]
        assert response.headers["cache-control"] == "no-store"
        schema = (await client.get("/openapi.json")).json()
        assert "/api/v1/cpes/{cpe_id}/telemetry" in schema["paths"]
        assert (
            await client.get(
                f"/api/v1/cpes/{uuid4()}/telemetry", auth=("admin", PASSWORD)
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_explicit_optical_leaves_require_recent_discovery_and_preserve_evidence(
    cwmp_database: Database,
) -> None:
    inform = parse_inform(
        (
            Path(__file__).parents[1]
            / "fixtures/cwmp/huawei_eg8145x6_10_v5r022c00s364.xml"
        ).read_bytes()
    )
    now = datetime.now(UTC)
    cpe = await accept(cwmp_database, inform, now, token="optical-discovery")
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    leaf = OPTICAL + "RXPower"
    with pytest.raises(QueryConflict, match="discovery_required"):
        await repo.create(
            cpe.cpe_id,
            "get_parameter_values",
            uuid4(),
            None,
            "local",
            now,
            scope="optical",
            parameter_path=OPTICAL,
            parameter_names=(leaf,),
        )
    await repo.create(
        cpe.cpe_id,
        "get_parameter_names",
        uuid4(),
        None,
        "local",
        now,
        scope="discovery",
        parameter_path=OPTICAL,
    )
    _, command = await repo.exchange("optical-discovery", None, now)
    assert command is not None
    await repo.exchange(
        "optical-discovery",
        parse_query_reply(
            reply_xml(
                command.rpc_id, "get_parameter_names", names=(leaf, OPTICAL + "TXPower")
            )
        ),
        now + timedelta(seconds=1),
    )
    await accept(
        cwmp_database, inform, now + timedelta(seconds=2), token="optical-read"
    )
    app = create_app(
        CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
        QueryAuth(inform),
        queries=QueryService(repo),
    )
    path = f"/api/v1/cpes/{cpe.cpe_id}/queries"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        auth=("admin", PASSWORD),
    ) as client:
        payload = {
            "kind": "get_parameter_values",
            "scope": "optical",
            "parameter_path": OPTICAL,
            "parameter_names": [leaf],
            "capture_evidence": True,
            "idempotency_key": str(uuid4()),
        }
        assert (
            await client.post(
                path, json={**payload, "parameter_names": [OPTICAL + "NotReviewed"]}
            )
        ).status_code == 409
        created = await client.post(path, json=payload)
        assert created.status_code == 202
        _, command = await repo.exchange(
            "optical-read", None, now + timedelta(seconds=2)
        )
        assert command is not None and command.plan.names == (leaf,)
        await repo.exchange(
            "optical-read",
            parse_query_reply(
                reply_xml(
                    command.rpc_id,
                    "get_parameter_values",
                    names=(leaf,),
                    values=("-2114",),
                )
            ),
            now + timedelta(seconds=3),
        )
        result = (await client.get(path + "/" + created.json()["id"])).json()
        assert (
            result["state"] == "failed"
            and result["failure_reason"] == "unverified_units"
        )
        assert result["evidence_available"]
        evidence = await client.get(path + "/" + created.json()["id"] + "/evidence")
        assert evidence.status_code == 200
        verified = verify_query_evidence(evidence.content)
        assert verified.manifest.optical_unit == "unverified"
        assert leaf in verified.response.xml


@pytest.mark.asyncio
async def test_explicit_wan_selection_keeps_internet_classification(
    cwmp_database: Database,
) -> None:
    inform = parse_inform(
        (
            Path(__file__).parents[1]
            / "fixtures/cwmp/huawei_eg8145x6_10_v5r022c00s364.xml"
        ).read_bytes()
    )
    now = datetime.now(UTC)
    cpe = await accept(cwmp_database, inform, now, token="wan-discovery")
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    names = tuple(
        WAN + suffix
        for suffix in ("TransportType", "X_HW_SERVICELIST", "ExternalIPAddress")
    )
    await repo.create(
        cpe.cpe_id,
        "get_parameter_names",
        uuid4(),
        None,
        "local",
        now,
        scope="discovery",
        parameter_path=WAN,
    )
    _, command = await repo.exchange("wan-discovery", None, now)
    assert command is not None
    await repo.exchange(
        "wan-discovery",
        parse_query_reply(
            reply_xml(command.rpc_id, "get_parameter_names", names=names)
        ),
        now + timedelta(seconds=1),
    )
    with pytest.raises(QueryConflict, match="internet_classification_required"):
        await repo.create(
            cpe.cpe_id,
            "get_parameter_values",
            uuid4(),
            None,
            "local",
            now + timedelta(seconds=2),
            scope="wan",
            parameter_path=WAN,
            parameter_names=(names[2],),
        )
    with pytest.raises(QueryConflict, match="discovery_required"):
        await repo.create(
            cpe.cpe_id,
            "get_parameter_values",
            uuid4(),
            None,
            "local",
            now + timedelta(seconds=2),
            scope="wan",
            parameter_path=WAN,
            parameter_names=(*names[:2], WAN + "DefaultGateway"),
        )
    selected = await repo.create(
        cpe.cpe_id,
        "get_parameter_values",
        uuid4(),
        None,
        "local",
        now + timedelta(seconds=2),
        scope="wan",
        parameter_path=WAN,
        parameter_names=names,
        capture_evidence=True,
    )
    assert selected.state == "pending"
    await accept(cwmp_database, inform, now + timedelta(seconds=3), token="wan-read")
    _, command = await repo.exchange("wan-read", None, now + timedelta(seconds=3))
    assert command is not None and set(command.plan.names) == set(names)

    await repo.exchange(
        "wan-read",
        parse_query_reply(
            reply_xml(
                command.rpc_id,
                "get_parameter_values",
                ("PPPoE", "PRIVATE-UNKNOWN", "192.0.2.42"),
                names=names,
            )
        ),
        now + timedelta(seconds=4),
    )
    result = await repo.get(cpe.cpe_id, selected.id, now + timedelta(seconds=5))
    assert result.failure_reason == "not_internet_pppoe"
    app = create_app(
        CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
        QueryAuth(inform),
        queries=QueryService(repo),
    )
    url = f"/api/v1/cpes/{cpe.cpe_id}/queries/{selected.id}/private-observations"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, auth=("support", PASSWORD))).status_code == 403
        response = await client.get(url, auth=("admin", PASSWORD))
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert any(item["value"] == "192.0.2.42" for item in response.json())
        assert any(item["value"] == "PRIVATE-UNKNOWN" for item in response.json())
        evidence = await client.get(
            url.replace("private-observations", "evidence"), auth=("admin", PASSWORD)
        )
        assert "192.0.2.42" not in evidence.text
        assert "PRIVATE-UNKNOWN" not in evidence.text
