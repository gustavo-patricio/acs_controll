"""PostgreSQL connectivity integration tests."""

import os
from typing import cast

import pytest
from pydantic import PostgresDsn
from sqlalchemy import text

from acs.infrastructure.database import Database
from acs.settings import Settings, get_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ACS_RUN_INTEGRATION_TESTS") != "1",
        reason="Set ACS_RUN_INTEGRATION_TESTS=1 to test PostgreSQL",
    ),
]


@pytest.mark.asyncio
async def test_database_accepts_connections() -> None:
    database = Database(get_settings())
    try:
        assert await database.is_healthy()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_database_session_executes_queries() -> None:
    database = Database(get_settings())
    try:
        async with database.session() as session:
            database_name = cast(
                str,
                (await session.execute(text("SELECT current_database()"))).scalar_one(),
            )
            database_user = cast(
                str,
                (await session.execute(text("SELECT current_user"))).scalar_one(),
            )
    finally:
        await database.close()

    assert database_name == "acs"
    assert database_user == "acs"


@pytest.mark.asyncio
async def test_database_healthcheck_reports_unavailable_endpoint() -> None:
    settings = Settings(
        database_url=PostgresDsn(
            "postgresql+psycopg://acs:local@127.0.0.1:1/unavailable"
        ),
        database_connect_timeout=1,
    )
    database = Database(settings)
    try:
        assert not await database.is_healthy()
    finally:
        await database.close()
