"""Public inventory contract, access policy and closed extension schema."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport
from httpx import AsyncClient as HttpClient
from pydantic import ValidationError

from acs.api.app import create_app
from acs.api.schemas.inventory import CpeInventoryResponse
from acs.application.services.cpe_inventory import (
    CpeInventoryService,
    CpeNotFound,
    InventoryUnavailable,
)
from acs.domain.cpe.inventory_view import (
    CpeInventoryView,
    InventoryFields,
    InventoryFieldView,
    LastObservation,
    ValueProvenance,
)
from acs.domain.security import AccessDenied
from auth_support import ADMIN, PASSWORD, StubAuthenticator, authorized_client
from contract.test_cpes import InventoryStub


class ObservedInventory(InventoryStub):
    async def get_inventory(self, cpe_id: UUID) -> CpeInventoryView:
        at = datetime(2026, 10, 25, tzinfo=UTC)
        provenance = ValueProvenance(
            "Device.DeviceInfo.SoftwareVersion", "generic-device-info", "1", at
        )
        observation = LastObservation(
            "invalid", None, "generic-device-info", "2", at, "conflicting_values"
        )
        return CpeInventoryView(
            cpe_id,
            at,
            InventoryFields(
                InventoryFieldView(None),
                InventoryFieldView("LAST-VALID", provenance, observation),
                InventoryFieldView("LEGACY"),
            ),
        )


@pytest.mark.asyncio
async def test_inventory_view_contract_and_swagger() -> None:
    cpe_id = uuid4()
    app = create_app(CpeInventoryService(ObservedInventory()), StubAuthenticator())
    async with authorized_client(transport=ASGITransport(app=app)) as client:
        response = await client.get(f"/api/v1/cpes/{cpe_id}/inventory")
        assert response.status_code == 200
        data = response.json()
        assert set(data) == {
            "schema_version",
            "cpe_id",
            "last_seen",
            "fields",
            "capabilities",
            "extensions",
            "remote_queries_available",
        }
        assert data["schema_version"] == "1" and data["cpe_id"] == str(cpe_id)
        assert set(data["fields"]) == {"model", "firmware", "hardware"}
        assert data["fields"]["firmware"]["value"] == "LAST-VALID"
        assert data["fields"]["firmware"]["value_status"] == "available"
        assert data["fields"]["firmware"]["last_observation"]["state"] == "invalid"
        assert data["fields"]["firmware"]["provenance"]["profile_version"] == "1"
        assert data["fields"]["firmware"]["last_observation"]["profile_version"] == "2"
        assert data["fields"]["model"] == {
            "value": None,
            "value_status": "missing",
            "provenance": None,
            "last_observation": None,
        }
        assert data["fields"]["hardware"]["value"] == "LEGACY"
        assert data["fields"]["hardware"]["provenance"] is None
        assert set(data["capabilities"]) == {"wifi", "wan", "optical", "voice"}
        assert all(
            c
            == {
                "device_support": "unknown",
                "acs_implemented": False,
                "profile_validated": False,
            }
            for c in data["capabilities"].values()
        )
        assert data["extensions"] == [] and data["remote_queries_available"] is True
        assert (
            response.headers["cache-control"] == "no-store"
            and response.headers["x-request-id"]
        )
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/api/v1/cpes/{cpe_id}/inventory"]["get"]
        assert set(operation["responses"]) == {"200", "401", "403", "404", "422", "503"}
        assert operation["security"] == [{"AdminBasic": []}]
        contract = schema["components"]["schemas"]["CpeInventoryResponse"]
        assert contract["properties"]["extensions"]["maxItems"] == 0
        assert contract["additionalProperties"] is False
        assert (await client.get("/docs")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_inventory_view_has_safe_not_found_and_storage_errors(
    missing: bool,
) -> None:
    class Failing(InventoryStub):
        async def get_inventory(self, cpe_id: UUID) -> CpeInventoryView:
            raise CpeNotFound("PRIVATE") if missing else InventoryUnavailable("PRIVATE")

    app = create_app(CpeInventoryService(Failing()), StubAuthenticator())
    async with authorized_client(transport=ASGITransport(app=app)) as client:
        response = await client.get(f"/api/v1/cpes/{uuid4()}/inventory")
    assert response.status_code == (404 if missing else 503)
    assert "PRIVATE" not in response.text
    assert response.json()["detail"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.asyncio
async def test_inventory_view_authentication_and_validation() -> None:
    app = create_app(CpeInventoryService(InventoryStub()), StubAuthenticator())
    async with HttpClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        path = f"/api/v1/cpes/{uuid4()}/inventory"
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, auth=("test", "wrong"))).status_code == 401
        assert (
            await client.get("http://test" + path, auth=("test", PASSWORD))
        ).status_code == 403
        assert (
            await client.get("/api/v1/cpes/invalid/inventory", auth=("test", PASSWORD))
        ).status_code == 422
    service = CpeInventoryService(InventoryStub())
    with pytest.raises(AccessDenied):
        await service.get_inventory(uuid4())


@pytest.mark.asyncio
async def test_unknown_device_features_and_extensions_are_not_invented() -> None:
    view = await CpeInventoryService(ObservedInventory(), ADMIN).get_inventory(uuid4())
    encoded = CpeInventoryResponse.model_validate(view).model_dump()
    encoded["extensions"] = [{"name": "password", "value": "SECRET"}]
    with pytest.raises(ValidationError):
        CpeInventoryResponse.model_validate(encoded)
    encoded["extensions"] = []
    encoded["raw_parameters"] = {"Password": "SECRET"}
    with pytest.raises(ValidationError):
        CpeInventoryResponse.model_validate(encoded)
