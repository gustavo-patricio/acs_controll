"""Persistent bounded traversal using the existing operation queue and CPE lock."""

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acs.application.services.queries import QueryPolicy
from acs.domain.operations.collection import (
    CONCRETE_OBJECT,
    CollectionProgress,
    CollectionSpec,
)
from acs.domain.operations.query import QueryConflict, QueryTarget
from acs.infrastructure.database.query_models import (
    CollectionConfig,
    CollectionJobRow,
    CollectionStep,
    ParameterCatalogRow,
    QueryOperationRow,
)


def progress(row: CollectionJobRow) -> CollectionProgress:
    spec = CollectionSpec(
        kind=row.spec["kind"],
        root=row.spec["root"],
        parameter_names=tuple(row.spec["parameter_names"]),
        max_depth=row.spec["max_depth"],
        max_rpcs=row.spec["max_rpcs"],
        max_entries=row.spec["max_entries"],
        batch_size=row.spec["batch_size"],
    )
    return CollectionProgress(
        row.id,
        row.cpe_id,
        row.kind,
        row.state,
        spec,
        row.rpc_count,
        row.completed_count,
        row.entry_count,
        len(row.frontier),
        tuple(row.failures),
        tuple(row.limits_reached),
        tuple(UUID(i) for i in row.operation_ids),
        row.created_at,
        row.finished_at,
    )


async def active(db: AsyncSession, cpe_id: UUID) -> CollectionJobRow | None:
    return await db.scalar(
        select(CollectionJobRow)
        .where(CollectionJobRow.cpe_id == cpe_id, CollectionJobRow.state == "running")
        .with_for_update()
    )


async def create(
    db: AsyncSession,
    cpe_id: UUID,
    target: QueryTarget,
    policy: QueryPolicy,
    spec: CollectionSpec,
    key: UUID,
    actor: UUID,
    now: datetime,
) -> CollectionJobRow:
    spec.validate()
    data = CollectionConfig(
        kind=spec.kind,
        root=spec.root,
        parameter_names=sorted(spec.parameter_names),
        max_depth=spec.max_depth,
        max_rpcs=spec.max_rpcs,
        max_entries=spec.max_entries,
        batch_size=spec.batch_size,
    )
    frontier: list[CollectionStep]
    old = await db.scalar(
        select(CollectionJobRow).where(
            CollectionJobRow.cpe_id == cpe_id, CollectionJobRow.idempotency_key == key
        )
    )
    if old is not None:
        if old.spec != data:
            raise QueryConflict("idempotency_conflict")
        return old
    if await active(db, cpe_id) or await db.scalar(
        select(QueryOperationRow.id).where(
            QueryOperationRow.cpe_id == cpe_id,
            QueryOperationRow.state.in_(("pending", "dispatched")),
        )
    ):
        raise QueryConflict("operation_in_progress")
    if spec.kind == "discovery":
        assert spec.root is not None
        policy.plan(target, "discovery", spec.root)
        frontier = [{"path": spec.root, "depth": 0}]
    else:
        names = sorted(spec.parameter_names)
        leaves = set(
            (
                await db.scalars(
                    select(ParameterCatalogRow.path).where(
                        ParameterCatalogRow.cpe_id == cpe_id,
                        ParameterCatalogRow.path.in_(names),
                        ParameterCatalogRow.is_object.is_(False),
                    )
                )
            ).all()
        )
        if leaves != set(names):
            raise QueryConflict("selection_requires_catalog_leaves")
        # Catalog presence never overrides the data policy.
        policy.plan(target, "device_info", names=tuple(names))
        frontier = [
            {"names": names[i : i + spec.batch_size], "depth": 0}
            for i in range(0, len(names), spec.batch_size)
        ]
    row = CollectionJobRow(
        id=uuid4(),
        cpe_id=cpe_id,
        idempotency_key=key,
        requested_by=actor,
        kind=spec.kind,
        state="running",
        spec=data,
        frontier=frontier,
        current_operation_id=None,
        current_depth=0,
        rpc_count=0,
        completed_count=0,
        entry_count=0,
        failures=[],
        limits_reached=[],
        operation_ids=[],
        created_at=now,
        finished_at=None,
    )
    db.add(row)
    await db.flush()
    await advance(db, row, target, policy, now)
    return row


def limited(row: CollectionJobRow, reason: str) -> None:
    if reason not in row.limits_reached:
        row.limits_reached = [*row.limits_reached, reason]


async def advance(
    db: AsyncSession,
    row: CollectionJobRow,
    target: QueryTarget,
    policy: QueryPolicy,
    now: datetime,
) -> None:
    if row.state != "running":
        return
    if row.current_operation_id is not None:
        operation = await db.get(QueryOperationRow, row.current_operation_id)
        assert operation is not None
        if operation.state in ("pending", "dispatched"):
            return
        row.completed_count += 1
        if operation.state != "succeeded":
            row.failures = [
                *row.failures,
                {
                    "operation_id": str(operation.id),
                    "reason": operation.failure_reason or operation.state,
                },
            ]
        elif row.kind == "discovery":
            entries = (
                await db.scalars(
                    select(ParameterCatalogRow)
                    .where(ParameterCatalogRow.operation_id == operation.id)
                    .order_by(ParameterCatalogRow.path)
                )
            ).all()
            row.entry_count += len(entries)
            frontier = list(row.frontier)
            for entry in entries:
                if not entry.is_object:
                    continue
                if row.current_depth >= row.spec["max_depth"]:
                    limited(row, "max_depth")
                elif CONCRETE_OBJECT.fullmatch(entry.path):
                    frontier.append(
                        {"path": entry.path, "depth": row.current_depth + 1}
                    )
                else:
                    limited(row, "unsupported_branch")
            row.frontier = frontier
        else:
            row.entry_count += len(operation.result)
        row.current_operation_id = None
    while row.frontier:
        if row.rpc_count >= row.spec["max_rpcs"]:
            limited(row, "max_rpcs")
            break
        remaining = row.spec["max_entries"] - row.entry_count
        if remaining <= 0:
            limited(row, "max_entries")
            break
        item, *rest = row.frontier
        row.frontier = rest
        names = tuple(item.get("names", ()))
        if len(names) > remaining:
            names = names[:remaining]
            limited(row, "max_entries")
        scope = "discovery" if row.kind == "discovery" else "device_info"
        path = item.get("path")
        try:
            plan = policy.plan(target, scope, path, names)
        except QueryConflict:
            row.failures = [*row.failures, {"reason": "unsupported_branch_or_profile"}]
            continue
        operation = QueryOperationRow(
            id=uuid4(),
            cpe_id=row.cpe_id,
            idempotency_key=uuid4(),
            kind="get_parameter_names"
            if row.kind == "discovery"
            else "get_parameter_values",
            scope=scope,
            parameter_path=path,
            capture_evidence=False,
            requested_names=list(plan.names) if names else [],
            state="pending",
            profile_id=plan.profile_id,
            profile_version=plan.profile_version,
            expected_firmware=cast(str, plan.target.firmware),
            requested_by=row.requested_by,
            request_source="api",
            local_actor=None,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            result=[],
        )
        db.add(operation)
        await db.flush()
        row.current_operation_id = operation.id
        row.current_depth = item["depth"]
        row.rpc_count += 1
        row.operation_ids = [*row.operation_ids, str(operation.id)]
        return
    row.state = "partial" if row.failures or row.limits_reached else "succeeded"
    row.finished_at = now
