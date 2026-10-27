"""Leituras WAN/óptica com origem e último valor válido por interface."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_wan_optical_telemetry"
down_revision = "0006_query_scopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "query_operations",
        sa.Column(
            "requested_names", postgresql.JSONB(), server_default="[]", nullable=False
        ),
    )
    op.drop_constraint(
        op.f("ck_query_operations_scope"), "query_operations", type_="check"
    )
    op.create_check_constraint(
        "scope",
        "query_operations",
        "(scope = 'device_info' AND parameter_path IS NULL) OR (scope = 'discovery' AND kind = 'get_parameter_names' AND parameter_path IS NOT NULL) OR (scope IN ('wan','optical') AND kind = 'get_parameter_values' AND parameter_path IS NOT NULL)",
    )
    op.create_table(
        "cpe_telemetry_fields",
        sa.Column("cpe_id", sa.Uuid(), sa.ForeignKey("cpes.id"), primary_key=True),
        sa.Column("interface_path", sa.String(256), primary_key=True),
        sa.Column("field_name", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32)),
        sa.Column("source_parameters", postgresql.JSONB(), nullable=False),
        sa.Column("value_source_parameters", postgresql.JSONB(none_as_null=True)),
        sa.Column("source_parameter", sa.String(256), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "operation_id",
            sa.Uuid(),
            sa.ForeignKey("query_operations.id"),
            nullable=False,
        ),
        sa.Column("profile_id", sa.String(128), nullable=False),
        sa.Column("profile_version", sa.String(32), nullable=False),
        sa.Column("value", postgresql.JSONB(none_as_null=True)),
        sa.Column("unit", sa.String(8)),
        sa.Column("value_source_parameter", sa.String(256)),
        sa.Column("value_received_at", sa.DateTime(timezone=True)),
        sa.Column(
            "value_operation_id", sa.Uuid(), sa.ForeignKey("query_operations.id")
        ),
        sa.Column("value_profile_id", sa.String(128)),
        sa.Column("value_profile_version", sa.String(32)),
        sa.CheckConstraint(
            "(state = 'valid' AND reason IS NULL AND value IS NOT NULL) OR (state <> 'valid' AND reason IS NOT NULL)",
            name="observation",
        ),
        sa.CheckConstraint(
            "(value IS NULL AND unit IS NULL AND value_source_parameter IS NULL AND value_source_parameters IS NULL AND value_received_at IS NULL AND value_operation_id IS NULL AND value_profile_id IS NULL AND value_profile_version IS NULL) OR (value IS NOT NULL AND value_source_parameter IS NOT NULL AND value_source_parameters IS NOT NULL AND value_received_at IS NOT NULL AND value_operation_id IS NOT NULL AND value_profile_id IS NOT NULL AND value_profile_version IS NOT NULL)",
            name="value_provenance",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_parameters) = 'array' AND jsonb_array_length(source_parameters) BETWEEN 1 AND 16 AND (value_source_parameters IS NULL OR (jsonb_typeof(value_source_parameters) = 'array' AND jsonb_array_length(value_source_parameters) BETWEEN 1 AND 16))",
            name="sources",
        ),
        sa.CheckConstraint(
            "(kind = 'wan' AND field_name IN ('ipv4','ipv6','mac','dns','connection_status','service_list','transport_type')) OR (kind = 'optical' AND field_name IN ('rx_power','tx_power'))",
            name="field",
        ),
        sa.CheckConstraint(
            "state IN ('valid','unavailable','invalid','unverified')", name="state"
        ),
        sa.CheckConstraint("unit IS NULL OR unit = 'dBm'", name="unit"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM query_operations WHERE scope IN ('wan','optical')"
        )
    ):
        raise RuntimeError("Archive telemetry operations before downgrade")
    op.drop_table("cpe_telemetry_fields")
    op.drop_constraint(
        op.f("ck_query_operations_scope"), "query_operations", type_="check"
    )
    op.create_check_constraint(
        "scope",
        "query_operations",
        "(scope = 'device_info' AND parameter_path IS NULL) OR (scope = 'discovery' AND kind = 'get_parameter_names' AND parameter_path IS NOT NULL)",
    )
    op.drop_column("query_operations", "requested_names")
