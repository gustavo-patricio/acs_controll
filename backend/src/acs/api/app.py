"""Administrative API application."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security.http import HTTPBase

from acs.api.routes.cpe_accounts import create_router as create_accounts_router
from acs.api.routes.cpes import create_router
from acs.api.routes.health import router as health_router
from acs.api.routes.queries import create_router as create_queries_router
from acs.application.services.authentication import Authenticator
from acs.application.services.cpe_accounts import CpeAccountService
from acs.application.services.cpe_inventory import CpeInventoryService
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.queries import HuaweiQueryPolicy
from acs.domain.security import (
    AccessDenied,
    AuthenticationFailed,
    AuthenticationUnavailable,
    Principal,
)
from acs.infrastructure.database.cpe_accounts import PostgresCpeAccountRepository
from acs.infrastructure.database.cpe_inventory import PostgresCpeInventoryRepository
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.queries import PostgresQueryRepository
from acs.infrastructure.observability.security import configure_logging, record
from acs.infrastructure.security.authentication import PostgresAuthenticator
from acs.infrastructure.security.http import InsecureTransport, authenticate_request
from acs.settings import get_settings


def create_app(
    inventory: CpeInventoryService | None = None,
    authenticator: Authenticator | None = None,
    accounts: CpeAccountService | None = None,
    queries: QueryService | None = None,
) -> FastAPI:
    settings = get_settings()
    accounts_enabled = (
        settings.enable_test_cpe_accounts
        and settings.environment in ("development", "test")
        and not settings.database_echo
    )
    anonymous_inventory = (
        settings.api_allow_anonymous_inventory
        and settings.environment in ("development", "test")
        and not settings.database_echo
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        nonlocal inventory, authenticator, accounts, queries
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
        configure_logging()
        inventory = CpeInventoryService(PostgresCpeInventoryRepository(database))
        queries = QueryService(PostgresQueryRepository(database, HuaweiQueryPolicy()))
        authenticator = PostgresAuthenticator(database)
        if accounts_enabled:
            accounts = CpeAccountService(PostgresCpeAccountRepository(database))
        try:
            yield
        finally:
            inventory = None
            authenticator = None
            accounts = None
            queries = None
            await database.close()

    async def get_inventory(request: Request) -> CpeInventoryService | None:
        request_id = str(uuid4())
        headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
        try:
            if inventory is None:
                raise AuthenticationUnavailable
            if anonymous_inventory:
                # Explicit local policy identity, not a persisted admin account.
                reader = Principal(UUID(int=0), "admin", frozenset({"inventory:read"}))
                return inventory.for_principal(reader)
            principal = await authenticate_request(request, authenticator, "admin")
            principal.require("inventory:read")
            return inventory.for_principal(principal)
        except (
            AuthenticationFailed,
            AuthenticationUnavailable,
            InsecureTransport,
            AccessDenied,
        ) as exc:
            if isinstance(exc, AuthenticationFailed):
                status, code = 401, "authentication_required"
                headers["WWW-Authenticate"] = 'Basic realm="acs-admin", charset="UTF-8"'
            elif isinstance(exc, AuthenticationUnavailable):
                status, code = 503, "inventory_unavailable"
            else:
                status, code = 403, "access_denied"
            record("inventory_access_rejected", request_id, status)
            raise HTTPException(
                status, detail={"code": code, "request_id": request_id}, headers=headers
            ) from None

    # Async dependency avoids a threadpool hop for this in-memory lookup.
    application = FastAPI(title="ACS Administrative API", lifespan=lifespan)
    application.include_router(health_router)
    application.include_router(
        create_queries_router(lambda: queries, lambda: authenticator)
    )
    if accounts_enabled:
        application.include_router(
            create_accounts_router(lambda: accounts, lambda: authenticator)
        )
    application.include_router(
        create_router(get_inventory, authentication_required=not anonymous_inventory),
        dependencies=[]
        if anonymous_inventory
        else [
            Depends(
                HTTPBase(scheme="basic", auto_error=False, scheme_name="AdminBasic")
            )
        ],
    )
    return application


app = create_app()
