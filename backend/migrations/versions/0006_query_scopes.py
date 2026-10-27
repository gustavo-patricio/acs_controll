"""Escopos persistidos para descoberta limitada de interfaces e objetos ópticos."""

import sqlalchemy as sa
from alembic import op

revision = "0006_query_scopes"
down_revision = "0005_read_queries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "query_operations",
        sa.Column("scope", sa.String(32), server_default="device_info", nullable=False),
    )
    op.add_column("query_operations", sa.Column("parameter_path", sa.String(256)))
    op.create_check_constraint(
        "scope",
        "query_operations",
        "(scope = 'device_info' AND parameter_path IS NULL) OR (scope = 'discovery' AND kind = 'get_parameter_names' AND parameter_path IS NOT NULL)",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM query_operations WHERE scope <> 'device_info'")
    ):
        raise RuntimeError("Archive scoped operations before downgrade")
    op.drop_constraint(
        op.f("ck_query_operations_scope"), "query_operations", type_="check"
    )
    op.drop_column("query_operations", "parameter_path")
    op.drop_column("query_operations", "scope")
