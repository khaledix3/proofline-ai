from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (JSON, CheckConstraint, Date, DateTime, ForeignKey,
                        ForeignKeyConstraint, Integer, Numeric, String, Text, Index, text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.pipeline.timestamps import UTCDateTime


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="run_status"),
        CheckConstraint("source_count >= 0 AND accepted_count >= 0 AND rejected_count >= 0 AND duplicate_count >= 0", name="nonnegative_counts"),
        CheckConstraint("duplicate_count <= rejected_count", name="duplicates_are_rejections"),
        CheckConstraint("status != 'completed' OR source_count = accepted_count + rejected_count", name="completed_counts_reconcile"),
        CheckConstraint("(status = 'running' AND finished_at IS NULL) OR (status != 'running' AND finished_at IS NOT NULL)", name="run_finished_timestamp"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_name: Mapped[str] = mapped_column(String(255))
    source_csv: Mapped[str] = mapped_column(Text)  # Exact source text, including header.
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40))


class RawOrder(Base):
    __tablename__ = "raw_orders"
    __table_args__ = (
        UniqueConstraint("run_id", "row_number", name="raw_run_row"),
        UniqueConstraint("run_id", "id", name="raw_run_id"),
        CheckConstraint("row_number > 0", name="positive_row_number"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    original_record: Mapped[str] = mapped_column(Text)
    original_data: Mapped[dict] = mapped_column(JSON)
    # No unique constraint on business order_id: duplicates are evidence.


class CleanOrder(Base):
    __tablename__ = "clean_orders"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "raw_order_id"], ["raw_orders.run_id", "raw_orders.id"]),
        UniqueConstraint("raw_order_id", name="clean_source_once"),
        UniqueConstraint("run_id", "order_id", name="clean_business_order_per_run"),
        CheckConstraint("quantity > 0 AND quantity <= 1000000", name="clean_quantity_range"),
        CheckConstraint("unit_price >= 0 AND unit_price <= 99999999.99", name="clean_price_range"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    raw_order_id: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[str] = mapped_column(String(64))
    customer_id: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    order_date: Mapped[date] = mapped_column(Date)


class RejectedOrder(Base):
    __tablename__ = "rejected_orders"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "raw_order_id"], ["raw_orders.run_id", "raw_orders.id"]),
        ForeignKeyConstraint(["run_id", "duplicate_of_raw_id"], ["raw_orders.run_id", "raw_orders.id"]),
        UniqueConstraint("raw_order_id", name="rejected_source_once"),
        CheckConstraint("reason_code IN ('INVALID_COLUMNS', 'INVALID_ORDER_ID', 'INVALID_CUSTOMER_ID', 'INVALID_QUANTITY', 'INVALID_UNIT_PRICE', 'INVALID_ORDER_DATE', 'INVALID_EVENT_TIMESTAMP', 'DUPLICATE_ORDER_ID')", name="known_rejection_reason"),
        CheckConstraint("(reason_code = 'DUPLICATE_ORDER_ID' AND duplicate_of_raw_id IS NOT NULL) OR (reason_code != 'DUPLICATE_ORDER_ID' AND duplicate_of_raw_id IS NULL)", name="duplicate_reference_required"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    raw_order_id: Mapped[int] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String(40))
    duplicate_of_raw_id: Mapped[int | None] = mapped_column(Integer)


class ReportingStage(Base):
    __tablename__ = "reporting_stages"
    __table_args__ = (
        CheckConstraint("expected_count >= 0", name="report_expected_nonnegative"),
        CheckConstraint("fault IN ('none', 'omit_demo_order')", name="known_reporting_fault"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), primary_key=True)
    expected_count: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    fault: Mapped[str] = mapped_column(String(32), default="none")


class ReportingExpectedOrder(Base):
    """Saved validated upstream identity, independent of downstream output."""
    __tablename__ = "reporting_expected_orders"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "raw_order_id"], ["raw_orders.run_id", "raw_orders.id"]),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("reporting_stages.run_id"), primary_key=True)
    raw_order_id: Mapped[int] = mapped_column(ForeignKey("clean_orders.raw_order_id"), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64))


class ReportedOrder(Base):
    __tablename__ = "reported_orders"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "raw_order_id"],
                             ["reporting_expected_orders.run_id", "reporting_expected_orders.raw_order_id"]),
    )
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    raw_order_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64))


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("run_id", "incident_type", name="one_incident_type_per_run"),
        UniqueConstraint("run_id", "id", name="incident_run_id"),
        CheckConstraint("incident_type IN ('DUPLICATE_RECORDS', 'MISSING_RECORDS', 'SCHEMA_DRIFT', 'LATE_ARRIVING_DATA')", name="known_incident_type"),
        CheckConstraint("affected_count > 0", name="positive_affected_count"),
        CheckConstraint("(incident_type = 'DUPLICATE_RECORDS' AND stage IN ('raw_to_clean', 'reporting')) OR (incident_type = 'MISSING_RECORDS' AND stage = 'clean_to_reporting') OR (incident_type = 'SCHEMA_DRIFT' AND stage = 'source_to_raw') OR (incident_type = 'LATE_ARRIVING_DATA' AND stage = 'source_to_ingestion')", name="incident_stage_matches_type"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    incident_type: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str] = mapped_column(String(32))
    affected_count: Mapped[int] = mapped_column(Integer)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    evidence: Mapped[dict] = mapped_column(JSON)


