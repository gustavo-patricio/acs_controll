"""Catálogo estrutural de nomes observados por CPE, sem tipo ou valor."""

import sqlalchemy as sa
from alembic import op

revision = "0009_parameter_catalog"
down_revision = "0008_query_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cpe_parameter_catalog",
        sa.Column("cpe_id", sa.Uuid(), sa.ForeignKey("cpes.id"), primary_key=True),
        sa.Column("path", sa.String(256), primary_key=True),
        sa.Column("is_object", sa.Boolean(), nullable=False),
        sa.Column("writable", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "operation_id",
            sa.Uuid(),
            sa.ForeignKey("query_operations.id"),
            nullable=False,
        ),
    )
    # Only recover names actually retained by previous successful operations.
    op.execute("""
        INSERT INTO cpe_parameter_catalog
            (cpe_id, path, is_object, writable, observed_at, operation_id)
        SELECT DISTINCT ON (q.cpe_id, item->>'name')
            q.cpe_id, item->>'name', right(item->>'name', 1) = '.',
            (item->>'writable')::boolean, q.finished_at, q.id
        FROM query_operations q CROSS JOIN LATERAL jsonb_array_elements(q.result) item
        WHERE q.kind = 'get_parameter_names' AND q.state = 'succeeded'
          AND q.finished_at IS NOT NULL
          AND jsonb_typeof(item->'name') = 'string'
          AND length(item->>'name') BETWEEN 1 AND 256
          AND jsonb_typeof(item->'writable') = 'boolean'
        ORDER BY q.cpe_id, item->>'name', q.finished_at DESC, q.id DESC
    """)


def downgrade() -> None:
    op.drop_table("cpe_parameter_catalog")
