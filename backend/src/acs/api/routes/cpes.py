"""Administrative inventory HTTP adapter."""

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from acs.application.services.cpe_inventory import (
    CpeInventoryService,
    CpePage,
    InventoryUnavailable,
)


class InventoryErrorDetail(BaseModel):
    code: str
    request_id: UUID


class InventoryError(BaseModel):
    detail: InventoryErrorDetail


def create_router(
    get_service: Callable[[], Awaitable[CpeInventoryService | None]],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["cpes"])

    @router.get(
        "/cpes",
        response_model=CpePage,
        summary="List registered CPEs",
        description=(
            "Development/test only until administrative authentication is implemented. "
            "Exact, case-sensitive filters are combined with AND. "
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
        request_id = str(uuid4())
        headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
        try:
            if service is None:
                raise InventoryUnavailable
            page = await service.list_cpes(limit, offset, manufacturer, serial_number)
        except InventoryUnavailable:
            raise HTTPException(
                status_code=503,
                detail={"code": "inventory_unavailable", "request_id": request_id},
                headers=headers,
            ) from None
        response.headers.update(headers)
        return page

    return router
