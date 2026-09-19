"""Command-line database connectivity check."""

import asyncio

from acs.infrastructure.database.database import Database
from acs.settings import get_settings


async def check_connection() -> bool:
    """Open and close a database connection using application settings."""

    database = Database(get_settings())
    try:
        return await database.is_healthy()
    finally:
        await database.close()


def main() -> None:
    """Exit unsuccessfully when the configured database cannot be reached."""

    if not asyncio.run(check_connection()):
        raise SystemExit("Database connection failed")
    print("Database connection successful")


if __name__ == "__main__":
    main()
