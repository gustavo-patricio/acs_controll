"""Telemetry errors, authorization and closed OpenAPI response."""

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport

from acs.application.services.cpe_inventory import (
    CpeInventoryService,
    CpeNotFound,
    InventoryUnavailable,
)
from acs.domain.cpe.telemetry import CpeTelemetryView
from auth_support import admin_app
from auth_support import authorized_client as AsyncClient
from contract.test_cpes import InventoryStub


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status"), [(CpeNotFound, 404), (InventoryUnavailable, 503)]
)
async def test_telemetry_safe_errors(error: type[Exception], status: int) -> None:
    class Failing(InventoryStub):
        async def get_telemetry(self, cpe_id: UUID) -> CpeTelemetryView:
            raise error("private details")

    async with AsyncClient(
        transport=ASGITransport(app=admin_app(CpeInventoryService(Failing()))),
        base_url="https://test",
    ) as client:
        response = await client.get(f"/api/v1/cpes/{uuid4()}/telemetry")
        assert response.status_code == status
        assert "private details" not in response.text
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_telemetry_empty_and_schema() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=admin_app(CpeInventoryService(InventoryStub()))),
        base_url="https://test",
    ) as client:
        response = await client.get(f"/api/v1/cpes/{uuid4()}/telemetry")
        assert response.status_code == 200
        assert response.json()["wan"] == response.json()["optical"] == []
        schema = (await client.get("/openapi.json")).json()
        route = schema["paths"]["/api/v1/cpes/{cpe_id}/telemetry"]["get"]
        assert set(route["responses"]) == {"200", "401", "403", "404", "422", "503"}
        assert (
            schema["components"]["schemas"]["TelemetryFieldResponse"][
                "additionalProperties"
            ]
            is False
        )
