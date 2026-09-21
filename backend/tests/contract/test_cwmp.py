"""HTTP adapter tests with a repository double and the real use cases."""

import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import PostgresDsn

from acs.application.services.inform import InformService
from acs.cwmp.http import app as cwmp_module
from acs.cwmp.http.app import COOKIE, create_app
from acs.cwmp.http.openapi import INFORM_EXAMPLE
from acs.domain.cpe.inform import Inform
from acs.domain.events.session import CwmpSession, InvalidSession, StorageUnavailable
from acs.settings import Settings


class MemoryRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, CwmpSession] = {}

    async def accept(
        self,
        inform: Inform,
        token_hash: str,
        now: datetime,
        expires_at: datetime,
        continuing: bool,
    ) -> CwmpSession:
        if continuing:
            raise InvalidSession
        row = CwmpSession(
            uuid4(),
            uuid4(),
            inform.rpc_id,
            inform.version,
            "awaiting_empty",
            expires_at,
        )
        self.sessions[token_hash] = row
        return row

    async def complete(self, token_hash: str, now: datetime) -> CwmpSession:
        row = self.sessions.get(token_hash)
        if row is None:
            raise InvalidSession
        row.require_active(now)
        row = replace(row, state="completed")
        self.sessions[token_hash] = row
        return row


@pytest.mark.asyncio
async def test_swagger_contract_and_documented_session_flow() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(InformService(MemoryRepository()))),
        base_url="http://test",
    ) as client:
        docs = await client.get("/docs")
        assert docs.status_code == 200
        assert "SwaggerUIBundle" in docs.text
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/cwmp"]["post"]
        body = operation["requestBody"]
        assert body["required"] is False
        examples = body["content"]["text/xml"]["examples"]
        assert examples["inform"]["value"] == INFORM_EXAMPLE
        assert "text/xml" in operation["responses"]["200"]["content"]
        assert "content" not in operation["responses"]["204"]
        assert set(operation["responses"]) == {
            "200",
            "204",
            "400",
            "408",
            "413",
            "415",
            "500",
            "503",
        }
        response = await client.post(
            "/cwmp",
            content=examples["inform"]["value"],
            headers={"Content-Type": "text/xml"},
        )
        assert response.status_code == 200
        assert "InformResponse" in response.text
        assert COOKIE in client.cookies
        response = await client.post(
            "/cwmp",
            content=examples["empty"]["value"],
            headers={"Content-Type": "text/xml"},
        )
        assert response.status_code == 204
        assert response.content == b""


@pytest.mark.asyncio
async def test_inform_then_empty_post(
    inform_xml: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    repo = MemoryRepository()
    app = create_app(InformService(repo))
    caplog.set_level(logging.INFO, logger="acs.cwmp")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/cwmp",
            content=inform_xml,
            headers={"Content-Type": "text/xml", "Authorization": "sensitive-auth"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/xml")
        assert b"InformResponse" in response.content
        assert COOKIE in client.cookies
        token = client.cookies[COOKIE]
        completed = await client.post("/cwmp", content=b"")
        assert completed.status_code == 204 and completed.content == b""
        assert COOKIE not in client.cookies
        assert (await client.post("/cwmp", content=b"")).status_code == 400
    records = [json.loads(r.message) for r in caplog.records if r.name == "acs.cwmp"]
    assert records[0]["session_id"] == records[1]["session_id"]
    assert records[0]["request_id"] == response.headers["x-request-id"]
    assert records[0]["request_id"] != records[1]["request_id"]
    for secret in (
        "SYNTHETIC-SECRET",
        "sensitive-auth",
        "lab-command",
        "LAB000001",
        token,
    ):
        assert secret not in caplog.text
    assert next(iter(repo.sessions.values())).state == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "content_type", "status"),
    [
        (b"<broken>", "text/xml", 400),
        (
            b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
            "text/xml",
            400,
        ),
        (b" ", "text/xml", 400),
        (b"{}", "application/json", 415),
        (b"x" * 1_048_577, "text/xml", 413),
    ],
)
async def test_http_rejects_invalid_input(
    payload: bytes, content_type: str, status: int
) -> None:
    repo = MemoryRepository()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(InformService(repo))),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/cwmp", content=payload, headers={"Content-Type": content_type}
        )
    assert response.status_code == status
    assert repo.sessions == {}


@pytest.mark.asyncio
async def test_invalid_arguments_return_soap_fault(inform_xml: bytes) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(InformService(MemoryRepository()))),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/cwmp",
            content=inform_xml.replace(b"<RetryCount>0</RetryCount>", b""),
            headers={"Content-Type": "text/xml"},
        )
    assert response.status_code == 500
    assert b"<FaultCode>8003</FaultCode>" in response.content


class BrokenRepository(MemoryRepository):
    async def accept(
        self,
        inform: Inform,
        token_hash: str,
        now: datetime,
        expires_at: datetime,
        continuing: bool,
    ) -> CwmpSession:
        raise StorageUnavailable


@pytest.mark.asyncio
async def test_storage_failure_never_acknowledges_inform(inform_xml: bytes) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(InformService(BrokenRepository()))),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/cwmp", content=inform_xml, headers={"Content-Type": "text/xml"}
        )
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert b"InformResponse" not in response.content


@pytest.mark.asyncio
async def test_chunked_request_is_bounded() -> None:
    async def chunks() -> AsyncGenerator[bytes]:
        yield b"x" * 524_288
        yield b"x" * 524_289

    repo = MemoryRepository()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(InformService(repo))),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/cwmp", content=chunks(), headers={"Content-Type": "text/xml"}
        )
    assert response.status_code == 413
    assert not repo.sessions


@pytest.mark.asyncio
async def test_unsupported_rpc_returns_standard_fault(inform_xml: bytes) -> None:
    repo = MemoryRepository()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(InformService(repo))),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/cwmp",
            content=inform_xml.replace(b"cwmp:Inform", b"cwmp:Other"),
            headers={"Content-Type": "text/xml"},
        )
    assert response.status_code == 500
    assert b"<FaultCode>8000</FaultCode>" in response.content
    assert not repo.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "echo"),
    [("production", False), ("staging", False), ("development", True)],
)
async def test_receiver_refuses_unprotected_deployment(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    echo: bool,
) -> None:
    settings = Settings(
        database_url=PostgresDsn("postgresql+psycopg://test:local@localhost/test")
    )
    settings = settings.model_copy(
        update={"environment": environment, "database_echo": echo}
    )
    monkeypatch.setattr(cwmp_module, "get_settings", lambda: settings)
    app = create_app()
    with pytest.raises(RuntimeError, match="development/test"):
        async with app.router.lifespan_context(app):
            pytest.fail("Unsafe startup must be rejected")
