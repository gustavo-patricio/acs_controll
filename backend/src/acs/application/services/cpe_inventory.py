"""Read-only inventory use case, independent of HTTP and persistence libraries."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


class InventoryUnavailable(Exception):
    """Inventory cannot currently be read."""


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


class CpeInventoryRepository(Protocol):
    async def list_cpes(
        self,
        limit: int,
        offset: int,
        manufacturer: str | None,
        serial_number: str | None,
    ) -> CpePage: ...


class CpeInventoryService:
    def __init__(self, repository: CpeInventoryRepository) -> None:
        self.repository = repository

    async def list_cpes(
        self,
        limit: int = 50,
        offset: int = 0,
        manufacturer: str | None = None,
        serial_number: str | None = None,
    ) -> CpePage:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination")
        return await self.repository.list_cpes(
            limit, offset, manufacturer, serial_number
        )
