"""Persist CPE identity, CWMP sessions and Inform events."""

import sqlalchemy as sa
from alembic import op

revision = "0001_cwmp_inform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cpes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("manufacturer", sa.String(64), nullable=False),
        sa.Column("oui", sa.String(6), nullable=False),
        sa.Column("product_class", sa.String(64), nullable=False),
        sa.Column("serial_number", sa.String(64), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(256)),
        sa.Column("firmware", sa.String(256)),
        sa.Column("hardware", sa.String(256)),
        sa.UniqueConstraint(
            "oui", "product_class", "serial_number", name="uq_cpes_identity"
        ),
    )
    op.create_table(
        "cwmp_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cpe_id", sa.Uuid(), sa.ForeignKey("cpes.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("rpc_id", sa.String(256), nullable=False),
        sa.Column("version", sa.String(8), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("current_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_envelopes", sa.BigInteger(), nullable=False),
        sa.Column("retry_count", sa.BigInteger(), nullable=False),
        sa.Column("parameter_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("token_hash"),
        sa.CheckConstraint("state IN ('awaiting_empty', 'completed')", name="state"),
    )
    op.create_index("ix_cwmp_sessions_cpe_id", "cwmp_sessions", ["cpe_id"])
    op.create_index("ix_cwmp_sessions_expires_at", "cwmp_sessions", ["expires_at"])
    op.create_table(
        "cwmp_events",
        sa.Column(
            "session_id", sa.Uuid(), sa.ForeignKey("cwmp_sessions.id"), primary_key=True
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("command_key", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cwmp_events")
    op.drop_index("ix_cwmp_sessions_expires_at", table_name="cwmp_sessions")
    op.drop_index("ix_cwmp_sessions_cpe_id", table_name="cwmp_sessions")
    op.drop_table("cwmp_sessions")
    op.drop_table("cpes")
