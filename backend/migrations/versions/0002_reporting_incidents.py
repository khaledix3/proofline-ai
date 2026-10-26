"""Add reporting manifests and incident evidence; preserve existing pipeline tables."""
from alembic import op
import sqlalchemy as sa

revision = "0002_incidents"
down_revision = "0001_pipeline"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('incidents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('incident_type', sa.String(length=32), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('affected_count', sa.Integer(), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.CheckConstraint("(incident_type = 'DUPLICATE_RECORDS' AND stage = 'raw_to_clean') OR (incident_type = 'MISSING_RECORDS' AND stage = 'clean_to_reporting')", name='incident_stage_matches_type'),
    sa.CheckConstraint("incident_type IN ('DUPLICATE_RECORDS', 'MISSING_RECORDS')", name='known_incident_type'),
    sa.CheckConstraint('affected_count > 0', name='positive_affected_count'),
    sa.ForeignKeyConstraint(['run_id'], ['pipeline_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'incident_type', name='one_incident_type_per_run')
    )
    op.create_index(op.f('ix_incidents_run_id'), 'incidents', ['run_id'], unique=False)
    op.create_table('reporting_stages',
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('expected_count', sa.Integer(), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fault', sa.String(length=32), nullable=False),
    sa.CheckConstraint("fault IN ('none', 'omit_demo_order')", name='known_reporting_fault'),
    sa.CheckConstraint('expected_count >= 0', name='report_expected_nonnegative'),
    sa.ForeignKeyConstraint(['run_id'], ['pipeline_runs.id'], ),
    sa.PrimaryKeyConstraint('run_id')
    )
    op.create_table('reporting_expected_orders',
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('raw_order_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['raw_order_id'], ['clean_orders.raw_order_id'], ),
    sa.ForeignKeyConstraint(['run_id', 'raw_order_id'], ['raw_orders.run_id', 'raw_orders.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['reporting_stages.run_id'], ),
    sa.PrimaryKeyConstraint('run_id', 'raw_order_id')
    )
    op.create_table('reported_orders',
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('raw_order_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['run_id', 'raw_order_id'], ['reporting_expected_orders.run_id', 'reporting_expected_orders.raw_order_id'], ),
    sa.PrimaryKeyConstraint('run_id', 'raw_order_id')
    )

def downgrade():
    op.drop_table('reported_orders')
    op.drop_table('reporting_expected_orders')
    op.drop_table('reporting_stages')
    op.drop_index(op.f('ix_incidents_run_id'), table_name='incidents')
    op.drop_table('incidents')
