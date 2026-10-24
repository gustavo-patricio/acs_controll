"""Local authentication accounts with pre-enrolled CPE identities."""

import sqlalchemy as sa
from alembic import op

revision = "0002_auth_accounts"
down_revision = "0001_cwmp_inform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("oui", sa.String(6)),
        sa.Column("product_class", sa.String(64)),
        sa.Column("serial_number", sa.String(64)),
        sa.UniqueConstraint("kind", "username", name="uq_auth_account_username"),
        sa.UniqueConstraint(
            "oui", "product_class", "serial_number", name="uq_auth_account_device"
        ),
        sa.CheckConstraint("kind IN ('admin', 'cpe')", name="account_kind"),
        sa.CheckConstraint(
            "(kind = 'admin' AND role IN ('administrator', 'support', 'pending') "
            "AND oui IS NULL AND product_class IS NULL AND serial_number IS NULL) OR "
            "(kind = 'cpe' AND role = 'device' AND oui IS NOT NULL "
            "AND product_class IS NOT NULL AND serial_number IS NOT NULL)",
            name="account_identity",
        ),
    )


def downgrade() -> None:
    op.drop_table("auth_accounts")
