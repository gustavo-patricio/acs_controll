"""Inform and empty-continuation use cases."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Protocol

from acs.domain.cpe.inform import Inform
from acs.domain.events.session import CwmpSession, InvalidSession


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
        self, inform: Inform, cookie: str | None
    ) -> tuple[CwmpSession, str]:
        now = datetime.now(UTC)
        token = cookie if cookie is not None else secrets.token_urlsafe(32)
        if len(token) > 128:
            raise InvalidSession
        session = await self.repository.accept(
            inform,
            hash_token(token),
            now,
            now + timedelta(seconds=self.session_seconds),
            cookie is not None,
        )
        return session, token

    async def finish(self, cookie: str | None) -> CwmpSession:
        if not cookie or len(cookie) > 128:
            raise InvalidSession
        return await self.repository.complete(hash_token(cookie), datetime.now(UTC))
