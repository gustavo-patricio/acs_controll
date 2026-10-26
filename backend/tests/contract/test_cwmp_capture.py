"""HTTP authentication and persistence precede optional Inform capture."""

from datetime import datetime

import pytest
from httpx import ASGITransport, BasicAuth

from acs.application.services.inform import InformService
from acs.cwmp.compatibility.generic import GenericInventoryNormalizer
from acs.domain.cpe.inform import Inform
from acs.domain.events.session import CwmpSession
from auth_support import PASSWORD, authorized_client, cwmp_app
from contract.test_cwmp import BrokenRepository, MemoryRepository


class ObservedCapture:
    def __init__(self, repository: MemoryRepository) -> None:
        self.repository = repository
        self.calls = 0

    async def capture(
        self, inform: Inform, session: CwmpSession, received_at: datetime
    ) -> None:
        assert session in self.repository.sessions.values()
        self.calls += 1


@pytest.mark.asyncio
async def test_capture_only_follows_successful_inform(inform_xml: bytes) -> None:
    repository = MemoryRepository()
    capture = ObservedCapture(repository)
    app = cwmp_app(
        InformService(repository, GenericInventoryNormalizer(), capture=capture)
    )
    async with authorized_client(transport=ASGITransport(app=app)) as client:
        for payload, auth, status in (
            (inform_xml, BasicAuth("test", "wrong"), 401),
            (b"<broken>", BasicAuth("test", PASSWORD), 400),
            (
                b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
                BasicAuth("test", PASSWORD),
                400,
            ),
            (
                inform_xml.replace(b"LAB000001", b"OTHER-DEVICE"),
                BasicAuth("test", PASSWORD),
                403,
            ),
        ):
            response = await client.post(
                "/cwmp",
                content=payload,
                headers={"Content-Type": "text/xml"},
                auth=auth,
            )
            assert response.status_code == status
            assert capture.calls == 0
        response = await client.post(
            "/cwmp", content=inform_xml, headers={"Content-Type": "text/xml"}
        )
        assert response.status_code == 200 and capture.calls == 1
        assert (await client.post("/cwmp", content=b"")).status_code == 204
        assert capture.calls == 1


@pytest.mark.asyncio
async def test_database_failure_never_captures(inform_xml: bytes) -> None:
    repository = BrokenRepository()
    capture = ObservedCapture(repository)
    app = cwmp_app(
        InformService(repository, GenericInventoryNormalizer(), capture=capture)
    )
    async with authorized_client(transport=ASGITransport(app=app)) as client:
        response = await client.post(
            "/cwmp", content=inform_xml, headers={"Content-Type": "text/xml"}
        )
    assert response.status_code == 503 and capture.calls == 0