class PipelineContract(Base):
    __tablename__ = "pipeline_contracts"
    __table_args__ = (
        CheckConstraint("schema_version IN ('orders.v1', 'orders.v2')", name="known_schema_version"),
        CheckConstraint("column_policy IN ('reject', 'allow')", name="known_column_policy"),
        CheckConstraint("time_source IN ('system_clock', 'injected_clock')", name="known_time_source"),
        CheckConstraint("lateness_threshold_seconds IS NULL OR (lateness_threshold_seconds >= 0 AND lateness_threshold_seconds <= 31536000)", name="lateness_threshold_range"),
        CheckConstraint("(schema_version = 'orders.v1' AND monitor_schema = false AND lateness_threshold_seconds IS NULL) OR (schema_version = 'orders.v2' AND monitor_schema = true AND lateness_threshold_seconds IS NOT NULL)", name="schema_monitoring_policy"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(32))
    expected_schema: Mapped[dict] = mapped_column(JSON)
    actual_header: Mapped[list] = mapped_column(JSON)
    column_policy: Mapped[str] = mapped_column(String(16))
    monitor_schema: Mapped[bool]
    lateness_threshold_seconds: Mapped[int | None] = mapped_column(Integer)
    ingestion_timestamp: Mapped[datetime] = mapped_column(UTCDateTime())
    time_source: Mapped[str] = mapped_column(String(16))


class RecordArrival(Base):
    __tablename__ = "record_arrivals"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "raw_order_id"], ["raw_orders.run_id", "raw_orders.id"]),
    )
    raw_order_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_contracts.run_id"), index=True)
    event_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime())
    arrival_timestamp: Mapped[datetime] = mapped_column(UTCDateTime())


class ReplayAudit(Base):
    __tablename__ = "replay_audits"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "incident_id"], ["incidents.run_id", "incidents.id"]),
        UniqueConstraint("replay_id", "simulation_sha256", name="unique_replay_result"),
        Index("one_verified_result_per_replay", "replay_id", unique=True,
              postgresql_where=text("outcome IN ('verified', 'no_change_required')"),
              sqlite_where=text("outcome IN ('verified', 'no_change_required')")),
        CheckConstraint("operation IN ('recover_missing_reporting', 'deduplicate_processing')", name="audit_known_operation"),
        CheckConstraint("outcome IN ('verified', 'no_change_required', 'verification_failed', 'failed')", name="audit_known_outcome"),
        CheckConstraint("applied_to_source = false", name="audit_never_applied"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    replay_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    incident_id: Mapped[str] = mapped_column(String(36), index=True)
    operation: Mapped[str] = mapped_column(String(40))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    algorithm_version: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32))
    simulation_sha256: Mapped[str] = mapped_column(String(64))
    audit_sha256: Mapped[str] = mapped_column(String(64))
    applied_to_source: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    report: Mapped[dict] = mapped_column(JSON)


class DatasetImport(Base):
    """One repeatable dataset experiment, separate from existing local demonstrations."""
    __tablename__ = "dataset_imports"
    __table_args__ = (
        CheckConstraint("source_kind IN ('uci_real', 'uci_controlled')", name="dataset_source_kind"),
        UniqueConstraint("import_key", name="dataset_import_once"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), primary_key=True)
    import_key: Mapped[str] = mapped_column(String(64))
    workbook_sha256: Mapped[str] = mapped_column(String(64))
    subset_sha256: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(24))
    provenance: Mapped[dict] = mapped_column(JSON)
    evaluation: Mapped[dict] = mapped_column(JSON)


class DatasetSourceRecord(Base):
    """Original workbook values; mapped pipeline fields never replace these values."""
    __tablename__ = "dataset_source_records"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "raw_order_id"], ["raw_orders.run_id", "raw_orders.id"]),
        UniqueConstraint("run_id", "source_row", name="dataset_row_once_per_run"),
    )
    raw_order_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("dataset_imports.run_id"))
    source_row: Mapped[int] = mapped_column(Integer)
    source_identity: Mapped[str] = mapped_column(String(100))
    original_values: Mapped[dict] = mapped_column(JSON)


class LabEvaluation(Base):
    """Only audit results persist; all fault-injected pipeline rows remain in memory."""
    __tablename__ = "lab_evaluations"
    __table_args__ = (UniqueConstraint("evaluation_key", name="lab_evaluation_once"),
        CheckConstraint("status IN ('completed', 'failed')", name="lab_known_status"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evaluation_key: Mapped[str] = mapped_column(String(64))
    source_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    algorithm_version: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime())
    report: Mapped[dict] = mapped_column(JSON)
    report_sha256: Mapped[str] = mapped_column(String(64))
