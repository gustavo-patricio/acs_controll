"""Real PostgreSQL tests in disposable schemas migrated by Alembic."""

import asyncio
import os
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from httpx import ASGITransport, AsyncClient
from pydantic import PostgresDsn
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection, make_url

from acs.application.services.inform import InformService
from acs.cwmp.http.app import create_app
from acs.cwmp.soap.inform import parse_inform
from acs.domain.cpe.inform import Event
from acs.domain.events.session import InvalidSession, StorageUnavailable
from acs.infrastructure.database.base import Base
from acs.infrastructure.database.cwmp_models import Cpe, CwmpEvent, CwmpSessionRow
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import PostgresInformRepository
from acs.settings import get_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ACS_RUN_INTEGRATION_TESTS") != "1",
        reason="PostgreSQL integration is opt-in",
    ),
]


def migrate(connection: Connection, direction: str = "up") -> None:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.attributes["connection"] = connection
    if direction == "up":
        command.upgrade(config, "head")
    else:
        command.downgrade(config, "base")


@pytest_asyncio.fixture
async def cwmp_database() -> AsyncGenerator[Database]:
    schema = f"test_cwmp_{uuid4().hex}"
    settings = get_settings()
    admin = Database(settings)
    url = make_url(str(settings.database_url)).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    database = Database(
        settings.model_copy(
            update={
                "database_url": PostgresDsn(url.render_as_string(hide_password=False))
            }
        )
    )
    try:
        async with admin.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with database.engine.begin() as connection:
            await connection.run_sync(migrate)
        yield database
    finally:
        await database.close()
        async with admin.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.close()


@pytest.mark.asyncio
async def test_upsert_sessions_events_and_repeated_inform(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    inform = parse_inform(inform_xml)
    service = InformService(PostgresInformRepository(cwmp_database))
    first, cookie = await service.receive(inform, None)
    duplicate, _ = await service.receive(inform, cookie)
    second, _ = await service.receive(replace(inform, retry_count=1), None)
    assert first.id == duplicate.id
    assert first.id != second.id and first.cpe_id == second.cpe_id
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(Cpe)) == 1
        assert await db.scalar(select(func.count()).select_from(CwmpSessionRow)) == 2
        events = (
            await db.scalars(
                select(CwmpEvent)
                .where(CwmpEvent.session_id == first.id)
                .order_by(CwmpEvent.position)
            )
        ).all()
        assert [(e.code, e.command_key) for e in events] == [
            (e.code, e.command_key) for e in inform.events
        ]
        row = await db.get(CwmpSessionRow, first.id)
        assert row is not None
        assert (
            row.version,
            row.rpc_id,
            row.parameter_count,
            row.retry_count,
            row.max_envelopes,
        ) == ("1.0", inform.rpc_id, 3, 0, 1)
        assert row.current_time == inform.current_time
        assert row.token_hash != cookie
        cpe = await db.get(Cpe, first.cpe_id)
        assert cpe is not None and cpe.firmware == "LAB-1.0"
        assert cpe.last_seen >= cpe.first_seen


@pytest.mark.asyncio
async def test_concurrent_upserts_do_not_duplicate_cpe(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    service = InformService(PostgresInformRepository(cwmp_database))
    inform = parse_inform(inform_xml)
    results = await asyncio.gather(*(service.receive(inform, None) for _ in range(4)))
    assert len({session.cpe_id for session, _ in results}) == 1
    async with cwmp_database.session() as db:
        assert await db.scalar(select(func.count()).select_from(Cpe)) == 1
        assert await db.scalar(select(func.count()).select_from(CwmpSessionRow)) == 4


@pytest.mark.asyncio
async def test_failed_events_roll_back_entire_inform(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    service = InformService(PostgresInformRepository(cwmp_database))
    invalid = replace(parse_inform(inform_xml), events=(Event("x" * 65, ""),))
    with pytest.raises(StorageUnavailable):
        await service.receive(invalid, None)
    async with cwmp_database.session() as db:
        for model in (Cpe, CwmpSessionRow, CwmpEvent):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_real_http_flow_persists_completion(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    service = InformService(PostgresInformRepository(cwmp_database))
    async with AsyncClient(
        transport=ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/cwmp", content=inform_xml, headers={"Content-Type": "text/xml"}
        )
        assert response.status_code == 200
        assert b"InformResponse" in response.content
        assert (await client.post("/cwmp", content=b"")).status_code == 204
    async with cwmp_database.session() as db:
        session = (await db.scalars(select(CwmpSessionRow))).one()
        assert session.state == "completed" and session.completed_at is not None
        assert await db.scalar(select(func.count()).select_from(CwmpEvent)) == 2


@pytest.mark.asyncio
async def test_session_cannot_be_reused_or_hijacked(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    service = InformService(PostgresInformRepository(cwmp_database))
    inform = parse_inform(inform_xml)
    _, cookie = await service.receive(inform, None)
    other = replace(inform, device=replace(inform.device, serial_number="OTHER"))
    with pytest.raises(InvalidSession):
        await service.receive(other, cookie)
    with pytest.raises(InvalidSession):
        await service.finish("unknown")
    await service.finish(cookie)
    with pytest.raises(InvalidSession):
        await service.finish(cookie)


@pytest.mark.asyncio
async def test_expired_session_cannot_continue(
    cwmp_database: Database, inform_xml: bytes
) -> None:
    repo = PostgresInformRepository(cwmp_database)
    now = datetime.now(UTC)
    await repo.accept(
        parse_inform(inform_xml),
        "expired",
        now - timedelta(minutes=2),
        now - timedelta(seconds=1),
        False,
    )
    with pytest.raises(InvalidSession):
        await repo.complete("expired", now)


@pytest.mark.asyncio
async def test_migration_matches_models_and_can_be_reapplied(
    cwmp_database: Database,
) -> None:
    async with cwmp_database.engine.begin() as connection:
        differences = await connection.run_sync(
            lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata)
        )
        assert differences == []
        await connection.run_sync(lambda c: migrate(c, "down"))
        await connection.run_sync(migrate)
        count = await connection.scalar(select(func.count()).select_from(Cpe))
        assert count == 0
