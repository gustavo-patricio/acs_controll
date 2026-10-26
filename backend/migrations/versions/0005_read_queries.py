"""Operações de leitura, tentativas e origem de observações coletadas por RPC."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_read_queries"
down_revision = "0004_inventory_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cpe_id", sa.Uuid(), sa.ForeignKey("cpes.id"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("profile_id", sa.String(128), nullable=False),
        sa.Column("profile_version", sa.String(32), nullable=False),
        sa.Column("expected_firmware", sa.String(256), nullable=False),
        sa.Column("requested_by", sa.Uuid()),
        sa.Column("request_source", sa.String(8), nullable=False),
        sa.Column("local_actor", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.String(64)),
        sa.Column("fault_code", sa.Integer()),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("cpe_id", "idempotency_key", name="uq_query_idempotency"),
        sa.CheckConstraint(
            "kind IN ('get_parameter_names', 'get_parameter_values')", name="kind"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'dispatched', 'succeeded', 'failed', 'expired')",
            name="state",
        ),
        sa.CheckConstraint(
            "(request_source = 'api' AND requested_by IS NOT NULL AND local_actor IS NULL) OR (request_source = 'local' AND requested_by IS NULL AND local_actor IS NOT NULL)",
            name="actor",
        ),
    )
    op.create_index(
        "uq_query_active_cpe",
        "query_operations",
        ["cpe_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('pending', 'dispatched')"),
    )
    op.create_index(
        "ix_query_operations_expires_at", "query_operations", ["expires_at"]
    )
    op.create_table(
        "query_attempts",
        sa.Column(
            "operation_id",
            sa.Uuid(),
            sa.ForeignKey("query_operations.id"),
            primary_key=True,
        ),
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("cwmp_sessions.id"), nullable=False
        ),
        sa.Column("rpc_id", sa.String(64), unique=True, nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_digest", sa.String(64)),
    )
    op.create_index("ix_query_attempts_session_id", "query_attempts", ["session_id"])
    op.drop_constraint(op.f("ck_cwmp_sessions_state"), "cwmp_sessions", type_="check")
    op.create_check_constraint(
        "state",
        "cwmp_sessions",
        "state IN ('awaiting_empty', 'awaiting_response', 'completed')",
    )
    op.add_column(
        "cpe_inventory_fields",
        sa.Column(
            "source_method", sa.String(32), nullable=False, server_default="Inform"
        ),
    )
    op.add_column(
        "cpe_inventory_fields",
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("query_operations.id")),
    )
    op.add_column(
        "cpe_inventory_fields", sa.Column("value_source_method", sa.String(32))
    )
    op.add_column(
        "cpe_inventory_fields",
        sa.Column(
            "value_operation_id", sa.Uuid(), sa.ForeignKey("query_operations.id")
        ),
    )
    # Before this revision the only producer of these metadata was Inform.
    op.execute(
        "UPDATE cpe_inventory_fields SET value_source_method = 'Inform' WHERE value_received_at IS NOT NULL"
    )
    op.create_check_constraint(
        "method_origin",
        "cpe_inventory_fields",
        "(source_method = 'Inform' AND operation_id IS NULL) OR (source_method = 'GetParameterValues' AND operation_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "value_method_origin",
        "cpe_inventory_fields",
        "(value_received_at IS NULL AND value_source_method IS NULL AND value_operation_id IS NULL) OR (value_received_at IS NOT NULL AND value_source_method IS NOT NULL AND ((value_source_method = 'Inform' AND value_operation_id IS NULL) OR (value_source_method = 'GetParameterValues' AND value_operation_id IS NOT NULL)))",
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM query_operations")):
        raise RuntimeError(
            "Read operation history must be explicitly archived before downgrade"
        )
    for name in ("method_origin", "value_method_origin"):
        op.drop_constraint(
            op.f("ck_cpe_inventory_fields_" + name),
            "cpe_inventory_fields",
            type_="check",
        )
    for name in (
        "value_operation_id",
        "value_source_method",
        "operation_id",
        "source_method",
    ):
        op.drop_column("cpe_inventory_fields", name)
    op.drop_constraint(op.f("ck_cwmp_sessions_state"), "cwmp_sessions", type_="check")
    op.create_check_constraint(
        "state", "cwmp_sessions", "state IN ('awaiting_empty', 'completed')"
    )
    op.drop_table("query_attempts")
    op.drop_table("query_operations")
