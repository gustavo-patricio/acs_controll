"""Authorization and orchestration ports for asynchronous CPE reads."""

from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from acs.application.services.inform import hash_token
from acs.domain.cpe.inventory import NormalizedInventory
from acs.domain.events.session import CwmpSession, InvalidSession
from acs.domain.operations.query import (
    QueryCommand,
    QueryKind,
    QueryOperation,
    QueryPlan,
    QueryReply,
    QueryTarget,
)
from acs.domain.security import Principal


class QueryPolicy(Protocol):
    def plan(self, target: QueryTarget) -> QueryPlan: ...
    def restore(self, profile_id: str, version: str) -> QueryPlan: ...
    def normalize(
        self, reply: QueryReply, plan: QueryPlan, at: datetime, operation_id: UUID
    ) -> NormalizedInventory: ...


class QueryRepository(Protocol):
    async def create(
        self,
        cpe_id: UUID,
        kind: QueryKind,
        key: UUID,
        actor: UUID | None,
        source: Literal["api", "local"],
        now: datetime,
    ) -> QueryOperation: ...
    async def get(
        self, cpe_id: UUID, operation_id: UUID, now: datetime
    ) -> QueryOperation: ...
    async def exchange(
        self, token_hash: str, reply: QueryReply | None, now: datetime
    ) -> tuple[CwmpSession, QueryCommand | None]: ...


class QueryService:
    def __init__(self, repository: QueryRepository) -> None:
        self.repository = repository

    async def create(
        self, cpe_id: UUID, kind: QueryKind, key: UUID, principal: Principal
    ) -> QueryOperation:
        principal.require("cpe:query")
        return await self.repository.create(
            cpe_id, kind, key, principal.id, "api", datetime.now(UTC)
        )

    async def get(
        self, cpe_id: UUID, operation_id: UUID, principal: Principal
    ) -> QueryOperation:
        principal.require("inventory:read")
        return await self.repository.get(cpe_id, operation_id, datetime.now(UTC))

    async def exchange(
        self, cookie: str | None, reply: QueryReply | None, principal: Principal
    ) -> tuple[CwmpSession, QueryCommand | None]:
        principal.require_cpe()
        if not cookie or len(cookie) > 128:
            raise InvalidSession
        return await self.repository.exchange(
            hash_token(f"{principal.id}:{cookie}"), reply, datetime.now(UTC)
        )
