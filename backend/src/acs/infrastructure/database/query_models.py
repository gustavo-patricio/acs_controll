"""Durable read requests and one auditable attempt per operation."""

from datetime import datetime
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acs.infrastructure.database.base import Base


class QueryOperationRow(Base):
    __tablename__ = "query_operations"
    __table_args__ = (
        CheckConstraint(
            "evidence_payload IS NULL OR (capture_evidence AND state IN ('succeeded','failed') AND octet_length(evidence_payload) <= 1048576)",
            name="evidence",
        ),
        CheckConstraint(
            "(scope = 'device_info' AND parameter_path IS NULL) OR (scope = 'discovery' AND kind = 'get_parameter_names' AND parameter_path IS NOT NULL) OR (scope IN ('wan','optical') AND kind = 'get_parameter_values' AND parameter_path IS NOT NULL)",
            name="scope",
        ),
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
    scope: Mapped[str] = mapped_column(String(32), server_default="device_info")
    parameter_path: Mapped[str | None] = mapped_column(String(256))
    capture_evidence: Mapped[bool] = mapped_column(Boolean, server_default="false")
    evidence_payload: Mapped[str | None] = mapped_column(Text)
    requested_names: Mapped[list[str]] = mapped_column(
        JSONB, server_default="[]", default=list
    )
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


class ParameterCatalogRow(Base):
    __tablename__ = "cpe_parameter_catalog"
    cpe_id: Mapped[UUID] = mapped_column(ForeignKey("cpes.id"), primary_key=True)
    path: Mapped[str] = mapped_column(String(256), primary_key=True)
    is_object: Mapped[bool] = mapped_column(Boolean)
    writable: Mapped[bool] = mapped_column(Boolean)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operation_id: Mapped[UUID] = mapped_column(ForeignKey("query_operations.id"))


class ParameterValueRow(Base):
    __tablename__ = "cpe_parameter_values"
    cpe_id: Mapped[UUID] = mapped_column(ForeignKey("cpes.id"), primary_key=True)
    path: Mapped[str] = mapped_column(String(256), primary_key=True)
    xml_type: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operation_id: Mapped[UUID] = mapped_column(ForeignKey("query_operations.id"))


class CollectionConfig(TypedDict):
    kind: Literal["discovery", "values"]
    root: str | None
    parameter_names: list[str]
    max_depth: int
    max_rpcs: int
    max_entries: int
    batch_size: int


class CollectionStep(TypedDict):
    depth: int
    path: NotRequired[str]
    names: NotRequired[list[str]]


class CollectionJobRow(Base):
    __tablename__ = "collection_jobs"
    __table_args__ = (
        UniqueConstraint("cpe_id", "idempotency_key"),
        Index(
            "uq_collection_active_cpe",
            "cpe_id",
            unique=True,
            postgresql_where=text("state = 'running'"),
        ),
        CheckConstraint("state IN ('running', 'succeeded', 'partial')", name="state"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    cpe_id: Mapped[UUID] = mapped_column(ForeignKey("cpes.id"))
    idempotency_key: Mapped[UUID]
    requested_by: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    spec: Mapped[CollectionConfig] = mapped_column(JSONB)
    frontier: Mapped[list[CollectionStep]] = mapped_column(JSONB)
    current_operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("query_operations.id")
    )
    current_depth: Mapped[int]
    rpc_count: Mapped[int]
    completed_count: Mapped[int]
    entry_count: Mapped[int]
    failures: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    limits_reached: Mapped[list[str]] = mapped_column(JSONB)
    operation_ids: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProfileValidationRow(Base):
    __tablename__ = "profile_validations"
    cpe_id: Mapped[UUID] = mapped_column(ForeignKey("cpes.id"), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(ForeignKey("query_operations.id"))
    idempotency_key: Mapped[UUID]
    expected_firmware: Mapped[str] = mapped_column(String(256))
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
