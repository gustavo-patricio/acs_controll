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


class CwmpSessionRow(Base):
    __tablename__ = "cwmp_sessions"
    __table_args__ = (
        CheckConstraint("state IN ('awaiting_empty', 'completed')", name="state"),
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
