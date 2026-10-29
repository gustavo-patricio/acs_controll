"""Authenticated exports read only stored observations and operations."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from defusedxml.ElementTree import fromstring
from httpx import ASGITransport, AsyncClient

from acs.api.app import create_app
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.queries import HuaweiQueryPolicy
from acs.domain.cpe.inform import Inform
from acs.domain.operations.query import ParameterInfo, QueryReply
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.cwmp_models import Cpe
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.queries import PostgresQueryRepository
from acs.infrastructure.database.query_models import ParameterValueRow
from auth_support import PASSWORD
from integration.test_inventory_provenance import accept, cwmp_database, huawei
from integration.test_read_queries import QueryAuth, pytestmark

__all__ = ["cwmp_database", "huawei", "pytestmark"]


@pytest.mark.asyncio
async def test_export_api_is_scoped_and_sanitized(
    cwmp_database: Database, huawei: Inform
) -> None:
    now = datetime.now(UTC)
    session = await accept(cwmp_database, huawei, now, token="export")
    repo = PostgresQueryRepository(cwmp_database, HuaweiQueryPolicy())
    operation = await repo.create(
        session.cpe_id, "get_parameter_names", uuid4(), None, "local", now
    )
    _, command = await repo.exchange("export", None, now)
    assert command is not None
    path = "InternetGatewayDevice.DeviceInfo.HardwareVersion"
    await repo.exchange(
        "export",
        QueryReply(
            command.rpc_id, "get_parameter_names", names=(ParameterInfo(path, False),)
        ),
        now,
    )
    private_serial = "UNIQUE-SERIAL-IDENTIFIER"
    async with cwmp_database.session() as db, db.begin():
        cpe = await db.get(Cpe, session.cpe_id)
        assert cpe is not None
        cpe.serial_number = private_serial
        db.add(
            ParameterValueRow(
                cpe_id=session.cpe_id,
                path=path,
                xml_type="xsd:string",
                value=private_serial,
                observed_at=now,
                operation_id=operation.id,
            )
        )
    app = create_app(
        CpeInventoryService(PostgresCpeInventoryRepository(cwmp_database)),
        QueryAuth(huawei),
        queries=QueryService(repo),
    )
    url = f"/api/v1/cpes/{session.cpe_id}/queries/export"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, auth=("support", PASSWORD))).status_code == 403
        response = await client.get(url, auth=("admin", PASSWORD))
        assert (
            response.status_code == 200
            and response.headers["cache-control"] == "no-store"
        )
        assert "attachment" in response.headers["content-disposition"]
        document = response.json()
        assert document["xml_fidelity"] == "reconstructed" and document["partial"]
        assert document["catalog"][0]["path"] == path
        assert document["values"][0]["value"] == "ANONYMIZED0001"
        assert document["values"][0]["xml_type"] == "xsd:string"
        assert private_serial not in response.text
        xml = await client.get(url, params={"format": "xml"}, auth=("admin", PASSWORD))
        assert xml.status_code == 200 and xml.headers["content-type"].startswith(
            "application/xml"
        )
        assert fromstring(xml.content).attrib["fidelity"] == "reconstructed"
        assert private_serial not in xml.text
        assert (
            await client.get(
                url, params={"prefix": "not-in-catalog"}, auth=("admin", PASSWORD)
            )
        ).json()["catalog"] == []
        assert (
            await client.get(
                url, params={"format": "invalid"}, auth=("admin", PASSWORD)
            )
        ).status_code == 422
        assert (
            await client.get(
                f"/api/v1/cpes/{uuid4()}/queries/export", auth=("admin", PASSWORD)
            )
        ).status_code == 404
        assert (
            url.replace(str(session.cpe_id), "{cpe_id}")
            in (await client.get("/openapi.json")).json()["paths"]
        )
