"""Durable simulation audits; no source-row changes."""
from alembic import op
import sqlalchemy as sa

revision = "0004_replay_audit"
down_revision = "0003_schema_lateness"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("incidents") as batch:
        batch.create_unique_constraint("incident_run_id", ["run_id", "id"])
    op.create_table("replay_audits",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("replay_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("algorithm_version", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("simulation_sha256", sa.String(64), nullable=False),
        sa.Column("audit_sha256", sa.String(64), nullable=False),
        sa.Column("applied_to_source", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline_runs.id"]),
        sa.ForeignKeyConstraint(["run_id", "incident_id"], ["incidents.run_id", "incidents.id"]),
        sa.UniqueConstraint("replay_id", "simulation_sha256", name="unique_replay_result"),
        sa.CheckConstraint("operation IN ('recover_missing_reporting', 'deduplicate_processing')", name="audit_known_operation"),
        sa.CheckConstraint("outcome IN ('verified', 'no_change_required', 'verification_failed', 'failed')", name="audit_known_outcome"),
        sa.CheckConstraint("applied_to_source = false", name="audit_never_applied"))
    op.create_index("one_verified_result_per_replay", "replay_audits", ["replay_id"], unique=True,
                    postgresql_where=sa.text("outcome IN ('verified', 'no_change_required')"),
                    sqlite_where=sa.text("outcome IN ('verified', 'no_change_required')"))
    for column in ("replay_id", "run_id", "incident_id"):
        op.create_index(f"ix_replay_audits_{column}", "replay_audits", [column])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Offline audit downgrade refused: live audit safety checks required")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM replay_audits")):
        raise RuntimeError("Downgrade refused: durable replay audit records would be lost")
    op.drop_index("one_verified_result_per_replay", table_name="replay_audits")
    for column in ("incident_id", "run_id", "replay_id"):
        op.drop_index(f"ix_replay_audits_{column}", table_name="replay_audits")
    op.drop_table("replay_audits")
    with op.batch_alter_table("incidents") as batch:
        batch.drop_constraint("incident_run_id", type_="unique")
