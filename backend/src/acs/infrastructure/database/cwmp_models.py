"""Relational persistence for the first CWMP flow."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from acs.infrastructure.database.base import Base


class Cpe(Base):
    __tablename__ = "cpes"
    __table_args__ = (
        UniqueConstraint(
            "oui", "product_class", "serial_number", name="uq_cpes_identity"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    manufacturer: Mapped[str] = mapped_column(String(64))
    oui: Mapped[str] = mapped_column(String(6))
    product_class: Mapped[str] = mapped_column(String(64))
    serial_number: Mapped[str] = mapped_column(String(64))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    model: Mapped[str | None] = mapped_column(String(256))
    firmware: Mapped[str | None] = mapped_column(String(256))
    hardware: Mapped[str | None] = mapped_column(String(256))


class CpeInventoryField(Base):
    """Latest observation and last valid value provenance, bounded per CPE."""

    __tablename__ = "cpe_inventory_fields"
    __table_args__ = (
        CheckConstraint(
            "field_name IN ('model', 'firmware', 'hardware')", name="field_name"
        ),
        CheckConstraint(
            "(state = 'valid' AND invalid_reason IS NULL AND source_parameter IS NOT NULL) OR "
            "(state = 'invalid' AND invalid_reason IS NOT NULL AND invalid_reason IN "
            "('empty', 'too_long', 'control_character', 'conflicting_values'))",
            name="observation_state",
        ),
        CheckConstraint(
            "(value_received_at IS NULL AND value_source_parameter IS NULL AND "
            "value_profile_id IS NULL AND value_profile_version IS NULL) OR "
            "(value_received_at IS NOT NULL AND value_source_parameter IS NOT NULL AND "
            "value_profile_id IS NOT NULL AND value_profile_version IS NOT NULL AND "
            "value_received_at <= observed_at)",
            name="value_provenance",
        ),
        CheckConstraint(
            "state <> 'valid' OR (value_received_at IS NOT NULL AND "
            "value_received_at = observed_at AND value_source_parameter = source_parameter "
            "AND value_profile_id = profile_id AND value_profile_version = profile_version)",
            name="valid_provenance",
        ),
        CheckConstraint(
            "(source_method = 'Inform' AND operation_id IS NULL) OR (source_method = 'GetParameterValues' AND operation_id IS NOT NULL)",
            name="method_origin",
        ),
        CheckConstraint(
            "(value_received_at IS NULL AND value_source_method IS NULL AND value_operation_id IS NULL) OR (value_received_at IS NOT NULL AND value_source_method IS NOT NULL AND ((value_source_method = 'Inform' AND value_operation_id IS NULL) OR (value_source_method = 'GetParameterValues' AND value_operation_id IS NOT NULL)))",
            name="value_method_origin",
        ),
    )

    cpe_id: Mapped[UUID] = mapped_column(ForeignKey("cpes.id"), primary_key=True)
    field_name: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    source_parameter: Mapped[str | None] = mapped_column(String(256))
    profile_id: Mapped[str] = mapped_column(String(128))
    profile_version: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invalid_reason: Mapped[str | None] = mapped_column(String(32))
    value_source_parameter: Mapped[str | None] = mapped_column(String(256))
    value_profile_id: Mapped[str | None] = mapped_column(String(128))
    value_profile_version: Mapped[str | None] = mapped_column(String(32))
    value_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_method: Mapped[str] = mapped_column(
        String(32), server_default=text("'Inform'")
    )
    operation_id: Mapped[UUID | None] = mapped_column(ForeignKey("query_operations.id"))
    value_source_method: Mapped[str | None] = mapped_column(String(32))
    value_operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("query_operations.id")
    )


class CwmpSessionRow(Base):
    __tablename__ = "cwmp_sessions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('awaiting_empty', 'awaiting_response', 'completed')",
            name="state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    cpe_id: Mapped[UUID] = mapped_column(ForeignKey("cpes.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    rpc_id: Mapped[str] = mapped_column(String(256))
    version: Mapped[str] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_envelopes: Mapped[int] = mapped_column(BigInteger)
    retry_count: Mapped[int] = mapped_column(BigInteger)
    parameter_count: Mapped[int]


class CwmpEvent(Base):
    __tablename__ = "cwmp_events"

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("cwmp_sessions.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    command_key: Mapped[str] = mapped_column(String(32))
