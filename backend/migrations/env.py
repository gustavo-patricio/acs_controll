"""Alembic entry point; credentials are loaded only at execution time."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from acs.infrastructure.database.cwmp_models import Cpe
from acs.infrastructure.database.database import Database
from acs.settings import get_settings

# Importing the models registers all three tables in their shared metadata.
target_metadata = Cpe.metadata


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    database = Database(get_settings())
    try:
        async with database.engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await database.close()


if context.is_offline_mode():
    context.configure(
        url=str(get_settings().database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif "connection" in context.config.attributes:
    migrate(context.config.attributes["connection"])
else:
    asyncio.run(online())
