"""Bounded current telemetry; invalid samples preserve the last valid value."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acs.infrastructure.database.base import Base


class CpeTelemetryField(Base):
    __tablename__ = "cpe_telemetry_fields"
    __table_args__ = (
        CheckConstraint(
            "(state = 'valid' AND reason IS NULL AND value IS NOT NULL) OR (state <> 'valid' AND reason IS NOT NULL)",
            name="observation",
        ),
        CheckConstraint(
            "(value IS NULL AND unit IS NULL AND value_source_parameter IS NULL AND value_source_parameters IS NULL AND value_received_at IS NULL AND value_operation_id IS NULL AND value_profile_id IS NULL AND value_profile_version IS NULL) OR (value IS NOT NULL AND value_source_parameter IS NOT NULL AND value_source_parameters IS NOT NULL AND value_received_at IS NOT NULL AND value_operation_id IS NOT NULL AND value_profile_id IS NOT NULL AND value_profile_version IS NOT NULL)",
            name="value_provenance",
        ),
        CheckConstraint(
            "jsonb_typeof(source_parameters) = 'array' AND jsonb_array_length(source_parameters) BETWEEN 1 AND 16 AND (value_source_parameters IS NULL OR (jsonb_typeof(value_source_parameters) = 'array' AND jsonb_array_length(value_source_parameters) BETWEEN 1 AND 16))",
            name="sources",
        ),
        CheckConstraint(
            "(kind = 'wan' AND field_name IN ('ipv4','ipv6','mac','dns','connection_status','service_list','transport_type')) OR (kind = 'optical' AND field_name IN ('rx_power','tx_power'))",
            name="field",
        ),
        CheckConstraint(
            "state IN ('valid','unavailable','invalid','unverified')", name="state"
        ),
        CheckConstraint("unit IS NULL OR unit = 'dBm'", name="unit"),
    )
    cpe_id: Mapped[UUID] = mapped_column(ForeignKey("cpes.id"), primary_key=True)
    interface_path: Mapped[str] = mapped_column(String(256), primary_key=True)
    field_name: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(32))
    source_parameters: Mapped[list[str]] = mapped_column(JSONB, default=list)
    value_source_parameters: Mapped[list[str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    source_parameter: Mapped[str] = mapped_column(String(256))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operation_id: Mapped[UUID] = mapped_column(ForeignKey("query_operations.id"))
    profile_id: Mapped[str] = mapped_column(String(128))
    profile_version: Mapped[str] = mapped_column(String(32))
    value: Mapped[str | float | list[str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    unit: Mapped[str | None] = mapped_column(String(8))
    value_source_parameter: Mapped[str | None] = mapped_column(String(256))
    value_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    value_operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("query_operations.id")
    )
    value_profile_id: Mapped[str | None] = mapped_column(String(128))
    value_profile_version: Mapped[str | None] = mapped_column(String(32))
