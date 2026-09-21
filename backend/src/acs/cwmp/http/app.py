"""Separate HTTP adapter for the development CWMP receiver."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from acs.application.services.inform import InformService
from acs.cwmp.http.openapi import REQUEST_BODY, RESPONSES
from acs.cwmp.soap.inform import (
    MAX_BODY,
    BadEnvelope,
    RpcFault,
    parse_inform,
    serialize_fault,
    serialize_inform_response,
)
from acs.domain.events.session import InvalidSession, StorageUnavailable
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import PostgresInformRepository
from acs.infrastructure.observability.cwmp import configure_logging, record
from acs.settings import get_settings

COOKIE = "acs_cwmp_session"


def create_app(service: InformService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        nonlocal service
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
        database = Database(settings)
        service = InformService(PostgresInformRepository(database))
        try:
            yield
        finally:
            await database.close()
            service = None

    app = FastAPI(title="ACS CWMP receiver", lifespan=lifespan, redoc_url=None)

    @app.post(
        "/cwmp",
        response_class=Response,
        tags=["CWMP"],
        summary="Receive Inform or complete a CWMP session",
        description=(
            "Development/test only. Send the synthetic Inform example as text/xml. "
            "Then clear the body completely and execute again within 60 seconds. "
            "The browser automatically sends the HttpOnly session cookie on the "
            "same origin; do not enter it manually. Whitespace is not an empty body. "
            "Requests persist data in the configured PostgreSQL database."
        ),
        openapi_extra={"requestBody": REQUEST_BODY},
        responses=RESPONSES,
    )
    async def cwmp(request: Request) -> Response:
        request_id = uuid4()
        headers = {"X-Request-ID": str(request_id), "Cache-Control": "no-store"}

        def error(status: int) -> Response:
            record("request_rejected", request_id, status)
            return Response(status_code=status, headers=headers)

        if service is None:
            return error(503)
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
                session = await service.finish(cookie)
                response = Response(status_code=204, headers=headers)
                response.delete_cookie(COOKIE, path="/cwmp")
                record("session_completed", request_id, 204, session)
                return response
            if (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "text/xml"
            ):
                return error(415)
            inform = parse_inform(bytes(body))
            session, token = await service.receive(inform, cookie)
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
        except StorageUnavailable:
            return error(503)

    return app


app = create_app()
