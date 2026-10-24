"""Read-only inventory use case, independent of HTTP and persistence libraries."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from acs.domain.security import AccessDenied, Principal


class InventoryUnavailable(Exception):
    """Inventory cannot currently be read."""


class CpeNotFound(Exception):
    """The requested device is not registered."""


@dataclass(frozen=True)
class CpeSummary:
    id: UUID
    manufacturer: str
    oui: str
    product_class: str
    serial_number: str
    model: str | None
    firmware: str | None
    hardware: str | None
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True)
class CpePage:
    items: tuple[CpeSummary, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class SessionSummary:
    id: UUID
    cpe_id: UUID
    version: str
    state: str
    started_at: datetime
    expires_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class EventSummary:
    session_id: UUID
    position: int
    code: str
    received_at: datetime


@dataclass(frozen=True)
class SessionPage:
    items: tuple[SessionSummary, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class EventPage:
    items: tuple[EventSummary, ...]
    total: int
    limit: int
    offset: int


class CpeInventoryRepository(Protocol):
    async def get_cpe(self, cpe_id: UUID) -> CpeSummary: ...

    async def list_sessions(
        self, cpe_id: UUID, limit: int, offset: int
    ) -> SessionPage: ...

    async def list_events(self, cpe_id: UUID, limit: int, offset: int) -> EventPage: ...

    async def list_cpes(
        self,
        limit: int,
        offset: int,
        manufacturer: str | None,
        serial_number: str | None,
    ) -> CpePage: ...


class CpeInventoryService:
    def __init__(
        self, repository: CpeInventoryRepository, principal: Principal | None = None
    ) -> None:
        self.repository = repository
        self.principal = principal

    def for_principal(self, principal: Principal) -> "CpeInventoryService":
        return CpeInventoryService(self.repository, principal)

    def _authorize(self) -> None:
        if self.principal is None:
            raise AccessDenied
        self.principal.require("inventory:read")

    async def get_cpe(self, cpe_id: UUID) -> CpeSummary:
        self._authorize()
        return await self.repository.get_cpe(cpe_id)

    async def list_sessions(
        self, cpe_id: UUID, limit: int = 50, offset: int = 0
    ) -> SessionPage:
        self._authorize()
        self._validate_pagination(limit, offset)
        return await self.repository.list_sessions(cpe_id, limit, offset)

    async def list_events(
        self, cpe_id: UUID, limit: int = 50, offset: int = 0
    ) -> EventPage:
        self._authorize()
        self._validate_pagination(limit, offset)
        return await self.repository.list_events(cpe_id, limit, offset)

    @staticmethod
    def _validate_pagination(limit: int, offset: int) -> None:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination")

    async def list_cpes(
        self,
        limit: int = 50,
        offset: int = 0,
        manufacturer: str | None = None,
        serial_number: str | None = None,
    ) -> CpePage:
        self._authorize()
        self._validate_pagination(limit, offset)
        return await self.repository.list_cpes(
            limit, offset, manufacturer, serial_number
        )
