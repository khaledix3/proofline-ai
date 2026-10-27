"""Allow explicit reporting-stage duplicate evidence; do not relabel old snapshots."""
from alembic import op
import sqlalchemy as sa
revision="0006_reporting_collision_stage"
down_revision="0005_dataset_provenance"
branch_labels=None
depends_on=None
OLD="(incident_type = 'DUPLICATE_RECORDS' AND stage = 'raw_to_clean') OR (incident_type = 'MISSING_RECORDS' AND stage = 'clean_to_reporting') OR (incident_type = 'SCHEMA_DRIFT' AND stage = 'source_to_raw') OR (incident_type = 'LATE_ARRIVING_DATA' AND stage = 'source_to_ingestion')"
NEW=OLD.replace("stage = 'raw_to_clean'", "stage IN ('raw_to_clean', 'reporting')")
def upgrade():
    with op.batch_alter_table("incidents") as batch:
        batch.drop_constraint("incident_stage_matches_type",type_="check")
        batch.create_check_constraint("incident_stage_matches_type",NEW)
def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Live incident safety check required")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM replay_audits")):
        raise RuntimeError("Downgrade refused: durable replay audit records would be lost by a chained downgrade")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM dataset_imports")):
        raise RuntimeError("Downgrade refused: source provenance requires current incident semantics")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM incidents WHERE incident_type='DUPLICATE_RECORDS' AND stage='reporting'")):
        raise RuntimeError("Downgrade refused: reporting-stage incidents exist")
    with op.batch_alter_table("incidents") as batch:
        batch.drop_constraint("incident_stage_matches_type",type_="check")
        batch.create_check_constraint("incident_stage_matches_type",OLD)
