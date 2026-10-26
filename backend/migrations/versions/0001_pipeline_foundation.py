"""Initial pipeline tables; frozen schema independent of future models."""
from alembic import op
import sqlalchemy as sa

revision = "0001_pipeline"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('pipeline_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_name', sa.String(length=255), nullable=False),
    sa.Column('source_csv', sa.Text(), nullable=False),
    sa.Column('source_count', sa.Integer(), nullable=False),
    sa.Column('accepted_count', sa.Integer(), nullable=False),
    sa.Column('rejected_count', sa.Integer(), nullable=False),
    sa.Column('duplicate_count', sa.Integer(), nullable=False),
    sa.Column('error_code', sa.String(length=40), nullable=True),
    sa.CheckConstraint("(status = 'running' AND finished_at IS NULL) OR (status != 'running' AND finished_at IS NOT NULL)", name='run_finished_timestamp'),
    sa.CheckConstraint("status != 'completed' OR source_count = accepted_count + rejected_count", name='completed_counts_reconcile'),
    sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name='run_status'),
    sa.CheckConstraint('duplicate_count <= rejected_count', name='duplicates_are_rejections'),
    sa.CheckConstraint('source_count >= 0 AND accepted_count >= 0 AND rejected_count >= 0 AND duplicate_count >= 0', name='nonnegative_counts'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('raw_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('row_number', sa.Integer(), nullable=False),
    sa.Column('original_record', sa.Text(), nullable=False),
    sa.Column('original_data', sa.JSON(), nullable=False),
    sa.CheckConstraint('row_number > 0', name='positive_row_number'),
    sa.ForeignKeyConstraint(['run_id'], ['pipeline_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'id', name='raw_run_id'),
    sa.UniqueConstraint('run_id', 'row_number', name='raw_run_row')
    )
    op.create_index(op.f('ix_raw_orders_run_id'), 'raw_orders', ['run_id'], unique=False)
    op.create_table('clean_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('raw_order_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.String(length=64), nullable=False),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('order_date', sa.Date(), nullable=False),
    sa.CheckConstraint('quantity > 0 AND quantity <= 1000000', name='clean_quantity_range'),
    sa.CheckConstraint('unit_price >= 0 AND unit_price <= 99999999.99', name='clean_price_range'),
    sa.ForeignKeyConstraint(['run_id', 'raw_order_id'], ['raw_orders.run_id', 'raw_orders.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['pipeline_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('raw_order_id', name='clean_source_once'),
    sa.UniqueConstraint('run_id', 'order_id', name='clean_business_order_per_run')
    )
    op.create_index(op.f('ix_clean_orders_run_id'), 'clean_orders', ['run_id'], unique=False)
    op.create_table('rejected_orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('raw_order_id', sa.Integer(), nullable=False),
    sa.Column('reason_code', sa.String(length=40), nullable=False),
    sa.Column('duplicate_of_raw_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("(reason_code = 'DUPLICATE_ORDER_ID' AND duplicate_of_raw_id IS NOT NULL) OR (reason_code != 'DUPLICATE_ORDER_ID' AND duplicate_of_raw_id IS NULL)", name='duplicate_reference_required'),
    sa.CheckConstraint("reason_code IN ('INVALID_COLUMNS', 'INVALID_ORDER_ID', 'INVALID_CUSTOMER_ID', 'INVALID_QUANTITY', 'INVALID_UNIT_PRICE', 'INVALID_ORDER_DATE', 'DUPLICATE_ORDER_ID')", name='known_rejection_reason'),
    sa.ForeignKeyConstraint(['run_id', 'duplicate_of_raw_id'], ['raw_orders.run_id', 'raw_orders.id'], ),
    sa.ForeignKeyConstraint(['run_id', 'raw_order_id'], ['raw_orders.run_id', 'raw_orders.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['pipeline_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('raw_order_id', name='rejected_source_once')
    )
    op.create_index(op.f('ix_rejected_orders_run_id'), 'rejected_orders', ['run_id'], unique=False)

def downgrade():
    op.drop_index(op.f('ix_rejected_orders_run_id'), table_name='rejected_orders')
    op.drop_table('rejected_orders')
    op.drop_index(op.f('ix_clean_orders_run_id'), table_name='clean_orders')
    op.drop_table('clean_orders')
    op.drop_index(op.f('ix_raw_orders_run_id'), table_name='raw_orders')
    op.drop_table('raw_orders')
    op.drop_table('pipeline_runs')
