"""Transactional queue, correlation, replay handling and inventory collection."""

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from acs.application.services.queries import QueryPolicy
from acs.domain.events.session import CwmpSession, InvalidSession, StorageUnavailable
from acs.domain.operations.query import (
    QueryCommand,
    QueryConflict,
    QueryKind,
    QueryNotFound,
    QueryOperation,
    QueryPlan,
    QueryReply,
    QueryResultItem,
    QueryState,
    QueryTarget,
)
from acs.infrastructure.database.cwmp_models import Cpe, CwmpSessionRow
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.inform_repository import (
    PostgresInformRepository,
    session_value,
)
from acs.infrastructure.database.query_models import QueryAttemptRow, QueryOperationRow


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
    )


class PostgresQueryRepository:
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
    ) -> QueryOperation:
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
                    if old.kind != kind:
                        raise QueryConflict("idempotency_conflict")
                    return operation(
                        old, await db.get(QueryAttemptRow, old.id) is not None
                    )
                if await db.scalar(
                    select(QueryOperationRow.id).where(
                        QueryOperationRow.cpe_id == cpe_id,
                        QueryOperationRow.state.in_(("pending", "dispatched")),
                    )
                ):
                    raise QueryConflict("operation_in_progress")
                plan = self.policy.plan(target(cpe))
                row = QueryOperationRow(
                    id=uuid4(),
                    cpe_id=cpe_id,
                    idempotency_key=key,
                    kind=kind,
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
        plan = self.policy.plan(target(cpe))
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
                plan = self.policy.restore(row.profile_id, row.profile_version)
            except QueryConflict:
                raise InvalidSession from None
        reason: str | None = None
        results: list[QueryResultItem] = []
        inventory = None
        if reply.kind == "fault":
            reason = "cpe_fault"
        elif reply.kind != row.kind:
            raise InvalidSession
        elif reply.kind == "get_parameter_values":
            if {p.name for p in reply.parameters} != set(plan.names):
                reason = "unexpected_parameters"
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
                not item.name.startswith(plan.parameter_path)
                or "." in item.name[len(plan.parameter_path) :].rstrip(".")
                for item in reply.names
            ):
                reason = "unexpected_parameters"
            else:
                results = [
                    QueryResultItem(item.name, writable=item.writable)
                    for item in reply.names
                    if item.name in plan.names
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
        if inventory is not None:
            await PostgresInformRepository.update_inventory(db, cpe.id, inventory)
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
        attempt.response_digest = digest
        return self._finish(session, now)
