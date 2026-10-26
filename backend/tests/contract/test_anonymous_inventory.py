"""Local read-only opt-out must not grant credential management access."""

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from acs.api import app as app_module
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.settings import get_settings
from auth_support import StubAuthenticator
from contract.test_cpes import InventoryStub


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_anonymous_inventory_is_read_only_and_documented(
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
) -> None:
    settings = get_settings().model_copy(
        update={
            "environment": "development",
            "api_allow_anonymous_inventory": True,
            "enable_test_cpe_accounts": True,
            "database_echo": False,
        }
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    app = app_module.create_app(
        CpeInventoryService(InventoryStub()), StubAuthenticator()
    )
    identifier = uuid4()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=f"{scheme}://test"
    ) as client:
        schema = (await client.get("/openapi.json")).json()
        for route, template in (
            ("/api/v1/cpes", "/api/v1/cpes"),
            (f"/api/v1/cpes/{identifier}", "/api/v1/cpes/{cpe_id}"),
            (f"/api/v1/cpes/{identifier}/sessions", "/api/v1/cpes/{cpe_id}/sessions"),
            (f"/api/v1/cpes/{identifier}/events", "/api/v1/cpes/{cpe_id}/events"),
            (f"/api/v1/cpes/{identifier}/inventory", "/api/v1/cpes/{cpe_id}/inventory"),
        ):
            response = await client.get(route)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-request-id"]
            assert not schema["paths"][template]["get"].get("security")
            assert "401" not in schema["paths"][template]["get"]["responses"]
        assert (await client.get("/api/v1/cpes?limit=0")).status_code == 422
        account_path = "/api/v1/test-cpe-accounts"
        assert schema["paths"][account_path]["post"]["security"] == [{"AdminBasic": []}]
        for response in (
            await client.post(
                f"/api/v1/cpes/{identifier}/queries",
                json={"kind": "get_parameter_values", "idempotency_key": str(uuid4())},
            ),
            await client.get(f"/api/v1/cpes/{identifier}/queries/{uuid4()}"),
            await client.post(
                account_path,
                json={
                    "username": "test",
                    "password": "not-created-password",
                    "shared": True,
                },
            ),
            await client.delete(f"{account_path}/{uuid4()}"),
        ):
            assert response.status_code == (401 if scheme == "https" else 403)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "echo", "enabled"),
    [
        ("production", False, True),
        ("staging", False, True),
        ("development", True, True),
        ("development", False, False),
    ],
)
async def test_anonymous_flag_cannot_bypass_restricted_settings(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    echo: bool,
    enabled: bool,
) -> None:
    settings = get_settings().model_copy(
        update={
            "environment": environment,
            "database_echo": echo,
            "api_allow_anonymous_inventory": enabled,
        }
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    app = app_module.create_app(
        CpeInventoryService(InventoryStub()), StubAuthenticator()
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (await client.get("/api/v1/cpes")).status_code == 401
        schema = (await client.get("/openapi.json")).json()
        assert schema["paths"]["/api/v1/cpes"]["get"]["security"] == [
            {"AdminBasic": []}
        ]
