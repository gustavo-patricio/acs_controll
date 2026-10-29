"""Authorization and orchestration ports for asynchronous CPE reads."""

from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from acs.application.services.inform import hash_token
from acs.domain.cpe.inform import Parameter
from acs.domain.cpe.inventory import NormalizedInventory
from acs.domain.cpe.telemetry import TelemetryCollection
from acs.domain.events.session import CwmpSession, InvalidSession
from acs.domain.operations.collection import CollectionProgress, CollectionSpec
from acs.domain.operations.export import ExportSnapshot
from acs.domain.operations.query import (
    CatalogEntry,
    ParameterInfo,
    QueryCommand,
    QueryKind,
    QueryOperation,
    QueryPlan,
    QueryReply,
    QueryResultItem,
    QueryScope,
    QueryTarget,
)
from acs.domain.security import Principal


class QueryPolicy(Protocol):
    def permitted_values(
        self, reply: QueryReply, plan: QueryPlan
    ) -> tuple[Parameter, ...]: ...

    def evidence(
        self, reply: QueryReply, plan: QueryPlan, kind: QueryKind, at: datetime
    ) -> str: ...
    def plan(
        self,
        target: QueryTarget,
        scope: QueryScope = "device_info",
        parameter_path: str | None = None,
        names: tuple[str, ...] = (),
    ) -> QueryPlan: ...
    def restore(
        self,
        profile_id: str,
        version: str,
        scope: QueryScope = "device_info",
        parameter_path: str | None = None,
        names: tuple[str, ...] = (),
    ) -> QueryPlan: ...
    def discovery_names(
        self, reply: QueryReply, plan: QueryPlan
    ) -> tuple[ParameterInfo, ...]: ...

    def collect(
        self, reply: QueryReply, plan: QueryPlan, at: datetime, operation_id: UUID
    ) -> TelemetryCollection: ...
    def collection_results(
        self, reply: QueryReply, plan: QueryPlan, collection: TelemetryCollection
    ) -> tuple[QueryResultItem, ...]: ...
    def normalize(
        self, reply: QueryReply, plan: QueryPlan, at: datetime, operation_id: UUID
    ) -> NormalizedInventory: ...


class QueryRepository(Protocol):
    async def export_snapshot(self, cpe_id: UUID, prefix: str) -> ExportSnapshot: ...

    async def create_collection(
        self, cpe_id: UUID, spec: CollectionSpec, key: UUID, actor: UUID, now: datetime
    ) -> CollectionProgress: ...
    async def get_collection(
        self, cpe_id: UUID, job_id: UUID, now: datetime
    ) -> CollectionProgress: ...

    async def catalog(
        self, cpe_id: UUID, prefix: str, limit: int, offset: int
    ) -> tuple[CatalogEntry, ...]: ...

    async def create(
        self,
        cpe_id: UUID,
        kind: QueryKind,
        key: UUID,
        actor: UUID | None,
        source: Literal["api", "local"],
        now: datetime,
        *,
        scope: QueryScope = "device_info",
        parameter_path: str | None = None,
        capture_evidence: bool = False,
        parameter_names: tuple[str, ...] = (),
    ) -> QueryOperation: ...
    async def get_evidence(self, cpe_id: UUID, operation_id: UUID) -> str: ...
    async def get(
        self, cpe_id: UUID, operation_id: UUID, now: datetime
    ) -> QueryOperation: ...
    async def exchange(
        self, token_hash: str, reply: QueryReply | None, now: datetime
    ) -> tuple[CwmpSession, QueryCommand | None]: ...


class QueryService:
    async def export_snapshot(
        self, cpe_id: UUID, prefix: str, principal: Principal
    ) -> ExportSnapshot:
        principal.require("cpe:query")
        return await self.repository.export_snapshot(cpe_id, prefix)

    def __init__(self, repository: QueryRepository) -> None:
        self.repository = repository

    async def create(
        self,
        cpe_id: UUID,
        kind: QueryKind,
        key: UUID,
        principal: Principal,
        *,
        scope: QueryScope = "device_info",
        parameter_path: str | None = None,
        capture_evidence: bool = False,
        parameter_names: tuple[str, ...] = (),
    ) -> QueryOperation:
        principal.require("cpe:query")
        return await self.repository.create(
            cpe_id,
            kind,
            key,
            principal.id,
            "api",
            datetime.now(UTC),
            scope=scope,
            parameter_path=parameter_path,
            capture_evidence=capture_evidence,
            parameter_names=parameter_names,
        )

    async def create_collection(
        self, cpe_id: UUID, spec: CollectionSpec, key: UUID, principal: Principal
    ) -> CollectionProgress:
        principal.require("cpe:query")
        return await self.repository.create_collection(
            cpe_id, spec, key, principal.id, datetime.now(UTC)
        )

    async def get_collection(
        self, cpe_id: UUID, job_id: UUID, principal: Principal
    ) -> CollectionProgress:
        principal.require("inventory:read")
        return await self.repository.get_collection(cpe_id, job_id, datetime.now(UTC))

    async def catalog(
        self, cpe_id: UUID, prefix: str, limit: int, offset: int, principal: Principal
    ) -> tuple[CatalogEntry, ...]:
        principal.require("inventory:read")
        return await self.repository.catalog(cpe_id, prefix, limit, offset)

    async def get_evidence(
        self, cpe_id: UUID, operation_id: UUID, principal: Principal
    ) -> str:
        principal.require("cpe:query")
        return await self.repository.get_evidence(cpe_id, operation_id)

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
