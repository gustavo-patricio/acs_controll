"""Administrative adapter for explicit draft validation and approval."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security.http import HTTPBase
from pydantic import BaseModel, ConfigDict

from acs.application.services.authentication import Authenticator
from acs.application.services.profile_validations import ProfileValidationService
from acs.domain.events.session import StorageUnavailable
from acs.domain.operations.query import QueryConflict, QueryNotFound
from acs.domain.security import (
    AccessDenied,
    AuthenticationFailed,
    AuthenticationUnavailable,
    Principal,
)
from acs.infrastructure.observability.security import record
from acs.infrastructure.security.http import InsecureTransport, authenticate_request


class KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID


class FieldResult(BaseModel):
    field: str
    path_or_rule: str
    xml_type: str | None
    conversion: str
    required: bool
    status: Literal["pending", "valid", "invalid"]
    reason: str | None


class ValidationResult(BaseModel):
    cpe_id: UUID
    profile_id: str
    state: Literal[
        "validating", "validated", "invalid", "approved", "needs_revalidation"
    ]
    operation_id: UUID
    firmware_changed: bool
    fields: list[FieldResult]
    approved_at: datetime | None


class CollectionResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cpe_id: UUID
    state: str
    kind: str
    scope: str


def create_router(
    service: Callable[[], ProfileValidationService | None],
    authenticator: Callable[[], Authenticator | None],
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/cpes/{cpe_id}/profiles/draft",
        tags=["profile-validation"],
        dependencies=[
            Depends(
                HTTPBase(scheme="basic", auto_error=False, scheme_name="AdminBasic")
            )
        ],
    )

    async def principal(request: Request) -> Principal:
        try:
            return await authenticate_request(request, authenticator(), "admin")
        except AuthenticationFailed:
            raise HTTPException(
                401,
                "authentication_required",
                headers={"WWW-Authenticate": 'Basic realm="acs-admin"'},
            ) from None
        except (AccessDenied, InsecureTransport):
            raise HTTPException(403, "access_denied") from None
        except AuthenticationUnavailable:
            raise HTTPException(503, "authentication_unavailable") from None

    def target() -> ProfileValidationService:
        result = service()
        if result is None:
            raise HTTPException(503, "profile_validation_unavailable")
        return result

    def headers(response: Response, event: str, principal: Principal) -> None:
        request_id = str(uuid4())
        response.headers.update(
            {"Cache-Control": "no-store", "X-Request-ID": request_id}
        )
        record(event, request_id, 200, principal.id)

    def failure(exc: Exception) -> HTTPException:
        if isinstance(exc, AccessDenied):
            return HTTPException(403, "access_denied")
        if isinstance(exc, QueryNotFound):
            return HTTPException(404, "profile_validation_not_found")
        if isinstance(exc, QueryConflict):
            return HTTPException(409, "profile_validation_conflict")
        return HTTPException(503, "profile_validation_unavailable")

    @router.post("/validate", status_code=202, response_model=ValidationResult)
    async def validate(
        cpe_id: UUID,
        body: KeyInput,
        response: Response,
        user: Annotated[Principal, Depends(principal)],
    ) -> ValidationResult:
        try:
            result = await target().start(cpe_id, body.idempotency_key, user)
        except (AccessDenied, QueryNotFound, QueryConflict, StorageUnavailable) as exc:
            raise failure(exc) from None
        headers(response, "profile_validation_started", user)
        return ValidationResult.model_validate(result)

    @router.get("", response_model=ValidationResult)
    async def get(
        cpe_id: UUID, response: Response, user: Annotated[Principal, Depends(principal)]
    ) -> ValidationResult:
        try:
            result = await target().status(cpe_id, user)
        except (AccessDenied, QueryNotFound, StorageUnavailable) as exc:
            raise failure(exc) from None
        headers(response, "profile_validation_read", user)
        return ValidationResult.model_validate(result)

    @router.post("/approve", response_model=ValidationResult)
    async def approve(
        cpe_id: UUID, response: Response, user: Annotated[Principal, Depends(principal)]
    ) -> ValidationResult:
        try:
            result = await target().approve(cpe_id, user)
        except (AccessDenied, QueryNotFound, QueryConflict, StorageUnavailable) as exc:
            raise failure(exc) from None
        headers(response, "profile_validation_approved", user)
        return ValidationResult.model_validate(result)

    @router.post("/collect", status_code=202, response_model=CollectionResult)
    async def collect(
        cpe_id: UUID,
        body: KeyInput,
        response: Response,
        user: Annotated[Principal, Depends(principal)],
    ) -> CollectionResult:
        try:
            result = await target().collect(cpe_id, body.idempotency_key, user)
        except (AccessDenied, QueryNotFound, QueryConflict, StorageUnavailable) as exc:
            raise failure(exc) from None
        headers(response, "profile_collection_started", user)
        return CollectionResult.model_validate(result)

    return router
