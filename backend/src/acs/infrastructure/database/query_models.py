"""Durable read requests and one auditable attempt per operation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acs.infrastructure.database.base import Base


class QueryOperationRow(Base):
    __tablename__ = "query_operations"
    __table_args__ = (
        UniqueConstraint("cpe_id", "idempotency_key", name="uq_query_idempotency"),
        CheckConstraint(
            "kind IN ('get_parameter_names', 'get_parameter_values')", name="kind"
        ),
        CheckConstraint(
            "state IN ('pending', 'dispatched', 'succeeded', 'failed', 'expired')",
            name="state",
        ),
        CheckConstraint(
            "(request_source = 'api' AND requested_by IS NOT NULL AND local_actor IS NULL) OR (request_source = 'local' AND requested_by IS NULL AND local_actor IS NOT NULL)",
            name="actor",
        ),
        Index(
            "uq_query_active_cpe",
            "cpe_id",
            unique=True,
            postgresql_where=text("state IN ('pending', 'dispatched')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    cpe_id: Mapped[UUID] = mapped_column(ForeignKey("cpes.id"))
    idempotency_key: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16))
    profile_id: Mapped[str] = mapped_column(String(128))
    profile_version: Mapped[str] = mapped_column(String(32))
    expected_firmware: Mapped[str] = mapped_column(String(256))
    requested_by: Mapped[UUID | None]
    request_source: Mapped[str] = mapped_column(String(8))
    local_actor: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    fault_code: Mapped[int | None]
    result: Mapped[list[dict[str, str | bool | None]]] = mapped_column(
        JSONB, default=list
    )


class QueryAttemptRow(Base):
    __tablename__ = "query_attempts"
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("query_operations.id"), primary_key=True
    )
    session_id: Mapped[UUID] = mapped_column(ForeignKey("cwmp_sessions.id"), index=True)
    rpc_id: Mapped[str] = mapped_column(String(64), unique=True)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_digest: Mapped[str | None] = mapped_column(String(64))
