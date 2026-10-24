"""Explicit local smoke test. Writes synthetic records; does not delete data."""

import os
import ssl

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
    serial = os.environ["ACS_SIMULATOR_SERIAL"]
    username = os.environ["ACS_SIMULATOR_USERNAME"]
    password = os.environ["ACS_SIMULATOR_PASSWORD"]
    context = ssl.create_default_context(cafile=os.getenv("ACS_SIMULATOR_CA_FILE"))
    async with AsyncClient(
        timeout=15,
        trust_env=False,
        follow_redirects=False,
        verify=context,
        auth=(username, password),
    ) as client:
        for _ in range(2):
            await simulate_session(client, "https://127.0.0.1:7547/cwmp", serial)
