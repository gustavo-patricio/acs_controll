"""Trabalhos limitados de descoberta e leitura em lotes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011_collection_jobs"
down_revision = "0010_parameter_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cpe_id", sa.Uuid(), sa.ForeignKey("cpes.id"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("spec", JSONB, nullable=False),
        sa.Column("frontier", JSONB, nullable=False),
        sa.Column(
            "current_operation_id", sa.Uuid(), sa.ForeignKey("query_operations.id")
        ),
        sa.Column("current_depth", sa.Integer(), nullable=False),
        sa.Column("rpc_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("failures", JSONB, nullable=False),
        sa.Column("limits_reached", JSONB, nullable=False),
        sa.Column("operation_ids", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("cpe_id", "idempotency_key"),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'partial')", name="state"
        ),
    )
    op.create_index(
        "uq_collection_active_cpe",
        "collection_jobs",
        ["cpe_id"],
        unique=True,
        postgresql_where=sa.text("state = 'running'"),
    )


def downgrade() -> None:
    op.drop_table("collection_jobs")
