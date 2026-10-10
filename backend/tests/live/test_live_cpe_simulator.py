"""Explicit local smoke test. Writes synthetic records; does not delete data."""

import os
from uuid import uuid4

import pytest
from httpx import AsyncClient

from cwmp_simulator import simulate_session

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("ACS_RUN_LIVE_TESTS") != "1",
        reason="Set ACS_RUN_LIVE_TESTS=1 with the local CWMP receiver running",
    ),
]


@pytest.mark.asyncio
async def test_two_sessions_against_local_receiver() -> None:
    serial = f"SIM-{uuid4().hex}"
    async with AsyncClient(
        timeout=15, trust_env=False, follow_redirects=False
    ) as client:
        for _ in range(2):
            await simulate_session(client, "http://127.0.0.1:7547/cwmp", serial)
