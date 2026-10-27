"""PostgreSQL inventory queries with explicit field mapping."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from acs.application.services.cpe_inventory import (
    CpeNotFound,
    CpePage,
    CpeSummary,
    EventPage,
    EventSummary,
    InventoryUnavailable,
    SessionPage,
    SessionSummary,
)
from acs.domain.cpe.inventory import InvalidReason
from acs.domain.cpe.inventory_view import (
    CpeInventoryView,
    InventoryFields,
    InventoryFieldView,
    LastObservation,
    ValueProvenance,
)
from acs.domain.cpe.telemetry import CpeTelemetryView
from acs.infrastructure.database.cwmp_models import (
    Cpe,
    CpeInventoryField,
    CwmpEvent,
    CwmpSessionRow,
)
from acs.infrastructure.database.database import Database
from acs.infrastructure.database.telemetry import read_telemetry


class PostgresCpeInventoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _summary(row: Cpe) -> CpeSummary:
        return CpeSummary(
            id=row.id,
            manufacturer=row.manufacturer,
            oui=row.oui,
            product_class=row.product_class,
            serial_number=row.serial_number,
            model=row.model,
            firmware=row.firmware,
            hardware=row.hardware,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
        )

    @asynccontextmanager
    async def _cpe_snapshot(self, cpe_id: UUID) -> AsyncGenerator[AsyncSession]:
        try:
            async with self.database.session() as session:
                await session.connection(
                    execution_options={"isolation_level": "REPEATABLE READ"}
                )
                if await session.scalar(select(Cpe.id).where(Cpe.id == cpe_id)) is None:
                    raise CpeNotFound
                yield session
        except SQLAlchemyError:
            raise InventoryUnavailable from None

    async def get_cpe(self, cpe_id: UUID) -> CpeSummary:
        async with self._cpe_snapshot(cpe_id) as session:
            row = (await session.scalars(select(Cpe).where(Cpe.id == cpe_id))).one()
            return self._summary(row)

    @staticmethod
    def _field(value: str | None, row: CpeInventoryField | None) -> InventoryFieldView:
        if row is None:
            return InventoryFieldView(value)
        provenance = None
        if row.value_received_at is not None:
            # Database constraints require all members of the value group together.
            assert row.value_source_parameter is not None
            assert row.value_profile_id is not None
            assert row.value_profile_version is not None
            provenance = ValueProvenance(
                row.value_source_parameter,
                row.value_profile_id,
                row.value_profile_version,
                row.value_received_at,
                cast(Literal["Inform", "GetParameterValues"], row.value_source_method),
                row.value_operation_id,
            )
        observation = LastObservation(
            cast(Literal["valid", "invalid"], row.state),
            row.source_parameter,
            row.profile_id,
            row.profile_version,
            row.observed_at,
            cast(InvalidReason | None, row.invalid_reason),
            cast(Literal["Inform", "GetParameterValues"], row.source_method),
            row.operation_id,
        )
        return InventoryFieldView(value, provenance, observation)

    async def get_inventory(self, cpe_id: UUID) -> CpeInventoryView:
        # Values and metadata must share one snapshot while Informs keep arriving.
        async with self._cpe_snapshot(cpe_id) as session:
            cpe = (await session.scalars(select(Cpe).where(Cpe.id == cpe_id))).one()
            metadata = {
                row.field_name: row
                for row in (
                    await session.scalars(
                        select(CpeInventoryField).where(
                            CpeInventoryField.cpe_id == cpe_id
                        )
                    )
                ).all()
            }
            return CpeInventoryView(
                cpe_id,
                cpe.last_seen,
                InventoryFields(
                    model=self._field(cpe.model, metadata.get("model")),
                    firmware=self._field(cpe.firmware, metadata.get("firmware")),
                    hardware=self._field(cpe.hardware, metadata.get("hardware")),
                ),
            )

    async def get_telemetry(self, cpe_id: UUID) -> CpeTelemetryView:
        async with self._cpe_snapshot(cpe_id) as session:
            return await read_telemetry(session, cpe_id)

    async def list_sessions(self, cpe_id: UUID, limit: int, offset: int) -> SessionPage:
        async with self._cpe_snapshot(cpe_id) as session:
            total = await session.scalar(
                select(func.count())
                .select_from(CwmpSessionRow)
                .where(CwmpSessionRow.cpe_id == cpe_id)
            )
            # Select only public fields; never fetch the session token for this view.
            rows = (
                await session.execute(
                    select(
                        CwmpSessionRow.id,
                        CwmpSessionRow.cpe_id,
                        CwmpSessionRow.version,
                        CwmpSessionRow.state,
                        CwmpSessionRow.started_at,
                        CwmpSessionRow.expires_at,
                        CwmpSessionRow.completed_at,
                    )
                    .where(CwmpSessionRow.cpe_id == cpe_id)
                    .order_by(CwmpSessionRow.started_at.desc(), CwmpSessionRow.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return SessionPage(
                items=tuple(SessionSummary(*row) for row in rows),
                total=total or 0,
                limit=limit,
                offset=offset,
            )

    async def list_events(self, cpe_id: UUID, limit: int, offset: int) -> EventPage:
        async with self._cpe_snapshot(cpe_id) as session:
            total = await session.scalar(
                select(func.count())
                .select_from(CwmpEvent)
                .join(CwmpSessionRow, CwmpSessionRow.id == CwmpEvent.session_id)
                .where(CwmpSessionRow.cpe_id == cpe_id)
            )
            rows = (
                await session.execute(
                    select(
                        CwmpEvent.session_id,
                        CwmpEvent.position,
                        CwmpEvent.code,
                        CwmpSessionRow.started_at,
                    )
                    .join(CwmpSessionRow, CwmpSessionRow.id == CwmpEvent.session_id)
                    .where(CwmpSessionRow.cpe_id == cpe_id)
                    .order_by(
                        CwmpSessionRow.started_at.desc(),
                        CwmpSessionRow.id,
                        CwmpEvent.position,
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return EventPage(
                items=tuple(EventSummary(*row) for row in rows),
                total=total or 0,
                limit=limit,
                offset=offset,
            )

    async def list_cpes(
        self,
        limit: int,
        offset: int,
        manufacturer: str | None,
        serial_number: str | None,
    ) -> CpePage:
        query = select(Cpe)
        count = select(func.count()).select_from(Cpe)
        if manufacturer is not None:
            query = query.where(Cpe.manufacturer == manufacturer)
            count = count.where(Cpe.manufacturer == manufacturer)
        if serial_number is not None:
            query = query.where(Cpe.serial_number == serial_number)
            count = count.where(Cpe.serial_number == serial_number)
        try:
            async with self.database.session() as session:
                # Keep the count and page on one snapshot while Inform writes continue.
                await session.connection(
                    execution_options={"isolation_level": "REPEATABLE READ"}
                )
                total = await session.scalar(count)
                rows = (
                    await session.scalars(
                        query.order_by(Cpe.last_seen.desc(), Cpe.id)
                        .limit(limit)
                        .offset(offset)
                    )
                ).all()
                return CpePage(
                    items=tuple(self._summary(row) for row in rows),
                    total=total or 0,
                    limit=limit,
                    offset=offset,
                )
        except SQLAlchemyError:
            raise InventoryUnavailable from None
