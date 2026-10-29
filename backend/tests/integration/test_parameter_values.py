"""Selected GPV through administrative HTTP, CWMP sessions and PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from acs.api.app import create_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.inform import InformService
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.evidence import anonymized_inform
from acs.cwmp.compatibility.profiles import ProfileInventoryNormalizer
from acs.cwmp.compatibility.queries import HuaweiQueryPolicy
from acs.cwmp.http.app import create_app as receiver_app
from acs.cwmp.soap.inform import CWMP, parse_envelope
from acs.domain.cpe.inform import Inform
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import PostgresInformRepository
from acs.infrastructure.database.queries import PostgresQueryRepository
from acs.infrastructure.database.query_models import (
    ParameterValueRow,
    QueryOperationRow,
)
from auth_support import PASSWORD
from cwmp_simulator import validate_response
from integration.test_inventory_provenance import accept, cwmp_database, huawei
from integration.test_read_queries import QueryAuth, pytestmark
from query_support import reply_xml

__all__ = ["cwmp_database", "huawei", "pytestmark"]
NAMES = (
    "InternetGatewayDevice.DeviceInfo.HardwareVersion",
    "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fault", "timeout", "unexpected", "invalid_type"])
async def test_selected_gpv_end_to_end_preserves_values(
    cwmp_database: Database,
    huawei: Inform,
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    now = datetime.now(UTC)
    initial = await accept(cwmp_database, huawei, now)
    auth = QueryAuth(huawei)
    service = QueryService(PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy()))
    app = create_app(
        CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
        auth,
        queries=service,
    )
    receiver = receiver_app(
        InformService(
            PostgresInformRepository(cwmp_database), ProfileInventoryNormalizer()
        ),
        auth,
        service,
    )
    path = f"/api/v1/cpes/{initial.cpe_id}/queries"
    async with (
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
            auth=("admin", PASSWORD),
        ) as admin,
        AsyncClient(
            transport=ASGITransport(app=receiver),
            base_url="https://test",
            auth=("device", PASSWORD),
        ) as cpe,
    ):
        for bad in (
            [],
            list(NAMES) * 2,
            ["InternetGatewayDevice.ManagementServer.Password"],
            ["InternetGatewayDevice.DeviceInfo."],
        ):
            rejected = await admin.post(
                path,
                json={
                    "kind": "get_parameter_values",
                    "idempotency_key": str(uuid4()),
                    "parameter_names": bad,
                },
            )
            assert rejected.status_code in (409, 422)
        last_success = None
        for step in range(3):
            body = {
                "kind": "get_parameter_values",
                "idempotency_key": str(uuid4()),
                "parameter_names": list(NAMES),
            }
            created = await admin.post(path, json=body)
            assert created.status_code == 202 and created.json()["state"] == "pending"
            operation_id = UUID(created.json()["id"])
            repeated = await admin.post(
                path, json={**body, "parameter_names": list(reversed(NAMES))}
            )
            assert repeated.json()["id"] == str(operation_id)
            assert (
                await admin.post(path, json={**body, "parameter_names": [NAMES[0]]})
            ).status_code == 409
            inform = anonymized_inform(huawei, datetime.now(UTC))
            inform_id, _ = parse_envelope(inform)
            response = await cpe.post(
                "/cwmp", content=inform, headers={"Content-Type": "text/xml"}
            )
            validate_response(response.content, inform_id)
            dispatched = await cpe.post("/cwmp", content=b"")
            assert dispatched.status_code == 200
            rpc_id, rpc = parse_envelope(dispatched.content)
            assert rpc.tag == f"{{{CWMP}}}GetParameterValues"
            assert (
                tuple(item.text for item in rpc.findall("ParameterNames/string"))
                == NAMES
            )
            values = (
                ("  SYNTHETIC-HARDWARE  ", "")
                if step == 0
                else ("", " SYNTHETIC-SOFTWARE ")
            )
            payload = reply_xml(
                rpc_id, "get_parameter_values", values=values, names=NAMES
            )
            if step == 2:
                if failure == "fault":
                    payload = reply_xml(rpc_id, "get_parameter_values", fault=9005)
                elif failure == "unexpected":
                    payload = reply_xml(
                        rpc_id,
                        "get_parameter_values",
                        names=(NAMES[0],),
                        values=("different",),
                    )
                elif failure == "invalid_type":
                    payload = reply_xml(
                        rpc_id, "get_parameter_values", names=NAMES, values=("1", "2")
                    ).replace(b'xsi:type="xsd:string"', b'xsi:type="xsd:int"')
                else:
                    async with cwmp_database.session() as db, db.begin():
                        await db.execute(
                            update(QueryOperationRow)
                            .where(QueryOperationRow.id == operation_id)
                            .values(expires_at=now - timedelta(seconds=1))
                        )
            else:
                last_success = operation_id
                # Incorrect correlation must not write values or consume the attempt.
                wrong = reply_xml(
                    "wrong-id", "get_parameter_values", values=values, names=NAMES
                )
                assert (
                    await cpe.post(
                        "/cwmp", content=wrong, headers={"Content-Type": "text/xml"}
                    )
                ).status_code == 400
            finished = await cpe.post(
                "/cwmp", content=payload, headers={"Content-Type": "text/xml"}
            )
            assert finished.status_code == 204 and not finished.content
            observed = (await admin.get(f"{path}/{operation_id}")).json()
            assert observed["state"] == (
                "succeeded"
                if step < 2
                else "expired"
                if failure == "timeout"
                else "failed"
            )
            async with cwmp_database.session() as db:
                rows = (
                    await db.scalars(
                        select(ParameterValueRow).order_by(ParameterValueRow.path)
                    )
                ).all()
                assert len(rows) == 2
                expected = values if step < 2 else ("", " SYNTHETIC-SOFTWARE ")
                assert tuple(row.value for row in rows) == expected
                assert all(
                    row.xml_type == "xsd:string"
                    and row.cpe_id == initial.cpe_id
                    and row.operation_id == last_success
                    and row.observed_at >= now
                    for row in rows
                )
    assert "SYNTHETIC-HARDWARE" not in caplog.text
    assert "SYNTHETIC-SOFTWARE" not in caplog.text
    assert "<soap:" not in caplog.text
