"""Explicitly enabled laboratory credential endpoints."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security.http import HTTPBase
from pydantic import BaseModel, ConfigDict, SecretStr

from acs.application.services.authentication import Authenticator
from acs.application.services.cpe_accounts import (
    AccountConflict,
    AccountNotFound,
    AccountStoreUnavailable,
    CpeAccountService,
    NewCpeAccount,
)
from acs.domain.security import (
    AccessDenied,
    AuthenticationFailed,
    AuthenticationUnavailable,
    Principal,
)
from acs.infrastructure.observability.security import record
from acs.infrastructure.security.http import InsecureTransport, authenticate_request


class SafeValidationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default errors can echo submitted passwords or entire bodies.
                raise HTTPException(
                    422,
                    detail="invalid_account_request",
                    headers={"Cache-Control": "no-store"},
                ) from None

        return safe


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: SecretStr
    shared: bool = False
    oui: str | None = None
    product_class: str | None = None
    serial_number: str | None = None


class AccountOutput(BaseModel):
    id: UUID
    username: str
    shared: bool


def create_router(
    service: Callable[[], CpeAccountService | None],
    authenticator: Callable[[], Authenticator | None],
) -> APIRouter:
    async def authorize(request: Request) -> Principal:
        try:
            principal = await authenticate_request(request, authenticator(), "admin")
            principal.require("cpe_accounts:write")
            return principal
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
        prefix="/api/v1/test-cpe-accounts",
        tags=["Contas CPE de teste"],
        route_class=SafeValidationRoute,
        dependencies=[
            Depends(
                HTTPBase(scheme="basic", auto_error=False, scheme_name="AdminBasic")
            )
        ],
        responses={
            401: {"description": "Autenticação necessária"},
            403: {"description": "Acesso negado"},
            503: {"description": "Serviço indisponível"},
        },
    )

    @router.post(
        "",
        status_code=201,
        response_model=AccountOutput,
        responses={409: {"description": "Usuário ou identidade já cadastrado"}},
        summary="Criar credencial de CPE para testes",
    )
    async def create(
        body: AccountInput,
        response: Response,
        principal: Annotated[Principal, Depends(authorize)],
    ) -> AccountOutput:
        target = service()
        if target is None:
            raise HTTPException(503, "account_store_unavailable")
        try:
            identifier = await target.create(
                NewCpeAccount(
                    body.username,
                    body.password.get_secret_value(),
                    body.shared,
                    body.oui.upper() if body.oui else body.oui,
                    body.product_class,
                    body.serial_number,
                ),
                principal,
            )
        except ValueError:
            raise HTTPException(422, "invalid_account_request") from None
        except AccountConflict:
            raise HTTPException(409, "account_already_exists") from None
        except AccountStoreUnavailable:
            raise HTTPException(503, "account_store_unavailable") from None
        request_id = str(uuid4())
        response.headers.update(
            {"Cache-Control": "no-store", "X-Request-ID": request_id}
        )
        record("cpe_account_created", request_id, 201, principal.id)
        return AccountOutput(id=identifier, username=body.username, shared=body.shared)

    @router.delete(
        "/{account_id}",
        status_code=204,
        responses={404: {"description": "Conta CPE não encontrada"}},
        summary="Excluir credencial de CPE de teste",
    )
    async def remove(
        account_id: UUID, principal: Annotated[Principal, Depends(authorize)]
    ) -> Response:
        target = service()
        if target is None:
            raise HTTPException(503, "account_store_unavailable")
        try:
            await target.delete(account_id, principal)
        except AccountNotFound:
            raise HTTPException(404, "cpe_account_not_found") from None
        except AccountStoreUnavailable:
            raise HTTPException(503, "account_store_unavailable") from None
        request_id = str(uuid4())
        record("cpe_account_deleted", request_id, 204, principal.id)
        return Response(
            status_code=204,
            headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
        )

    return router
