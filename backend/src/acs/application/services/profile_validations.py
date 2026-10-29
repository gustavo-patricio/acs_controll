"""Authorization for draft validation and approval."""

from typing import Protocol
from uuid import UUID

from acs.domain.operations.query import QueryOperation
from acs.domain.security import Principal


class ValidationRepository(Protocol):
    async def start(
        self, cpe_id: UUID, key: UUID, actor: UUID
    ) -> dict[str, object]: ...
    async def status(self, cpe_id: UUID) -> dict[str, object]: ...
    async def approve(self, cpe_id: UUID, actor: UUID) -> dict[str, object]: ...
    async def collect(self, cpe_id: UUID, key: UUID, actor: UUID) -> QueryOperation: ...


class ProfileValidationService:
    def __init__(self, repository: ValidationRepository) -> None:
        self.repository = repository

    async def start(
        self, cpe_id: UUID, key: UUID, principal: Principal
    ) -> dict[str, object]:
        principal.require("cpe:query")
        return await self.repository.start(cpe_id, key, principal.id)

    async def status(self, cpe_id: UUID, principal: Principal) -> dict[str, object]:
        principal.require("inventory:read")
        return await self.repository.status(cpe_id)

    async def approve(self, cpe_id: UUID, principal: Principal) -> dict[str, object]:
        principal.require("cpe:query")
        return await self.repository.approve(cpe_id, principal.id)

    async def collect(
        self, cpe_id: UUID, key: UUID, principal: Principal
    ) -> QueryOperation:
        principal.require("cpe:query")
        return await self.repository.collect(cpe_id, key, principal.id)
