"""Administrative API application."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from acs.api.routes.cpes import create_router
from acs.api.routes.health import router as health_router
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.database import Database
from acs.settings import get_settings


def create_app(inventory: CpeInventoryService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        nonlocal inventory
        if inventory is not None:
            yield
            return
        settings = get_settings()
        # Do not expose an unauthenticated inventory outside the lab.
        if (
            settings.environment not in ("development", "test")
            or settings.database_echo
        ):
            yield
            return
        database = Database(settings)
        inventory = CpeInventoryService(PostgresCpeInventoryRepository(database))
        try:
            yield
        finally:
            inventory = None
            await database.close()

    async def get_inventory() -> CpeInventoryService | None:
        return inventory

    # Async dependency avoids a threadpool hop for this in-memory lookup.
    application = FastAPI(title="ACS Administrative API", lifespan=lifespan)
    application.include_router(health_router)
    application.include_router(create_router(get_inventory))
    return application


app = create_app()
