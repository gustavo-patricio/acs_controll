"""Explicit per-CPE draft validation and approval."""

import sqlalchemy as sa
from alembic import op

revision = "0012_profile_validations"
down_revision = "0011_collection_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_validations",
        sa.Column("cpe_id", sa.Uuid(), sa.ForeignKey("cpes.id"), primary_key=True),
        sa.Column(
            "operation_id",
            sa.Uuid(),
            sa.ForeignKey("query_operations.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("expected_firmware", sa.String(256), nullable=False),
        sa.Column("approved_by", sa.Uuid()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("profile_validations")
