"""Evidência parcial de consultas, opt-in e persistida com a operação."""

import sqlalchemy as sa
from alembic import op

revision = "0008_query_evidence"
down_revision = "0007_wan_optical_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "query_operations",
        sa.Column(
            "capture_evidence", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    op.add_column("query_operations", sa.Column("evidence_payload", sa.Text()))
    op.create_check_constraint(
        "evidence",
        "query_operations",
        "evidence_payload IS NULL OR (capture_evidence AND state IN ('succeeded','failed') AND octet_length(evidence_payload) <= 1048576)",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM query_operations WHERE capture_evidence")
    ):
        raise RuntimeError("Archive evidence operations before downgrade")
    op.drop_constraint(
        op.f("ck_query_operations_evidence"), "query_operations", type_="check"
    )
    op.drop_column("query_operations", "evidence_payload")
    op.drop_column("query_operations", "capture_evidence")
