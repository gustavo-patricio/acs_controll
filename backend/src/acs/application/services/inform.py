"""Inform and empty-continuation use cases."""

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from acs.application.services.inform_capture import InformCapture
from acs.application.services.inventory_normalization import InventoryNormalizer
from acs.domain.cpe.inform import Inform
from acs.domain.cpe.inventory import NormalizedInventory
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
        *,
        inventory: NormalizedInventory,
    ) -> CwmpSession: ...

    async def complete(self, token_hash: str, now: datetime) -> CwmpSession: ...


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InformService:
    def __init__(
        self,
        repository: InformRepository,
        normalizer: InventoryNormalizer,
        session_seconds: int = 60,
        *,
        capture: InformCapture | None = None,
        on_inventory_updated: Callable[[CwmpSession, NormalizedInventory], None]
        | None = None,
    ) -> None:
        self.repository = repository
        self.normalizer = normalizer
        self.session_seconds = session_seconds
        self.capture = capture
        self.on_inventory_updated = on_inventory_updated

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
        inventory = self.normalizer.normalize(inform, now)
        session = await self.repository.accept(
            inform,
            hash_token(f"{principal.id}:{token}"),
            now,
            now + timedelta(seconds=self.session_seconds),
            cookie is not None,
            inventory=inventory,
        )
        if cookie is None and self.on_inventory_updated is not None:
            self.on_inventory_updated(session, inventory)
        if self.capture is not None:
            await self.capture.capture(inform, session, now)
        return session, token

    async def finish(self, cookie: str | None, *, principal: Principal) -> CwmpSession:
        principal.require_cpe()
        if not cookie or len(cookie) > 128:
            raise InvalidSession
        return await self.repository.complete(
            hash_token(f"{principal.id}:{cookie}"), datetime.now(UTC)
        )
