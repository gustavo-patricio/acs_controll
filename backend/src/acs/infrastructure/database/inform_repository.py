"""Atomic PostgreSQL Inform persistence and session completion."""

from datetime import datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from acs.domain.cpe.inform import Inform
from acs.domain.cpe.inventory import NormalizedInventory
from acs.domain.events.session import CwmpSession, InvalidSession, StorageUnavailable
from acs.infrastructure.database.cwmp_models import (
    Cpe,
    CpeInventoryField,
    CwmpEvent,
    CwmpSessionRow,
)
from acs.infrastructure.database.database import Database


def session_value(row: CwmpSessionRow) -> CwmpSession:
    return CwmpSession(
        row.id,
        row.cpe_id,
        row.rpc_id,
        row.version,
        cast(Literal["awaiting_empty", "awaiting_response", "completed"], row.state),
        row.expires_at,
    )


class PostgresInformRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    async def update_inventory(
        db: AsyncSession, cpe_id: UUID, inventory: NormalizedInventory
    ) -> None:
        # The CPE upsert holds its row lock until commit. All writers of field
        # metadata must hold that same lock, so values and provenance move together.
        values: dict[str, str] = {}
        for name, observation in (
            ("model", inventory.model),
            ("firmware", inventory.firmware),
            ("hardware", inventory.hardware),
        ):
            if observation.state == "not_reported":
                continue
            row = await db.get(CpeInventoryField, (cpe_id, name))
            if row is not None and inventory.received_at <= row.observed_at:
                continue
            if row is None:
                row = CpeInventoryField(cpe_id=cpe_id, field_name=name)
                db.add(row)
            row.state = observation.state
            row.source_parameter = observation.source_parameter
            row.profile_id = inventory.profile_id
            row.profile_version = inventory.profile_version
            row.observed_at = inventory.received_at
            row.invalid_reason = observation.reason
            row.source_method = inventory.source_method
            row.operation_id = inventory.operation_id
            if observation.state == "valid":
                assert observation.value is not None
                values[name] = observation.value
                row.value_source_parameter = observation.source_parameter
                row.value_profile_id = inventory.profile_id
                row.value_profile_version = inventory.profile_version
                row.value_received_at = inventory.received_at
                row.value_source_method = inventory.source_method
                row.value_operation_id = inventory.operation_id
        if values:
            await db.execute(update(Cpe).where(Cpe.id == cpe_id).values(**values))

    async def accept(
        self,
        inform: Inform,
        token_hash: str,
        now: datetime,
        expires_at: datetime,
        continuing: bool,
        *,
        inventory: NormalizedInventory,
    ) -> CwmpSession:
        try:
            async with self.database.session() as db, db.begin():
                if continuing:
                    row = await db.scalar(
                        select(CwmpSessionRow)
                        .where(CwmpSessionRow.token_hash == token_hash)
                        .with_for_update()
                    )
                    if row is None:
                        raise InvalidSession
                    session_value(row).require_active(now)
                    cpe = await db.get(Cpe, row.cpe_id)
                    d = inform.device
                    if cpe is None or (
                        cpe.oui,
                        cpe.product_class,
                        cpe.serial_number,
                        row.rpc_id,
                        row.version,
                    ) != (
                        d.oui,
                        d.product_class,
                        d.serial_number,
                        inform.rpc_id,
                        inform.version,
                    ):
                        raise InvalidSession
                    return session_value(row)
                d = inform.device
                stmt = insert(Cpe).values(
                    id=uuid4(),
                    manufacturer=d.manufacturer,
                    oui=d.oui,
                    product_class=d.product_class,
                    serial_number=d.serial_number,
                    first_seen=now,
                    last_seen=now,
                    model=None,
                    firmware=None,
                    hardware=None,
                )
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_cpes_identity",
                    set_={
                        "manufacturer": stmt.excluded.manufacturer,
                        "last_seen": func.greatest(
                            Cpe.last_seen, stmt.excluded.last_seen
                        ),
                    },
                ).returning(Cpe.id)
                cpe_id = (await db.execute(stmt)).scalar_one()
                await self.update_inventory(db, cpe_id, inventory)
                row = CwmpSessionRow(
                    id=uuid4(),
                    cpe_id=cpe_id,
                    token_hash=token_hash,
                    rpc_id=inform.rpc_id,
                    version=inform.version,
                    state="awaiting_empty",
                    started_at=now,
                    expires_at=expires_at,
                    current_time=inform.current_time,
                    max_envelopes=inform.max_envelopes,
                    retry_count=inform.retry_count,
                    parameter_count=len(inform.parameters),
                )
                db.add(row)
                await db.flush()
                db.add_all(
                    CwmpEvent(
                        session_id=row.id,
                        position=i,
                        code=e.code,
                        command_key=e.command_key,
                    )
                    for i, e in enumerate(inform.events)
                )
                result = session_value(row)
            return result
        except SQLAlchemyError:
            raise StorageUnavailable from None

    async def complete(self, token_hash: str, now: datetime) -> CwmpSession:
        try:
            async with self.database.session() as db, db.begin():
                row = await db.scalar(
                    select(CwmpSessionRow)
                    .where(CwmpSessionRow.token_hash == token_hash)
                    .with_for_update()
                )
                if row is None:
                    raise InvalidSession
                session_value(row).require_active(now)
                row.state = "completed"
                row.completed_at = now
                result = session_value(row)
            return result
        except SQLAlchemyError:
            raise StorageUnavailable from None
