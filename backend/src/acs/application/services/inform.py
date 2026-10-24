"""Inform and empty-continuation use cases."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol

from acs.domain.cpe.inform import Inform
from acs.domain.events.session import CwmpSession, InvalidSession
from acs.domain.security import Principal


class InformRepository(Protocol):
    async def accept(
        self,
        inform: Inform,
        token_hash: str,
        now: datetime,
        expires_at: datetime,
        continuing: bool,
    ) -> CwmpSession: ...

    async def complete(self, token_hash: str, now: datetime) -> CwmpSession: ...


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InformService:
    def __init__(self, repository: InformRepository, session_seconds: int = 60) -> None:
        self.repository = repository
        self.session_seconds = session_seconds

    async def receive(
        self, inform: Inform, cookie: str | None, *, principal: Principal
    ) -> tuple[CwmpSession, str]:
        principal.require_device(
            inform.device.oui, inform.device.product_class, inform.device.serial_number
        )
        now = datetime.now(UTC)
        token = cookie if cookie is not None else secrets.token_urlsafe(32)
        if len(token) > 128:
            raise InvalidSession
        session = await self.repository.accept(
            inform,
            hash_token(f"{principal.id}:{token}"),
            now,
            now + timedelta(seconds=self.session_seconds),
            cookie is not None,
        )
        return session, token

    async def finish(self, cookie: str | None, *, principal: Principal) -> CwmpSession:
        principal.require_cpe()
        if not cookie or len(cookie) > 128:
            raise InvalidSession
        return await self.repository.complete(
            hash_token(f"{principal.id}:{cookie}"), datetime.now(UTC)
        )
