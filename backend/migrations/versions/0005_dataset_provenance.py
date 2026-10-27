"""Add source provenance only; preserve all existing pipeline and audit rows."""
from alembic import op
import sqlalchemy as sa
revision = "0005_dataset_provenance"
down_revision = "0004_replay_audit"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("dataset_imports",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("import_key", sa.String(64), nullable=False),
        sa.Column("workbook_sha256", sa.String(64), nullable=False),
        sa.Column("subset_sha256", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("evaluation", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline_runs.id"]),
        sa.UniqueConstraint("import_key", name="dataset_import_once"),
        sa.CheckConstraint("source_kind IN ('uci_real', 'uci_controlled')", name="dataset_source_kind"))
    op.create_table("dataset_source_records",
        sa.Column("raw_order_id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("source_identity", sa.String(100), nullable=False),
        sa.Column("original_values", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["dataset_imports.run_id"]),
        sa.ForeignKeyConstraint(["run_id", "raw_order_id"], ["raw_orders.run_id", "raw_orders.id"]),
        sa.UniqueConstraint("run_id", "source_row", name="dataset_row_once_per_run"))

def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Live provenance safety check required")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM replay_audits")):
        raise RuntimeError("Downgrade refused: durable replay audit records would be lost by a chained downgrade")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM dataset_imports")):
        raise RuntimeError("Downgrade refused: source provenance would be lost")
    op.drop_table("dataset_source_records")
    op.drop_table("dataset_imports")
