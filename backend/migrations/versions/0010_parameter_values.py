"""Últimos valores GPV permitidos, preservando tipo XML e origem."""

import sqlalchemy as sa
from alembic import op

revision = "0010_parameter_values"
down_revision = "0009_parameter_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cpe_parameter_values",
        sa.Column("cpe_id", sa.Uuid(), sa.ForeignKey("cpes.id"), primary_key=True),
        sa.Column("path", sa.String(256), primary_key=True),
        sa.Column("xml_type", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "operation_id",
            sa.Uuid(),
            sa.ForeignKey("query_operations.id"),
            nullable=False,
        ),
    )
    # Historical normalized results do not contain the reported XML type.
    # Do not invent a type or reconstruct a lost original string.


def downgrade() -> None:
    op.drop_table("cpe_parameter_values")
