"""HTTP contract tests for API liveness."""

import pytest
from httpx import ASGITransport, AsyncClient

from acs.api.app import app


@pytest.mark.asyncio
async def test_health_endpoint_reports_api_is_healthy() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_health_endpoint_is_in_openapi_contract() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
