"""Temporary management routes must disappear when disabled or outside the lab."""

import pytest
from httpx import ASGITransport, AsyncClient

import acs.api.app as api_module
from acs.settings import get_settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "environment", "echo"),
    [
        (False, "development", False),
        (True, "production", False),
        (True, "staging", False),
        (True, "development", True),
    ],
)
async def test_temporary_routes_unavailable_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    environment: str,
    echo: bool,
) -> None:
    settings = get_settings().model_copy(
        update={
            "enable_test_cpe_accounts": enabled,
            "environment": environment,
            "database_echo": echo,
        }
    )
    monkeypatch.setattr(api_module, "get_settings", lambda: settings)
    app = api_module.create_app()
    path = "/api/v1/test-cpe-accounts"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        assert (await client.post(path, json={})).status_code == 404
        assert (
            await client.delete(path + "/00000000-0000-0000-0000-000000000000")
        ).status_code == 404
        assert path not in (await client.get("/openapi.json")).json()["paths"]
