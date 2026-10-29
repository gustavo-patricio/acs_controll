"""Transactional queue, correlation, replay handling and inventory collection."""

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from acs.application.services.queries import QueryPolicy
from acs.domain.events.session import CwmpSession, InvalidSession, StorageUnavailable
from acs.domain.operations.collection import (
    CONCRETE_OBJECT,
    CollectionProgress,
    CollectionSpec,
)
from acs.domain.operations.export import ExportSnapshot
from acs.domain.operations.query import (
    CatalogEntry,
    QueryCommand,
    QueryConflict,
    QueryKind,
    QueryNotFound,
    QueryOperation,
    QueryPlan,
    QueryReply,
    QueryResultItem,
    QueryScope,
    QueryState,
    QueryTarget,
)
from acs.infrastructure.database import collection_jobs, result_export
from acs.infrastructure.database.cwmp_models import Cpe, CwmpSessionRow
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import (
    PostgresInformRepository,
    session_value,
)
from acs.infrastructure.database.query_models import (
    CollectionJobRow,
    ParameterCatalogRow,
    ParameterValueRow,
    QueryAttemptRow,
    QueryOperationRow,
)
from acs.infrastructure.database.telemetry import update_telemetry


def target(cpe: Cpe) -> QueryTarget:
    return QueryTarget(
        cpe.manufacturer, cpe.oui, cpe.product_class, cpe.firmware, cpe.model
    )


def operation(row: QueryOperationRow, attempted: bool) -> QueryOperation:
    return QueryOperation(
        row.id,
        row.cpe_id,
        cast(QueryKind, row.kind),
        cast(QueryState, row.state),
        row.profile_id,
        row.profile_version,
        row.created_at,
        row.expires_at,
        row.finished_at,
        row.failure_reason,
        row.fault_code,
        tuple(
            QueryResultItem(
                cast(str, item["name"]),
                cast(str | None, item["value"]),
                cast(bool | None, item["writable"]),
            )
            for item in row.result
        ),
        int(attempted),
        cast(QueryScope, row.scope),
        row.parameter_path,
        row.capture_evidence,
        row.evidence_payload is not None,
    )


