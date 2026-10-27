"""Telemetry persistence inside the operation transaction and canonical views."""

from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acs.domain.cpe.telemetry import (
    CpeTelemetryView,
    OpticalFields,
    OpticalInterface,
    TelemetryCollection,
    TelemetryFieldView,
    TelemetryProvenance,
    TelemetryReason,
    TelemetryState,
    WanFields,
    WanInterface,
)
from acs.infrastructure.database.telemetry_models import CpeTelemetryField


async def update_telemetry(
    db: AsyncSession, cpe_id: UUID, collection: TelemetryCollection
) -> None:
    # Caller holds the CPE lock; writes commit with the operation and session.
    for observation in collection.fields:
        key = (cpe_id, collection.interface_path, observation.name)
        row = await db.get(CpeTelemetryField, key)
        if row is not None and row.observed_at >= collection.received_at:
            continue
        if row is None:
            row = CpeTelemetryField(
                cpe_id=cpe_id,
                interface_path=collection.interface_path,
                field_name=observation.name,
                kind=collection.kind,
            )
            db.add(row)
        row.source_parameters = list(
            observation.source_parameters or (observation.source_parameter,)
        )
        row.state, row.reason = observation.state, observation.reason
        row.source_parameter, row.observed_at, row.operation_id = (
            observation.source_parameter,
            collection.received_at,
            collection.operation_id,
        )
        row.profile_id, row.profile_version = (
            collection.profile_id,
            collection.profile_version,
        )
        if observation.state == "valid":
            row.value = (
                list(observation.value)
                if isinstance(observation.value, tuple)
                else observation.value
            )
            row.unit = observation.unit
            row.value_source_parameters = row.source_parameters
            (
                row.value_source_parameter,
                row.value_received_at,
                row.value_operation_id,
            ) = row.source_parameter, row.observed_at, row.operation_id
            row.value_profile_id, row.value_profile_version = (
                row.profile_id,
                row.profile_version,
            )


def field_view(row: CpeTelemetryField) -> TelemetryFieldView:
    provenance = None
    if (
        row.value_received_at is not None
        and row.value_source_parameter is not None
        and row.value_profile_id is not None
        and row.value_profile_version is not None
    ):
        provenance = TelemetryProvenance(
            row.value_source_parameter,
            row.value_profile_id,
            row.value_profile_version,
            row.value_received_at,
            "GetParameterValues",
            row.value_operation_id,
            tuple(row.value_source_parameters or ()),
        )
    return TelemetryFieldView(
        tuple(row.value) if isinstance(row.value, list) else row.value,
        cast(Literal["dBm"] | None, row.unit),
        provenance,
        cast(TelemetryState, row.state),
        row.observed_at,
        row.source_parameter,
        row.operation_id,
        cast(TelemetryReason | None, row.reason),
        tuple(row.source_parameters),
    )


async def read_telemetry(db: AsyncSession, cpe_id: UUID) -> CpeTelemetryView:
    rows = (
        await db.scalars(
            select(CpeTelemetryField)
            .where(CpeTelemetryField.cpe_id == cpe_id)
            .order_by(CpeTelemetryField.interface_path, CpeTelemetryField.field_name)
        )
    ).all()
    wan: dict[str, dict[str, TelemetryFieldView]] = {}
    optical: dict[str, dict[str, TelemetryFieldView]] = {}
    for row in rows:
        target = wan if row.kind == "wan" else optical
        target.setdefault(row.interface_path, {})[row.field_name] = field_view(row)
    return CpeTelemetryView(
        cpe_id,
        tuple(WanInterface(path, WanFields(**fields)) for path, fields in wan.items()),
        tuple(
            OpticalInterface(path, OpticalFields(**fields))
            for path, fields in optical.items()
        ),
    )
