"""Authenticated creation and observation of bounded read operations."""

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security.http import HTTPBase
from pydantic import BaseModel, ConfigDict, Field

from acs.application.services.authentication import Authenticator
from acs.application.services.queries import QueryService
from acs.domain.events.session import StorageUnavailable
from acs.domain.operations.query import (
    QueryConflict,
    QueryKind,
    QueryNotFound,
    QueryOperation,
    QueryScope,
    QueryState,
)
from acs.domain.security import (
    AccessDenied,
    AuthenticationFailed,
    AuthenticationUnavailable,
    Principal,
)
from acs.infrastructure.observability.security import record
from acs.infrastructure.security.http import InsecureTransport, authenticate_request


class SafeQueryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        def rejected(exc: HTTPException) -> HTTPException:
            request_id = str(uuid4())
            exc.headers = {
                **(exc.headers or {}),
                "Cache-Control": "no-store",
                "X-Request-ID": request_id,
            }
            record("query_request_rejected", request_id, exc.status_code)
            return exc

        async def safe(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                raise rejected(HTTPException(422, "invalid_query_request")) from None
            except HTTPException as exc:
                raise rejected(exc) from None

        return safe


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: QueryKind
    idempotency_key: UUID
    scope: QueryScope = "device_info"
    parameter_path: str | None = Field(default=None, max_length=256)


class QueryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    value: str | None
    writable: bool | None


class QueryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cpe_id: UUID
    kind: QueryKind
    state: QueryState
    profile_id: str
    profile_version: str
    created_at: datetime
    expires_at: datetime
    finished_at: datetime | None
    failure_reason: str | None
    fault_code: int | None
    result: tuple[QueryItemResponse, ...]
    attempt_count: int
    scope: QueryScope
    parameter_path: str | None


def create_router(
    service: Callable[[], QueryService | None],
    authenticator: Callable[[], Authenticator | None],
) -> APIRouter:
    async def authenticate(request: Request) -> Principal:
        try:
            return await authenticate_request(request, authenticator(), "admin")
        except AuthenticationFailed:
            raise HTTPException(
                401,
                "authentication_required",
                headers={
                    "WWW-Authenticate": 'Basic realm="acs-admin"',
                    "Cache-Control": "no-store",
                },
            ) from None
        except (AccessDenied, InsecureTransport):
            raise HTTPException(403, "access_denied") from None
        except AuthenticationUnavailable:
            raise HTTPException(503, "authentication_unavailable") from None

    router = APIRouter(
        prefix="/api/v1/cpes/{cpe_id}/queries",
        tags=["Consultas remotas"],
        route_class=SafeQueryRoute,
        dependencies=[
            Depends(
                HTTPBase(scheme="basic", auto_error=False, scheme_name="AdminBasic")
            )
        ],
        responses={
            401: {"description": "Autenticação necessária"},
            403: {"description": "Acesso negado"},
            404: {"description": "CPE/operação não encontrada"},
            409: {
                "description": "Perfil incompatível, operação ativa ou chave em conflito"
            },
            503: {"description": "Serviço indisponível"},
        },
    )

    def target() -> QueryService:
        result = service()
        if result is None:
            raise HTTPException(503, "query_store_unavailable")
        return result

    def output(
        result: QueryOperation, response: Response, principal: Principal, status: int
    ) -> QueryResponse:
        request_id = str(uuid4())
        response.headers.update(
            {"X-Request-ID": request_id, "Cache-Control": "no-store"}
        )
        record(
            "query_created" if status == 202 else "query_read",
            request_id,
            status,
            principal.id,
            operation_id=result.id,
        )
        return QueryResponse.model_validate(result)

    def failure(exc: Exception) -> HTTPException:
        if isinstance(exc, AccessDenied):
            return HTTPException(403, "access_denied")
        if isinstance(exc, QueryNotFound):
            return HTTPException(404, "query_target_not_found")
        if isinstance(exc, QueryConflict):
            return HTTPException(409, "query_conflict")
        return HTTPException(503, "query_store_unavailable")

    @router.post(
        "",
        status_code=202,
        response_model=QueryResponse,
        summary="Agendar consulta de leitura pelo perfil da CPE",
    )
    async def create(
        cpe_id: UUID,
        body: QueryInput,
        response: Response,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> QueryResponse:
        try:
            return output(
                await target().create(
                    cpe_id,
                    body.kind,
                    body.idempotency_key,
                    principal,
                    scope=body.scope,
                    parameter_path=body.parameter_path,
                ),
                response,
                principal,
                202,
            )
        except (AccessDenied, QueryNotFound, QueryConflict, StorageUnavailable) as exc:
            raise failure(exc) from None

    @router.get(
        "/{operation_id}",
        response_model=QueryResponse,
        summary="Consultar estado e resultado da operação",
    )
    async def get(
        cpe_id: UUID,
        operation_id: UUID,
        response: Response,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> QueryResponse:
        try:
            return output(
                await target().get(cpe_id, operation_id, principal),
                response,
                principal,
                200,
            )
        except (AccessDenied, QueryNotFound, StorageUnavailable) as exc:
            raise failure(exc) from None

    return router
