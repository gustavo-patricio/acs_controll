"""Authenticated creation and observation of bounded read operations."""

import json
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security.http import HTTPBase
from pydantic import BaseModel, ConfigDict, Field

from acs.application.services.authentication import Authenticator
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.result_export import export_document, export_xml
from acs.domain.events.session import StorageUnavailable
from acs.domain.operations.collection import CollectionProgress, CollectionSpec
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


class CollectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    kind: Literal["discovery", "values"]
    root: str | None = Field(default=None, max_length=256)
    parameter_names: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = (
        Field(default=(), max_length=4096)
    )
    max_depth: int = Field(default=3, ge=0, le=16)
    max_rpcs: int = Field(default=20, ge=1, le=128)
    max_entries: int = Field(default=1000, ge=1, le=4096)
    batch_size: int = Field(default=8, ge=1, le=16)


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: QueryKind
    idempotency_key: UUID
    scope: QueryScope = "device_info"
    parameter_path: str | None = Field(default=None, max_length=256)
    parameter_names: (
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] | None
    ) = Field(default=None, min_length=1, max_length=16)
    capture_evidence: bool = False


class CatalogEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    cpe_id: UUID
    path: str
    is_object: bool
    writable: bool
    observed_at: datetime
    operation_id: UUID


class CatalogPageResponse(BaseModel):
    items: tuple[CatalogEntryResponse, ...]
    limit: int
    offset: int
    has_more: bool


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
    capture_evidence: bool
    evidence_available: bool


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

    @router.get(
        "/export",
        response_class=Response,
        responses={
            200: {
                "description": "Resultados anonimizados; XML reconstruído",
                "content": {"application/json": {}, "application/xml": {}},
            }
        },
        summary="Exportar resultados armazenados sem nova coleta",
    )
    async def export_results(
        cpe_id: UUID,
        principal: Annotated[Principal, Depends(authenticate)],
        format: Literal["json", "xml"] = "json",
        prefix: Annotated[str, Query(max_length=256)] = "",
    ) -> Response:
        try:
            document = export_document(
                await target().export_snapshot(cpe_id, prefix, principal)
            )
            payload = (
                export_xml(document)
                if format == "xml"
                else json.dumps(document, ensure_ascii=False).encode()
            )
            if len(payload) > 8 * 1024 * 1024:
                raise QueryConflict("export_requires_narrower_prefix")
        except (AccessDenied, QueryNotFound, QueryConflict, StorageUnavailable) as exc:
            raise failure(exc) from None
        request_id = str(uuid4())
        record("stored_results_exported", request_id, 200, principal.id)
        return Response(
            payload,
            media_type="application/xml" if format == "xml" else "application/json",
            headers={
                "Cache-Control": "no-store",
                "X-Request-ID": request_id,
                "Content-Disposition": f'attachment; filename="acs-results.{format}"',
            },
        )

    @router.get(
        "/catalog",
        response_model=CatalogPageResponse,
        summary="Consultar catálogo de parâmetros da CPE",
    )
    async def catalog(
        cpe_id: UUID,
        response: Response,
        principal: Annotated[Principal, Depends(authenticate)],
        prefix: Annotated[str, Query(max_length=256)] = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> CatalogPageResponse:
        try:
            rows = await target().catalog(cpe_id, prefix, limit + 1, offset, principal)
        except (AccessDenied, QueryNotFound, StorageUnavailable) as exc:
            raise failure(exc) from None
        response.headers.update(
            {"Cache-Control": "no-store", "X-Request-ID": str(uuid4())}
        )
        return CatalogPageResponse(
            items=tuple(CatalogEntryResponse.model_validate(r) for r in rows[:limit]),
            limit=limit,
            offset=offset,
            has_more=len(rows) > limit,
        )

    @router.post(
        "/collections",
        status_code=202,
        response_model=CollectionProgress,
        summary="Agendar descoberta limitada ou valores selecionados em lotes",
    )
    async def create_collection(
        cpe_id: UUID,
        body: CollectionInput,
        response: Response,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> CollectionProgress:
        try:
            result = await target().create_collection(
                cpe_id,
                CollectionSpec(**body.model_dump(exclude={"idempotency_key"})),
                body.idempotency_key,
                principal,
            )
        except (AccessDenied, QueryNotFound, QueryConflict, StorageUnavailable) as exc:
            raise failure(exc) from None
        response.headers.update(
            {"Cache-Control": "no-store", "X-Request-ID": str(uuid4())}
        )
        return result

    @router.get(
        "/collections/{job_id}",
        response_model=CollectionProgress,
        summary="Consultar progresso da coleta",
    )
    async def get_collection(
        cpe_id: UUID,
        job_id: UUID,
        response: Response,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> CollectionProgress:
        try:
            result = await target().get_collection(cpe_id, job_id, principal)
        except (AccessDenied, QueryNotFound, StorageUnavailable) as exc:
            raise failure(exc) from None
        response.headers.update(
            {"Cache-Control": "no-store", "X-Request-ID": str(uuid4())}
        )
        return result

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
                    capture_evidence=body.capture_evidence,
                    parameter_names=body.parameter_names or (),
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

    @router.get(
        "/{operation_id}/evidence",
        response_class=Response,
        summary="Baixar evidência anonimizada da consulta",
        description="Exige cpe:query. Retorna pacote JSON query-evidence-1 com XMLs reconstruídos de requisição/resposta. Disponível apenas quando capture_evidence foi habilitado e uma resposta válida foi correlacionada. Não representa XML bruto ou homologação de unidade.",
        responses={
            200: {
                "description": "Pacote parcial verificável offline",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": [
                                "format_version",
                                "manifest",
                                "request",
                                "response",
                            ],
                            "properties": {
                                "format_version": {"const": "query-evidence-1"},
                                "manifest": {"type": "object"},
                                "request": {"type": "object"},
                                "response": {"type": "object"},
                            },
                        }
                    }
                },
            }
        },
    )
    async def evidence(
        cpe_id: UUID,
        operation_id: UUID,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> Response:
        try:
            payload = await target().get_evidence(cpe_id, operation_id, principal)
        except (AccessDenied, QueryNotFound, QueryConflict, StorageUnavailable) as exc:
            raise failure(exc) from None
        request_id = str(uuid4())
        record(
            "query_evidence_read",
            request_id,
            200,
            principal.id,
            operation_id=operation_id,
        )
        return Response(
            payload,
            media_type="application/json",
            headers={
                "Cache-Control": "no-store",
                "X-Request-ID": request_id,
                "Content-Disposition": 'attachment; filename="query-evidence.json"',
            },
        )

    return router
