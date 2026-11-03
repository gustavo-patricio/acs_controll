"""Store selected private observations for CPE homologation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_private_observations"
down_revision = "0012_profile_validations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "query_operations",
        sa.Column("private_observations", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("query_operations", "private_observations")
