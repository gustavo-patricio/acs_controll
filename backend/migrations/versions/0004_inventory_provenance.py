"""Rastreabilidade por campo sem inferir origem ou datas de valores legados."""

import sqlalchemy as sa
from alembic import op

revision = "0004_inventory_provenance"
down_revision = "0003_shared_cpe_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cpe_inventory_fields",
        sa.Column("cpe_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("source_parameter", sa.String(256)),
        sa.Column("profile_id", sa.String(128), nullable=False),
        sa.Column("profile_version", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalid_reason", sa.String(32)),
        sa.Column("value_source_parameter", sa.String(256)),
        sa.Column("value_profile_id", sa.String(128)),
        sa.Column("value_profile_version", sa.String(32)),
        sa.Column("value_received_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["cpe_id"], ["cpes.id"]),
        sa.PrimaryKeyConstraint("cpe_id", "field_name"),
        sa.CheckConstraint(
            "field_name IN ('model', 'firmware', 'hardware')", name="field_name"
        ),
        sa.CheckConstraint(
            "(state = 'valid' AND invalid_reason IS NULL AND source_parameter IS NOT NULL) OR "
            "(state = 'invalid' AND invalid_reason IS NOT NULL AND invalid_reason IN "
            "('empty', 'too_long', 'control_character', 'conflicting_values'))",
            name="observation_state",
        ),
        sa.CheckConstraint(
            "(value_received_at IS NULL AND value_source_parameter IS NULL AND "
            "value_profile_id IS NULL AND value_profile_version IS NULL) OR "
            "(value_received_at IS NOT NULL AND value_source_parameter IS NOT NULL AND "
            "value_profile_id IS NOT NULL AND value_profile_version IS NOT NULL AND "
            "value_received_at <= observed_at)",
            name="value_provenance",
        ),
        sa.CheckConstraint(
            "state <> 'valid' OR (value_received_at IS NOT NULL AND "
            "value_received_at = observed_at AND value_source_parameter = source_parameter "
            "AND value_profile_id = profile_id AND value_profile_version = profile_version)",
            name="valid_provenance",
        ),
    )


def downgrade() -> None:
    # Removes provenance only; canonical CPE values and session history survive.
    op.drop_table("cpe_inventory_fields")
