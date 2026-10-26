"""Safe failure classification and recovery without changing device credentials."""

import json
import logging
from typing import Literal

import pytest
from httpx import ASGITransport, AsyncClient

from acs.application.services.inform import InformService
from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.cwmp.http.app import create_app
from acs.domain.security import AuthenticationUnavailable, Principal
from auth_support import PASSWORD, StubAuthenticator
from contract.test_cwmp import BrokenRepository, MemoryRepository


class RecoveringAuthenticator(StubAuthenticator):
    unavailable = True

    async def authenticate(
        self, username: str, password: str, kind: Literal["admin", "cpe"]
    ) -> Principal:
        if self.unavailable:
            raise AuthenticationUnavailable("authentication_store_unavailable")
        return await super().authenticate(username, password, kind)


@pytest.mark.asyncio
async def test_challenge_outage_and_recovery_on_same_receiver(
    inform_xml: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="acs.cwmp")
    auth = RecoveringAuthenticator()
    repository = MemoryRepository()
    app = create_app(InformService(repository, GenericInventoryNormalizer()), auth)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        headers = {"Content-Type": "text/xml"}
        challenge = await client.post("/cwmp", content=inform_xml, headers=headers)
        assert (
            challenge.status_code == 401
            and "Basic" in challenge.headers["www-authenticate"]
        )
        unavailable = await client.post(
            "/cwmp", content=inform_xml, headers=headers, auth=("test", PASSWORD)
        )
        assert unavailable.status_code == 503 and not repository.sessions
        assert "set-cookie" not in unavailable.headers and not unavailable.content
        auth.unavailable = False
        invalid = await client.post(
            "/cwmp",
            content=inform_xml,
            headers=headers,
            auth=("test", "SYNTHETIC-PRIVATE"),
        )
        assert invalid.status_code == 401
        accepted = await client.post(
            "/cwmp", content=inform_xml, headers=headers, auth=("test", PASSWORD)
        )
        assert accepted.status_code == 200
        assert (
            await client.post("/cwmp", content=b"", auth=("test", PASSWORD))
        ).status_code == 204
    logs = [json.loads(r.message) for r in caplog.records if r.name == "acs.cwmp"]
    assert [r.get("reason") for r in logs[:3]] == [
        "authentication_required",
        "authentication_store_unavailable",
        "authentication_rejected",
    ]
    assert logs[1]["request_id"] == unavailable.headers["x-request-id"]
    assert logs[-1]["event"] == "session_completed" and len(repository.sessions) == 1
    for private in (PASSWORD, "SYNTHETIC-PRIVATE", "LAB000001", inform_xml.decode()):
        assert private not in caplog.text


@pytest.mark.asyncio
async def test_storage_failure_is_distinct_and_retry_succeeds(
    inform_xml: bytes, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger="acs.cwmp")
    repository = MemoryRepository()
    app = create_app(
        InformService(repository, GenericInventoryNormalizer()), StubAuthenticator()
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        auth=("test", PASSWORD),
    ) as client:
        with monkeypatch.context() as scoped:
            scoped.setattr(repository, "accept", BrokenRepository().accept)
            rejected = await client.post(
                "/cwmp", content=inform_xml, headers={"Content-Type": "text/xml"}
            )
            assert rejected.status_code == 503 and not repository.sessions
        accepted = await client.post(
            "/cwmp", content=inform_xml, headers={"Content-Type": "text/xml"}
        )
        assert accepted.status_code == 200
        assert (await client.post("/cwmp", content=b"")).status_code == 204
    logs = [json.loads(r.message) for r in caplog.records if r.name == "acs.cwmp"]
    assert logs[0]["reason"] == "storage_unavailable"
    assert logs[0]["request_id"] == rejected.headers["x-request-id"]
    assert logs[-1]["event"] == "session_completed"
