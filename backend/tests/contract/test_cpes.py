"""Inventory HTTP contract and safe failure responses."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import PostgresDsn

from acs.api import app as app_module
from acs.api.app import create_app
from acs.application.services.cpe_inventory import (
    CpeInventoryService,
    CpePage,
    CpeSummary,
    InventoryUnavailable,
)
from acs.settings import Settings


class InventoryStub:
    async def list_cpes(
        self,
        limit: int,
        offset: int,
        manufacturer: str | None,
        serial_number: str | None,
    ) -> CpePage:
        now = datetime.now(UTC)
        return CpePage(
            (
                CpeSummary(
                    uuid4(),
                    manufacturer or "Example",
                    "A1B2C3",
                    "LAB",
                    serial_number or "SYNTHETIC",
                    None,
                    "1.0",
                    None,
                    now,
                    now,
                ),
            ),
            1,
            limit,
            offset,
        )


@pytest.mark.asyncio
async def test_inventory_response_and_openapi() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(CpeInventoryService(InventoryStub()))),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/cpes?limit=2&offset=1&manufacturer=Example&serial_number=LAB1"
        )
        assert response.status_code == 200
        page = response.json()
        assert (page["total"], page["limit"], page["offset"]) == (1, 2, 1)
        item = page["items"][0]
        assert item["manufacturer"] == "Example" and item["serial_number"] == "LAB1"
        assert set(item) == {
            "id",
            "manufacturer",
            "oui",
            "product_class",
            "serial_number",
            "model",
            "firmware",
            "hardware",
            "first_seen",
            "last_seen",
        }
        assert item["model"] is None
        assert datetime.fromisoformat(item["last_seen"]).tzinfo is not None
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-request-id"]
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/api/v1/cpes"]["get"]
        assert set(operation["responses"]) == {"200", "422", "503"}
        assert {p["name"] for p in operation["parameters"]} == {
            "limit",
            "offset",
            "manufacturer",
            "serial_number",
        }
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "limit=x",
        "manufacturer=",
        "serial_number=" + "x" * 65,
    ],
)
async def test_invalid_inventory_query(query: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(CpeInventoryService(InventoryStub()))),
        base_url="http://test",
    ) as client:
        assert (await client.get(f"/api/v1/cpes?{query}")).status_code == 422


@pytest.mark.asyncio
async def test_inventory_failure_is_safe() -> None:
    class UnavailableInventory(InventoryStub):
        async def list_cpes(
            self,
            limit: int,
            offset: int,
            manufacturer: str | None,
            serial_number: str | None,
        ) -> CpePage:
            raise InventoryUnavailable("sensitive internal data")

    async with AsyncClient(
        transport=ASGITransport(
            app=create_app(CpeInventoryService(UnavailableInventory()))
        ),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/cpes")
        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "inventory_unavailable",
            "request_id": response.headers["x-request-id"],
        }
        assert "sensitive" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "environment,echo",
    [("production", False), ("staging", False), ("development", True)],
)
async def test_inventory_disabled_outside_safe_lab_settings(
    environment: str,
    echo: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings.model_validate(
        {
            "environment": environment,
            "database_echo": echo,
            "database_url": PostgresDsn(
                "postgresql+psycopg://acs:unused@localhost/acs"
            ),
        }
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/api/v1/cpes")).status_code == 503
        assert (await client.get("/health")).status_code == 200
