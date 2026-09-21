"""Atomic PostgreSQL Inform persistence and session completion."""

from datetime import datetime
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from acs.domain.cpe.inform import Inform
from acs.domain.events.session import CwmpSession, InvalidSession, StorageUnavailable
from acs.infrastructure.database.cwmp_models import Cpe, CwmpEvent, CwmpSessionRow
from acs.infrastructure.database.database import Database


def session_value(row: CwmpSessionRow) -> CwmpSession:
    return CwmpSession(
        row.id,
        row.cpe_id,
        row.rpc_id,
        row.version,
        cast(Literal["awaiting_empty", "completed"], row.state),
        row.expires_at,
    )


def inventory_value(inform: Inform, suffix: str) -> str | None:
    allowed = {
        f"{root}.DeviceInfo.{suffix}" for root in ("Device", "InternetGatewayDevice")
    }
    return next(
        (
            p.value
            for p in inform.parameters
            if p.name in allowed and len(p.value) <= 256
        ),
        None,
    )


class PostgresInformRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def accept(
        self,
        inform: Inform,
        token_hash: str,
        now: datetime,
        expires_at: datetime,
        continuing: bool,
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
                    model=inventory_value(inform, "ModelName"),
                    firmware=inventory_value(inform, "SoftwareVersion"),
                    hardware=inventory_value(inform, "HardwareVersion"),
                )
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_cpes_identity",
                    set_={
                        "manufacturer": stmt.excluded.manufacturer,
                        "last_seen": func.greatest(
                            Cpe.last_seen, stmt.excluded.last_seen
                        ),
                        "model": func.coalesce(stmt.excluded.model, Cpe.model),
                        "firmware": func.coalesce(stmt.excluded.firmware, Cpe.firmware),
                        "hardware": func.coalesce(stmt.excluded.hardware, Cpe.hardware),
                    },
                ).returning(Cpe.id)
                cpe_id = (await db.execute(stmt)).scalar_one()
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
