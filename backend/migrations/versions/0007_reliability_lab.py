"""Persist isolated reliability evaluations, never cloned source tables."""
from alembic import op
import sqlalchemy as sa
revision = "0007_reliability_lab"
down_revision = "0006_reporting_collision_stage"
branch_labels = depends_on = None

def upgrade():
    op.create_table("lab_evaluations",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("evaluation_key",sa.String(64),nullable=False),
        sa.Column("source_run_id",sa.String(36),sa.ForeignKey("pipeline_runs.id"),nullable=False),
        sa.Column("source_fingerprint",sa.String(64),nullable=False),
        sa.Column("algorithm_version",sa.String(32),nullable=False),
        sa.Column("config",sa.JSON(),nullable=False),sa.Column("status",sa.String(16),nullable=False),
        sa.Column("started_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("finished_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("report",sa.JSON(),nullable=False),sa.Column("report_sha256",sa.String(64),nullable=False),
        sa.UniqueConstraint("evaluation_key",name="lab_evaluation_once"),
        sa.CheckConstraint("status IN ('completed', 'failed')",name="lab_known_status"))
    op.create_index("ix_lab_evaluations_source_run_id","lab_evaluations",["source_run_id"])

def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Live audit safety check required")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM lab_evaluations")):
        raise RuntimeError("Downgrade refused: durable reliability lab audit records would be lost")
    # Fail before any DDL when a chained downgrade would destroy existing evidence.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM replay_audits")):
        raise RuntimeError("Downgrade refused: durable replay audit records would be lost")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM dataset_imports")):
        raise RuntimeError("Downgrade refused: source provenance would be lost")
    op.drop_table("lab_evaluations")
