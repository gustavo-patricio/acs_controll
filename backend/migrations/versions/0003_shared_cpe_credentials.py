"""Explicit shared CPE provisioning profiles; existing accounts stay individual."""

import sqlalchemy as sa
from alembic import op

revision = "0003_shared_cpe_credentials"
down_revision = "0002_auth_accounts"
branch_labels = None
depends_on = None

INDIVIDUAL_IDENTITY = (
    "(kind = 'admin' AND role IN ('administrator', 'support', 'pending') "
    "AND oui IS NULL AND product_class IS NULL AND serial_number IS NULL) OR "
    "(kind = 'cpe' AND role = 'device' AND oui IS NOT NULL "
    "AND product_class IS NOT NULL AND serial_number IS NOT NULL)"
)
SHARED_IDENTITY = (
    "(kind = 'admin' AND role IN ('administrator', 'support', 'pending') "
    "AND shared_cpe = false AND oui IS NULL AND product_class IS NULL AND serial_number IS NULL) OR "
    "(kind = 'cpe' AND role = 'device' AND ((shared_cpe = false AND oui IS NOT NULL "
    "AND product_class IS NOT NULL AND serial_number IS NOT NULL) OR "
    "(shared_cpe = true AND oui IS NULL AND product_class IS NULL AND serial_number IS NULL)))"
)


def upgrade() -> None:
    op.add_column(
        "auth_accounts",
        sa.Column(
            "shared_cpe", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.drop_constraint(
        op.f("ck_auth_accounts_account_identity"), "auth_accounts", type_="check"
    )
    op.create_check_constraint("account_identity", "auth_accounts", SHARED_IDENTITY)


def downgrade() -> None:
    # Do not silently delete accounts or invent identities when removing this feature.
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM auth_accounts WHERE shared_cpe = true")
    ):
        raise RuntimeError(
            "Shared CPE accounts must be explicitly migrated or removed before downgrade"
        )
    op.drop_constraint(
        op.f("ck_auth_accounts_account_identity"), "auth_accounts", type_="check"
    )
    op.create_check_constraint("account_identity", "auth_accounts", INDIVIDUAL_IDENTITY)
    op.drop_column("auth_accounts", "shared_cpe")
