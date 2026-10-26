"""Separate HTTP adapter for the development CWMP receiver."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.security.http import HTTPBase

from acs.application.services.authentication import Authenticator
from acs.application.services.inform import InformService
from acs.application.services.queries import QueryService
from acs.cwmp.compatibility.evidence import anonymized_inform
from acs.cwmp.compatibility.profiles import ProfileInventoryNormalizer
from acs.cwmp.compatibility.queries import HuaweiQueryPolicy
from acs.cwmp.http.openapi import REQUEST_BODY, RESPONSES
from acs.cwmp.soap.inform import (
    CWMP,
    MAX_BODY,
    BadEnvelope,
    RpcFault,
    parse_envelope,
    parse_inform,
    serialize_fault,
    serialize_inform_response,
)
from acs.cwmp.soap.queries import parse_query_reply, serialize_query
from acs.domain.events.session import InvalidSession, StorageUnavailable
from acs.domain.security import (
    AccessDenied,
    AuthenticationFailed,
    AuthenticationUnavailable,
)
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import PostgresInformRepository
from acs.infrastructure.database.queries import PostgresQueryRepository
from acs.infrastructure.observability.cwmp import (
    RejectionReason,
    configure_logging,
    record,
    record_inventory,
)
from acs.infrastructure.security.authentication import PostgresAuthenticator
from acs.infrastructure.security.http import InsecureTransport, authenticate_request
from acs.infrastructure.storage.inform_capture import FileInformCapture
from acs.settings import get_settings

COOKIE = "acs_cwmp_session"


def create_app(
    service: InformService | None = None,
    authenticator: Authenticator | None = None,
    queries: QueryService | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        nonlocal service, authenticator, queries
        if service is not None:
            yield
            return
        settings = get_settings()
        if (
            settings.environment not in ("development", "test")
            or settings.database_echo
        ):
            raise RuntimeError(
                "CWMP receiver requires development/test mode and SQL echo disabled"
            )
        configure_logging()
        capture = None
        if (
            settings.cwmp_capture_cpe_id is not None
            and settings.cwmp_capture_output is not None
        ):
            capture = FileInformCapture(
                settings.cwmp_capture_cpe_id,
                settings.cwmp_capture_output,
                settings.cwmp_capture_seconds,
                anonymized_inform,
            )
        database = Database(settings)
        queries = QueryService(PostgresQueryRepository(database, HuaweiQueryPolicy()))
        service = InformService(
            PostgresInformRepository(database),
            ProfileInventoryNormalizer(),
            capture=capture,
            on_inventory_updated=record_inventory,
        )
        authenticator = PostgresAuthenticator(
            database, allow_shared_cpe=settings.cwmp_allow_shared_credentials
        )
        try:
            yield
        finally:
            await database.close()
            service = None
            authenticator = None
            queries = None

    app = FastAPI(title="ACS CWMP receiver", lifespan=lifespan, redoc_url=None)

    @app.post(
        "/cwmp",
        response_class=Response,
        tags=["CWMP"],
        summary="Receber Inform, continuar sessão ou receber resposta de consulta",
        description=(
            "Development/test only. Send the synthetic Inform example as text/xml. "
            "Then clear the body completely and execute again within 60 seconds. "
            "A pending authenticated read operation may return a GetParameterNames or GetParameterValues RPC. "
            "The browser automatically sends the HttpOnly session cookie on the "
            "same origin; do not enter it manually. Whitespace is not an empty body. "
            "Requests persist data in the configured PostgreSQL database."
        ),
        openapi_extra={"requestBody": REQUEST_BODY},
        responses=RESPONSES,
        dependencies=[
            Depends(HTTPBase(scheme="basic", auto_error=False, scheme_name="CpeBasic"))
        ],
    )
    async def cwmp(request: Request) -> Response:
        request_id = uuid4()
        headers = {"X-Request-ID": str(request_id), "Cache-Control": "no-store"}

        def error(status: int, reason: RejectionReason | None = None) -> Response:
            record("request_rejected", request_id, status, reason=reason)
            return Response(status_code=status, headers=headers)

        if service is None:
            return error(503, "service_unavailable")
        try:
            principal = await authenticate_request(request, authenticator, "cpe")
        except InsecureTransport:
            return error(403)
        except AuthenticationFailed:
            headers["WWW-Authenticate"] = 'Basic realm="acs-cpe", charset="UTF-8"'
            return error(
                401,
                "authentication_rejected"
                if "authorization" in request.headers
                else "authentication_required",
            )
        except AuthenticationUnavailable as exc:
            return error(503, exc.reason)
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            return error(415)
        length = request.headers.get("content-length")
        if length is not None:
            if not length.isdecimal():
                return error(400)
            if len(length) > 12:
                return error(413)
            if int(length) > MAX_BODY:
                return error(413)
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY:
                        return error(413)
                    body.extend(chunk)
        except TimeoutError:
            return error(408)
        cookie = request.cookies.get(COOKIE)
        try:
            if not body:
                if queries is not None:
                    session, command = await queries.exchange(cookie, None, principal)
                    if command is not None:
                        record(
                            "query_dispatched",
                            request_id,
                            200,
                            session,
                            operation_id=command.operation_id,
                        )
                        return Response(
                            serialize_query(command),
                            media_type="text/xml",
                            headers=headers,
                        )
                else:
                    session = await service.finish(cookie, principal=principal)
                response = Response(status_code=204, headers=headers)
                response.delete_cookie(COOKIE, path="/cwmp")
                record("session_completed", request_id, 204, session)
                return response
            if (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "text/xml"
            ):
                return error(415)
            payload = bytes(body)
            _, rpc = parse_envelope(payload)
            if rpc.tag != f"{{{CWMP}}}Inform" and queries is not None:
                reply = parse_query_reply(payload)
                session, _ = await queries.exchange(cookie, reply, principal)
                response = Response(status_code=204, headers=headers)
                response.delete_cookie(COOKIE, path="/cwmp")
                record("query_response_processed", request_id, 204, session)
                return response
            inform = parse_inform(payload)
            session, token = await service.receive(inform, cookie, principal=principal)
            response = Response(
                serialize_inform_response(inform.rpc_id),
                media_type="text/xml",
                headers=headers,
            )
            response.set_cookie(
                COOKIE,
                token,
                path="/cwmp",
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="strict",
            )
            record("inform_accepted", request_id, 200, session)
            return response
        except BadEnvelope:
            return error(400)
        except RpcFault as fault:
            record("rpc_rejected", request_id, 500)
            return Response(
                serialize_fault(fault.rpc_id, fault.code),
                status_code=500,
                media_type="text/xml",
                headers=headers,
            )
        except InvalidSession:
            return error(400)
        except AccessDenied:
            return error(403)
        except StorageUnavailable:
            return error(503, "storage_unavailable")

    return app


app = create_app()
