"""Administrative inventory HTTP adapter."""

from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from acs.api.schemas.cpes import CpeResponse, EventListResponse, SessionListResponse
from acs.api.schemas.inventory import CpeInventoryResponse
from acs.application.services.cpe_inventory import (
    CpeInventoryService,
    CpeNotFound,
    CpePage,
    InventoryUnavailable,
)
from acs.infrastructure.observability.security import record


class InventoryErrorDetail(BaseModel):
    code: str
    request_id: UUID


class InventoryError(BaseModel):
    detail: InventoryErrorDetail


@contextmanager
def inventory_request(
    service: CpeInventoryService | None, response: Response
) -> Generator[CpeInventoryService]:
    request_id = str(uuid4())
    headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
    response.headers.update(headers)
    try:
        if service is None:
            raise InventoryUnavailable
        yield service
        record(
            "inventory_read",
            request_id,
            200,
            service.principal.id if service.principal else None,
        )
    except (CpeNotFound, InventoryUnavailable) as exc:
        missing = isinstance(exc, CpeNotFound)
        record("inventory_read_failed", request_id, 404 if missing else 503)
        raise HTTPException(
            status_code=404 if missing else 503,
            detail={
                "code": "cpe_not_found" if missing else "inventory_unavailable",
                "request_id": request_id,
            },
            headers=headers,
        ) from None


def create_router(
    get_service: Callable[[Request], Awaitable[CpeInventoryService | None]],
    *,
    authentication_required: bool = True,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1",
        tags=["cpes"],
        responses={
            401: {"model": InventoryError, "description": "Authentication required"},
            403: {
                "model": InventoryError,
                "description": "Permission denied or HTTPS required",
            },
        }
        if authentication_required
        else {},
    )

    @router.get(
        "/cpes",
        response_model=CpePage,
        summary="List registered CPEs",
        description=(
            (
                "Development/test only. Requires HTTPS and inventory:read permission. "
                if authentication_required
                else "Development/test only. Anonymous read access explicitly enabled. "
            )
            + "Exact, case-sensitive filters are combined with AND. "
            "Ordered by last_seen descending, then id ascending. "
            "last_seen is the last accepted Inform, not proof the CPE is online."
        ),
        responses={
            503: {"model": InventoryError, "description": "Inventory unavailable"}
        },
    )
    async def list_cpes(
        response: Response,
        service: Annotated[CpeInventoryService | None, Depends(get_service)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        manufacturer: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
        serial_number: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    ) -> CpePage:
        with inventory_request(service, response) as inventory:
            return await inventory.list_cpes(limit, offset, manufacturer, serial_number)

    errors: dict[int | str, dict[str, Any]] = {
        404: {"model": InventoryError, "description": "CPE not registered"},
        503: {"model": InventoryError, "description": "Inventory unavailable"},
    }

    @router.get(
        "/cpes/{cpe_id}",
        response_model=CpeResponse,
        responses=errors,
        summary="Get a registered CPE",
        description="Development/test only. Last persisted inventory; no remote query is issued.",
    )
    async def get_cpe(
        cpe_id: UUID,
        response: Response,
        service: Annotated[CpeInventoryService | None, Depends(get_service)],
    ) -> CpeResponse:
        with inventory_request(service, response) as inventory:
            return CpeResponse.model_validate(await inventory.get_cpe(cpe_id))

    @router.get(
        "/cpes/{cpe_id}/inventory",
        response_model=CpeInventoryResponse,
        responses=errors,
        summary="Consultar inventário normalizado e sua origem",
        description=(
            "Valores persistidos, origem do último valor válido e última observação de cada campo. "
            "Campos ausentes não indicam falta de suporte. Capacidades sem evidência ficam unknown. "
            "Não consulta a CPE, não dispara RPCs e não classifica online/offline."
        ),
    )
    async def get_inventory(
        cpe_id: UUID,
        response: Response,
        service: Annotated[CpeInventoryService | None, Depends(get_service)],
    ) -> CpeInventoryResponse:
        with inventory_request(service, response) as inventory:
            return CpeInventoryResponse.model_validate(
                await inventory.get_inventory(cpe_id)
            )

    @router.get(
        "/cpes/{cpe_id}/sessions",
        response_model=SessionListResponse,
        responses=errors,
        summary="List CPE sessions",
        description="Development/test only. Ordered by started_at descending, then id ascending. Persisted state; awaiting_empty can be expired. No tokens or RPC IDs returned.",
    )
    async def list_sessions(
        cpe_id: UUID,
        response: Response,
        service: Annotated[CpeInventoryService | None, Depends(get_service)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> SessionListResponse:
        with inventory_request(service, response) as inventory:
            return SessionListResponse.model_validate(
                await inventory.list_sessions(cpe_id, limit, offset)
            )

    @router.get(
        "/cpes/{cpe_id}/events",
        response_model=EventListResponse,
        responses=errors,
        summary="List CPE events",
        description="Development/test only. Ordered by session started_at descending, session id ascending, then position ascending. No command keys or parameter values returned.",
    )
    async def list_events(
        cpe_id: UUID,
        response: Response,
        service: Annotated[CpeInventoryService | None, Depends(get_service)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> EventListResponse:
        with inventory_request(service, response) as inventory:
            return EventListResponse.model_validate(
                await inventory.list_events(cpe_id, limit, offset)
            )

    return router