class PostgresQueryRepository:
    async def export_snapshot(self, cpe_id: UUID, prefix: str) -> ExportSnapshot:
        return await result_export.snapshot(self.database, cpe_id, prefix)

    def __init__(self, database: Database, policy: QueryPolicy) -> None:
        self.database = database
        self.policy = policy

    @staticmethod
    async def _cpe(db: AsyncSession, cpe_id: UUID) -> Cpe:
        cpe = await db.scalar(select(Cpe).where(Cpe.id == cpe_id).with_for_update())
        if cpe is None:
            raise QueryNotFound
        return cpe

    @staticmethod
    async def _expire(db: AsyncSession, cpe_id: UUID, now: datetime) -> None:
        rows = (
            await db.scalars(
                select(QueryOperationRow)
                .where(
                    QueryOperationRow.cpe_id == cpe_id,
                    QueryOperationRow.state.in_(("pending", "dispatched")),
                    QueryOperationRow.expires_at <= now,
                )
                .with_for_update()
            )
        ).all()
        for row in rows:
            row.state, row.failure_reason, row.finished_at = (
                "expired",
                "deadline_exceeded",
                now,
            )
            attempt = await db.get(QueryAttemptRow, row.id)
            if attempt is not None:
                session = await db.get(
                    CwmpSessionRow, attempt.session_id, with_for_update=True
                )
                if session is not None and session.state != "completed":
                    session.state, session.completed_at = "completed", now
        await db.flush()

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
    ) -> QueryOperation:
        if parameter_names and (
            kind != "get_parameter_values"
            or scope != "device_info"
            or len(set(parameter_names)) != len(parameter_names)
            or len(parameter_names) > 16
        ):
            raise QueryConflict("unsupported_parameters")
        parameter_names = tuple(sorted(parameter_names))
        try:
            async with self.database.session() as db, db.begin():
                cpe = await self._cpe(db, cpe_id)
                await self._expire(db, cpe_id, now)
                old = await db.scalar(
                    select(QueryOperationRow).where(
                        QueryOperationRow.cpe_id == cpe_id,
                        QueryOperationRow.idempotency_key == key,
                    )
                )
                if old is not None:
                    if (
                        old.kind,
                        old.scope,
                        old.parameter_path,
                        old.capture_evidence,
                    ) != (
                        kind,
                        scope,
                        parameter_path,
                        capture_evidence,
                    ):
                        raise QueryConflict("idempotency_conflict")
                    if (
                        scope == "device_info"
                        and tuple(old.requested_names) != parameter_names
                    ):
                        raise QueryConflict("idempotency_conflict")
                    return operation(
                        old, await db.get(QueryAttemptRow, old.id) is not None
                    )
                if await collection_jobs.active(db, cpe_id):
                    raise QueryConflict("collection_in_progress")
                if await db.scalar(
                    select(QueryOperationRow.id).where(
                        QueryOperationRow.cpe_id == cpe_id,
                        QueryOperationRow.state.in_(("pending", "dispatched")),
                    )
                ):
                    raise QueryConflict("operation_in_progress")
                if scope == "discovery" and kind != "get_parameter_names":
                    raise QueryConflict("discovery_is_names_only")
                discovered: set[str] = set()
                if scope in ("wan", "optical"):
                    if kind != "get_parameter_values" or parameter_path is None:
                        raise QueryConflict("collection_is_values_only")
                    evidence = (
                        await db.scalars(
                            select(QueryOperationRow)
                            .where(
                                QueryOperationRow.cpe_id == cpe_id,
                                QueryOperationRow.scope == "discovery",
                                QueryOperationRow.state == "succeeded",
                                QueryOperationRow.expected_firmware == cpe.firmware,
                                QueryOperationRow.finished_at
                                >= now - timedelta(hours=24),
                            )
                            .order_by(
                                QueryOperationRow.finished_at.desc(),
                                QueryOperationRow.id,
                            )
                            .limit(256)
                        )
                    ).all()
                    visited: set[str | None] = set()
                    for discovery in evidence:
                        if discovery.parameter_path in visited:
                            continue
                        visited.add(discovery.parameter_path)
                        for item in discovery.result:
                            name = item.get("name")
                            if isinstance(name, str) and name.startswith(
                                parameter_path
                            ):
                                discovered.add(name)
                plan = self.policy.plan(
                    target(cpe),
                    scope,
                    parameter_path,
                    parameter_names or tuple(discovered),
                )
                row = QueryOperationRow(
                    id=uuid4(),
                    cpe_id=cpe_id,
                    idempotency_key=key,
                    kind=kind,
                    scope=scope,
                    parameter_path=parameter_path,
                    capture_evidence=capture_evidence,
                    requested_names=list(plan.names)
                    if scope in ("wan", "optical") or parameter_names
                    else [],
                    state="pending",
                    profile_id=plan.profile_id,
                    profile_version=plan.profile_version,
                    expected_firmware=plan.target.firmware,
                    requested_by=actor,
                    request_source=source,
                    local_actor=f"uid:{os.getuid()}" if source == "local" else None,
                    created_at=now,
                    expires_at=now + timedelta(minutes=10),
                    result=[],
                )
                db.add(row)
                await db.flush()
                return operation(row, False)
        except SQLAlchemyError:
            raise StorageUnavailable from None

    async def create_collection(
        self, cpe_id: UUID, spec: CollectionSpec, key: UUID, actor: UUID, now: datetime
    ) -> CollectionProgress:
        try:
            async with self.database.session() as db, db.begin():
                cpe = await self._cpe(db, cpe_id)
                await self._expire(db, cpe_id, now)
                row = await collection_jobs.create(
                    db, cpe_id, target(cpe), self.policy, spec, key, actor, now
                )
                return collection_jobs.progress(row)
        except SQLAlchemyError:
            raise StorageUnavailable from None

    async def get_collection(
        self, cpe_id: UUID, job_id: UUID, now: datetime
    ) -> CollectionProgress:
        try:
            async with self.database.session() as db, db.begin():
                cpe = await self._cpe(db, cpe_id)
                row = await db.get(CollectionJobRow, job_id, with_for_update=True)
                if row is None or row.cpe_id != cpe_id:
                    raise QueryNotFound
                await self._expire(db, cpe_id, now)
                await collection_jobs.advance(db, row, target(cpe), self.policy, now)
                return collection_jobs.progress(row)
        except SQLAlchemyError:
            raise StorageUnavailable from None

    async def catalog(
        self, cpe_id: UUID, prefix: str, limit: int, offset: int
    ) -> tuple[CatalogEntry, ...]:
        try:
            async with self.database.session() as db:
                if await db.get(Cpe, cpe_id) is None:
                    raise QueryNotFound
                rows = (
                    await db.scalars(
                        select(ParameterCatalogRow)
                        .where(
                            ParameterCatalogRow.cpe_id == cpe_id,
                            ParameterCatalogRow.path.startswith(
                                prefix, autoescape=True
                            ),
                        )
                        .order_by(ParameterCatalogRow.path)
                        .limit(limit)
                        .offset(offset)
                    )
                ).all()
                return tuple(
                    CatalogEntry(
                        r.cpe_id,
                        r.path,
                        r.is_object,
                        r.writable,
                        r.observed_at,
                        r.operation_id,
                    )
                    for r in rows
                )
        except SQLAlchemyError:
            raise StorageUnavailable from None

    async def get_evidence(self, cpe_id: UUID, operation_id: UUID) -> str:
        try:
            async with self.database.session() as db:
                row = await db.get(QueryOperationRow, operation_id)
                if row is None or row.cpe_id != cpe_id:
                    raise QueryNotFound
                if row.evidence_payload is None:
                    raise QueryConflict("evidence_unavailable")
                return row.evidence_payload
        except SQLAlchemyError:
            raise StorageUnavailable from None

    async def get(
        self, cpe_id: UUID, operation_id: UUID, now: datetime
    ) -> QueryOperation:
        try:
            async with self.database.session() as db, db.begin():
                await self._cpe(db, cpe_id)
                await self._expire(db, cpe_id, now)
                row = await db.get(QueryOperationRow, operation_id)
                if row is None or row.cpe_id != cpe_id:
                    raise QueryNotFound
                return operation(row, await db.get(QueryAttemptRow, row.id) is not None)
        except SQLAlchemyError:
            raise StorageUnavailable from None

    @staticmethod
    def _finish(session: CwmpSessionRow, now: datetime) -> tuple[CwmpSession, None]:
        session.state, session.completed_at = "completed", session.completed_at or now
        return session_value(session), None

    def _plan(self, cpe: Cpe, row: QueryOperationRow) -> QueryPlan:
        plan = self.policy.plan(
            target(cpe),
            cast(QueryScope, row.scope),
            row.parameter_path,
            tuple(row.requested_names),
        )
        if (plan.profile_id, plan.profile_version, plan.target.firmware) != (
            row.profile_id,
            row.profile_version,
            row.expected_firmware,
        ):
            raise QueryConflict("profile_changed")
        return plan

    async def exchange(
        self, token_hash: str, reply: QueryReply | None, now: datetime
    ) -> tuple[CwmpSession, QueryCommand | None]:
        try:
            async with self.database.session() as db, db.begin():
                cpe_id = await db.scalar(
                    select(CwmpSessionRow.cpe_id).where(
                        CwmpSessionRow.token_hash == token_hash
                    )
                )
                if cpe_id is None:
                    raise InvalidSession
                cpe = await self._cpe(db, cpe_id)
                # Always CPE -> session -> operation: same order across transactions.
                session = await db.scalar(
                    select(CwmpSessionRow)
                    .where(CwmpSessionRow.token_hash == token_hash)
                    .with_for_update()
                )
                assert session is not None
                await self._expire(db, cpe_id, now)
                job = await collection_jobs.active(db, cpe_id)
                if job is not None:
                    await collection_jobs.advance(
                        db, job, target(cpe), self.policy, now
                    )
                if reply is not None:
                    return await self._reply(db, cpe, session, reply, now)
                if session.state == "completed":
                    return session_value(session), None
                if session.expires_at <= now:
                    raise InvalidSession
                if session.state == "awaiting_response":
                    attempt = await db.scalar(
                        select(QueryAttemptRow).where(
                            QueryAttemptRow.session_id == session.id
                        )
                    )
                    if attempt is None:
                        raise InvalidSession
                    row = await db.get(QueryOperationRow, attempt.operation_id)
                    assert row is not None
                else:
                    row = await db.scalar(
                        select(QueryOperationRow)
                        .where(
                            QueryOperationRow.cpe_id == cpe_id,
                            QueryOperationRow.state == "pending",
                        )
                        .with_for_update()
                    )
                    if row is None:
                        return self._finish(session, now)
                    attempt = None
                try:
                    plan = self._plan(cpe, row)
                except QueryConflict:
                    row.state, row.failure_reason, row.finished_at = (
                        "failed",
                        "profile_changed",
                        now,
                    )
                    return self._finish(session, now)
                if attempt is None:
                    deadline = min(row.expires_at, now + timedelta(seconds=60))
                    attempt = QueryAttemptRow(
                        operation_id=row.id,
                        session_id=session.id,
                        rpc_id=uuid4().hex,
                        dispatched_at=now,
                        expires_at=deadline,
                    )
                    db.add(attempt)
                    row.state, row.expires_at = "dispatched", deadline
                    session.state, session.expires_at = "awaiting_response", deadline
                    await db.flush()
                return session_value(session), QueryCommand(
                    row.id, attempt.rpc_id, cast(QueryKind, row.kind), plan
                )
        except SQLAlchemyError:
            raise StorageUnavailable from None

    async def _reply(
        self,
        db: AsyncSession,
        cpe: Cpe,
        session: CwmpSessionRow,
        reply: QueryReply,
        now: datetime,
    ) -> tuple[CwmpSession, None]:
        attempt = await db.scalar(
            select(QueryAttemptRow).where(
                QueryAttemptRow.session_id == session.id,
                QueryAttemptRow.rpc_id == reply.rpc_id,
            )
        )
        if attempt is None:
            raise InvalidSession
        row = await db.get(
            QueryOperationRow, attempt.operation_id, with_for_update=True
        )
        assert row is not None
        if row.state == "expired":
            return self._finish(session, now)
        try:
            plan = self._plan(cpe, row)
        except QueryConflict:
            if row.state == "dispatched":
                row.state, row.failure_reason, row.finished_at = (
                    "failed",
                    "profile_changed",
                    now,
                )
                return self._finish(session, now)
            # A successful firmware query can legitimately change profile eligibility.
            try:
                plan = self.policy.restore(
                    row.profile_id,
                    row.profile_version,
                    cast(QueryScope, row.scope),
                    row.parameter_path,
                    tuple(row.requested_names),
                )
            except QueryConflict:
                raise InvalidSession from None
        reason: str | None = None
        results: list[QueryResultItem] = []
        inventory = None
        telemetry = None
        if reply.kind == "fault":
            reason = "cpe_fault"
        elif reply.kind != row.kind:
            raise InvalidSession
        elif reply.kind == "get_parameter_values":
            if {p.name for p in reply.parameters} != set(plan.names):
                reason = "unexpected_parameters"
            elif plan.scope in ("wan", "optical"):
                try:
                    telemetry = self.policy.collect(reply, plan, now, row.id)
                    # Store only canonical results. Optical calibration retains only
                    # validated numeric syntax in the restricted operation result.
                    results = list(
                        self.policy.collection_results(reply, plan, telemetry)
                    )
                    if any(
                        field.state in ("invalid", "unverified")
                        for field in telemetry.fields
                    ):
                        reason = (
                            "invalid_values"
                            if any(
                                field.state == "invalid" for field in telemetry.fields
                            )
                            else "unverified_units"
                        )
                except QueryConflict:
                    reason = "not_internet_pppoe"
            elif row.requested_names:
                permitted = self.policy.permitted_values(reply, plan)
                if len(permitted) != len(reply.parameters):
                    reason = "invalid_values"
                else:
                    results = [QueryResultItem(p.name, p.value) for p in permitted]
            elif any(p.value_type != "xsd:string" for p in reply.parameters):
                reason = "invalid_values"
            else:
                inventory = self.policy.normalize(reply, plan, now, row.id)
                observations = (inventory.model, inventory.firmware, inventory.hardware)
                results = [
                    QueryResultItem(name, obs.value)
                    for name, obs in zip(plan.names, observations, strict=True)
                ]
                if any(obs.state != "valid" for obs in observations):
                    reason = "invalid_values"
        else:
            if any(
                item.name == plan.parameter_path
                or not CONCRETE_OBJECT.fullmatch(
                    item.name if item.name.endswith(".") else item.name + "."
                )
                or not item.name.startswith(plan.parameter_path)
                or "." in item.name[len(plan.parameter_path) :].rstrip(".")
                for item in reply.names
            ):
                reason = "unexpected_parameters"
            else:
                results = [
                    QueryResultItem(item.name, writable=item.writable)
                    for item in self.policy.discovery_names(reply, plan)
                ]
        results.sort(key=lambda item: item.name)
        safe_result = [asdict(item) for item in results]
        digest = hashlib.sha256(
            json.dumps(
                [reply.kind, reply.fault_code, reason, safe_result], sort_keys=True
            ).encode()
        ).hexdigest()
        if row.state in ("succeeded", "failed"):
            if attempt.response_digest != digest:
                raise InvalidSession
            return session_value(session), None
        if row.state != "dispatched" or session.state != "awaiting_response":
            raise InvalidSession
        if reply.kind == "get_parameter_names" and reason is None:
            names = reply.names
            job = await collection_jobs.active(db, cpe.id)
            if job is not None and job.current_operation_id == row.id:
                remaining = job.spec["max_entries"] - job.entry_count
                if len(names) > remaining:
                    collection_jobs.limited(job, "max_entries")
                    names = tuple(sorted(names, key=lambda p: p.name))[:remaining]
            for item in names:
                statement = insert(ParameterCatalogRow).values(
                    cpe_id=cpe.id,
                    path=item.name,
                    is_object=item.name.endswith("."),
                    writable=item.writable,
                    observed_at=now,
                    operation_id=row.id,
                )
                await db.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            ParameterCatalogRow.cpe_id,
                            ParameterCatalogRow.path,
                        ],
                        set_={
                            key: getattr(statement.excluded, key)
                            for key in (
                                "is_object",
                                "writable",
                                "observed_at",
                                "operation_id",
                            )
                        },
                        where=ParameterCatalogRow.observed_at <= now,
                    )
                )
        if reply.kind == "get_parameter_values" and reason is None:
            for item in self.policy.permitted_values(reply, plan):
                statement = insert(ParameterValueRow).values(
                    cpe_id=cpe.id,
                    path=item.name,
                    xml_type=item.value_type,
                    value=item.value,
                    observed_at=now,
                    operation_id=row.id,
                )
                await db.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            ParameterValueRow.cpe_id,
                            ParameterValueRow.path,
                        ],
                        set_={
                            key: getattr(statement.excluded, key)
                            for key in (
                                "xml_type",
                                "value",
                                "observed_at",
                                "operation_id",
                            )
                        },
                        where=ParameterValueRow.observed_at <= now,
                    )
                )
        if inventory is not None:
            await PostgresInformRepository.update_inventory(db, cpe.id, inventory)
        if telemetry is not None:
            await update_telemetry(db, cpe.id, telemetry)
        row.state = "succeeded" if reason is None else "failed"
        row.failure_reason, row.finished_at, row.fault_code = (
            reason,
            now,
            reply.fault_code,
        )
        row.result = [
            {"name": item.name, "value": item.value, "writable": item.writable}
            for item in results
        ]
        if row.capture_evidence and reason != "unexpected_parameters":
            row.evidence_payload = self.policy.evidence(
                reply, plan, cast(QueryKind, row.kind), now
            )
        attempt.response_digest = digest
        result = self._finish(session, now)
        await db.flush()
        job = await collection_jobs.active(db, cpe.id)
        if job is not None:
            await collection_jobs.advance(db, job, target(cpe), self.policy, now)
        return result
