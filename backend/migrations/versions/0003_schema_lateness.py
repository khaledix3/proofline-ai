"""Versioned input contracts, arrival evidence and two new incident types."""
from alembic import op
import sqlalchemy as sa

revision = "0003_schema_lateness"
down_revision = "0002_incidents"
branch_labels = None
depends_on = None

OLD_REASONS = "reason_code IN ('INVALID_COLUMNS', 'INVALID_ORDER_ID', 'INVALID_CUSTOMER_ID', 'INVALID_QUANTITY', 'INVALID_UNIT_PRICE', 'INVALID_ORDER_DATE', 'DUPLICATE_ORDER_ID')"
NEW_REASONS = "reason_code IN ('INVALID_COLUMNS', 'INVALID_ORDER_ID', 'INVALID_CUSTOMER_ID', 'INVALID_QUANTITY', 'INVALID_UNIT_PRICE', 'INVALID_ORDER_DATE', 'INVALID_EVENT_TIMESTAMP', 'DUPLICATE_ORDER_ID')"
OLD_TYPES = "incident_type IN ('DUPLICATE_RECORDS', 'MISSING_RECORDS')"
NEW_TYPES = "incident_type IN ('DUPLICATE_RECORDS', 'MISSING_RECORDS', 'SCHEMA_DRIFT', 'LATE_ARRIVING_DATA')"
OLD_STAGES = "(incident_type = 'DUPLICATE_RECORDS' AND stage = 'raw_to_clean') OR (incident_type = 'MISSING_RECORDS' AND stage = 'clean_to_reporting')"
NEW_STAGES = OLD_STAGES + " OR (incident_type = 'SCHEMA_DRIFT' AND stage = 'source_to_raw') OR (incident_type = 'LATE_ARRIVING_DATA' AND stage = 'source_to_ingestion')"


def replace_checks(reasons, types, stages):
    with op.batch_alter_table("rejected_orders") as batch:
        batch.drop_constraint("known_rejection_reason", type_="check")
        batch.create_check_constraint("known_rejection_reason", reasons)
    with op.batch_alter_table("incidents") as batch:
        batch.drop_constraint("known_incident_type", type_="check")
        batch.drop_constraint("incident_stage_matches_type", type_="check")
        batch.create_check_constraint("known_incident_type", types)
        batch.create_check_constraint("incident_stage_matches_type", stages)


def upgrade():
    replace_checks(NEW_REASONS, NEW_TYPES, NEW_STAGES)
    op.create_table("pipeline_contracts",
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("expected_schema", sa.JSON(), nullable=False),
        sa.Column("actual_header", sa.JSON(), nullable=False),
        sa.Column("column_policy", sa.String(16), nullable=False),
        sa.Column("monitor_schema", sa.Boolean(), nullable=False),
        sa.Column("lateness_threshold_seconds", sa.Integer(), nullable=True),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_source", sa.String(16), nullable=False),
        sa.CheckConstraint("schema_version IN ('orders.v1', 'orders.v2')", name="known_schema_version"),
        sa.CheckConstraint("column_policy IN ('reject', 'allow')", name="known_column_policy"),
        sa.CheckConstraint("time_source IN ('system_clock', 'injected_clock')", name="known_time_source"),
        sa.CheckConstraint("lateness_threshold_seconds IS NULL OR (lateness_threshold_seconds >= 0 AND lateness_threshold_seconds <= 31536000)", name="lateness_threshold_range"),
        sa.CheckConstraint("(schema_version = 'orders.v1' AND monitor_schema = false AND lateness_threshold_seconds IS NULL) OR (schema_version = 'orders.v2' AND monitor_schema = true AND lateness_threshold_seconds IS NOT NULL)", name="schema_monitoring_policy"),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("run_id"))
    op.create_table("record_arrivals",
        sa.Column("raw_order_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrival_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id", "raw_order_id"], ["raw_orders.run_id", "raw_orders.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline_contracts.run_id"]),
        sa.PrimaryKeyConstraint("raw_order_id"))
    op.create_index("ix_record_arrivals_run_id", "record_arrivals", ["run_id"])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Offline downgrade is refused: live evidence safety checks are required")
    connection = op.get_bind()
    has_evidence = connection.scalar(sa.text(
        "SELECT (SELECT count(*) FROM pipeline_contracts) + "
        "(SELECT count(*) FROM incidents WHERE incident_type IN ('SCHEMA_DRIFT', 'LATE_ARRIVING_DATA')) + "
        "(SELECT count(*) FROM rejected_orders WHERE reason_code = 'INVALID_EVENT_TIMESTAMP')"))
    if has_evidence:
        raise RuntimeError("Downgrade refused: Phase 3B contract or incident evidence would be lost")
    op.drop_index("ix_record_arrivals_run_id", table_name="record_arrivals")
    op.drop_table("record_arrivals")
    op.drop_table("pipeline_contracts")
    replace_checks(OLD_REASONS, OLD_TYPES, OLD_STAGES)
