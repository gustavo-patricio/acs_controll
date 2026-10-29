"""Bounded consistent database snapshot; never reads raw XML or credentials."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from acs.domain.events.session import StorageUnavailable
from acs.domain.operations.export import (
    ExportName,
    ExportOperation,
    ExportSnapshot,
    ExportValue,
)
from acs.domain.operations.query import QueryConflict, QueryNotFound
from acs.infrastructure.database.cwmp_models import Cpe
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.query_models import (
    ParameterCatalogRow,
    ParameterValueRow,
    QueryOperationRow,
)

MAX_ENTRIES = 4096
HISTORY_LIMIT = 256


async def snapshot(database: Database, cpe_id: UUID, prefix: str) -> ExportSnapshot:
    try:
        async with database.session() as db, db.begin():
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            cpe = await db.get(Cpe, cpe_id)
            if cpe is None:
                raise QueryNotFound
            names = (
                await db.scalars(
                    select(ParameterCatalogRow)
                    .where(
                        ParameterCatalogRow.cpe_id == cpe_id,
                        ParameterCatalogRow.path.startswith(prefix, autoescape=True),
                    )
                    .order_by(ParameterCatalogRow.path)
                    .limit(MAX_ENTRIES + 1)
                )
            ).all()
            values = (
                await db.scalars(
                    select(ParameterValueRow)
                    .where(
                        ParameterValueRow.cpe_id == cpe_id,
                        ParameterValueRow.path.startswith(prefix, autoescape=True),
                    )
                    .order_by(ParameterValueRow.path)
                    .limit(MAX_ENTRIES + 1)
                )
            ).all()
            if len(names) > MAX_ENTRIES or len(values) > MAX_ENTRIES:
                raise QueryConflict("export_requires_narrower_prefix")
            recent_ids = (
                await db.scalars(
                    select(QueryOperationRow.id)
                    .where(QueryOperationRow.cpe_id == cpe_id)
                    .order_by(QueryOperationRow.created_at.desc(), QueryOperationRow.id)
                    .limit(HISTORY_LIMIT + 1)
                )
            ).all()
            ids = {r.operation_id for r in (*names, *values)} | set(
                recent_ids[:HISTORY_LIMIT]
            )
            if len(ids) > 512:
                raise QueryConflict("export_requires_narrower_prefix")
            q = QueryOperationRow
            rows = (
                await db.execute(
                    select(
                        q.id,
                        q.kind,
                        q.scope,
                        q.parameter_path,
                        q.requested_names,
                        q.result,
                        q.state,
                        q.failure_reason,
                        q.fault_code,
                        q.finished_at,
                    )
                    .where(q.cpe_id == cpe_id, q.id.in_(ids))
                    .order_by(q.created_at, q.id)
                )
            ).all()
            return ExportSnapshot(
                cpe.manufacturer,
                cpe.model,
                cpe.product_class,
                cpe.firmware,
                cpe.serial_number,
                prefix,
                tuple(
                    ExportName(
                        r.path, r.is_object, r.writable, r.observed_at, r.operation_id
                    )
                    for r in names
                ),
                tuple(
                    ExportValue(
                        r.path, r.xml_type, r.value, r.observed_at, r.operation_id
                    )
                    for r in values
                ),
                tuple(
                    ExportOperation(
                        r.id,
                        r.kind,
                        r.scope,
                        r.parameter_path,
                        tuple(r.requested_names),
                        tuple(
                            item["name"]
                            for item in r.result
                            if isinstance(item.get("name"), str)
                        ),
                        r.state,
                        r.failure_reason,
                        r.fault_code,
                        r.finished_at,
                    )
                    for r in rows
                ),
                len(recent_ids) > HISTORY_LIMIT,
            )
    except SQLAlchemyError:
        raise StorageUnavailable from None
