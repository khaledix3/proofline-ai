from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CleanOrder, PipelineRun, RawOrder, RejectedOrder, PipelineContract, RecordArrival, utc_now
from app.config import lateness_threshold_seconds as configured_lateness_threshold
from app.pipeline.ingestion import parse_orders_csv
from app.pipeline.contracts import ColumnPolicy, get_contract, validate_contract_order
from app.pipeline.timestamps import as_utc, parse_event_timestamp
from app.pipeline.validation import COLUMNS, find_duplicate
from app.pipeline.reporting import publish_reporting
from app.incidents.detection import detect_incidents

SAMPLE_PATH = Path(__file__).resolve().parents[2] / "data" / "synthetic_orders.csv"


class PipelineProcessingError(RuntimeError):
    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__("Pipeline processing failed")


def persist_raw_orders(session: Session, source: str, source_name: str, *,
                       schema_version: str = "orders.v1", column_policy: ColumnPolicy = "reject",
                       ingested_at: datetime | None = None, lateness_seconds: int | None = None) -> str:
    """Commit original input, versioned expectations and timing before processing."""
    contract = get_contract(schema_version)
    if column_policy not in ("reject", "allow"):
        raise ValueError("Unknown unexpected column policy")
    arrival_time = as_utc(ingested_at) if ingested_at is not None else utc_now()
    if schema_version == "orders.v2":
        threshold = configured_lateness_threshold() if lateness_seconds is None else lateness_seconds
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 31536000:
            raise ValueError("Lateness threshold must be an integer from 0 to 31536000 seconds")
    else:
        if column_policy != "reject" or lateness_seconds is not None:
            raise ValueError("Legacy v1 uses strict columns without timestamp monitoring")
        threshold = None
    parsed = parse_orders_csv(source)
    if schema_version == "orders.v1" and parsed.header != COLUMNS:
        raise ValueError(f"CSV header must be {','.join(COLUMNS)}")
    with session.begin():
        run = PipelineRun(source_name=source_name, source_csv=source, source_count=len(parsed.rows))
        session.add(run)
        session.flush()
        run_id = run.id
        session.add(PipelineContract(run_id=run_id, schema_version=schema_version,
                                    expected_schema=contract.definition(), actual_header=parsed.header,
                                    column_policy=column_policy, monitor_schema=contract.monitor_schema,
                                    lateness_threshold_seconds=threshold, ingestion_timestamp=arrival_time,
                                    time_source="injected_clock" if ingested_at is not None else "system_clock"))
        session.flush()
        raw_records = [RawOrder(run_id=run_id, row_number=row.row_number,
                                original_record=row.original_record, original_data=row.data) for row in parsed.rows]
        session.add_all(raw_records)
        session.flush()
        for raw in raw_records:
            event_time = None
            if schema_version == "orders.v2" and isinstance(raw.original_data.get("event_timestamp"), str):
                try:
                    event_time = parse_event_timestamp(raw.original_data["event_timestamp"])
                except ValueError:
                    pass  # Keep faulty values in raw; explicit validation rejects them.
            session.add(RecordArrival(run_id=run_id, raw_order_id=raw.id,
                                      event_timestamp=event_time, arrival_timestamp=arrival_time))
    return run_id


def persist_order_outcomes(session: Session, run_id: str) -> None:
    """Write exactly one outcome per raw row. Caller owns the transaction."""
    contract = session.get(PipelineContract, run_id)
    version = contract.schema_version if contract else "orders.v1"
    policy = contract.column_policy if contract else "reject"
    accepted_ids: dict[str, int] = {}
    raw_orders = session.scalars(select(RawOrder).where(RawOrder.run_id == run_id).order_by(RawOrder.row_number)).all()
    for raw in raw_orders:
        order, reason = validate_contract_order(raw.original_data, version, policy)
        duplicate_of = None
        if order is not None:
            duplicate_of = find_duplicate(order, accepted_ids)
            if duplicate_of is not None:
                reason = "DUPLICATE_ORDER_ID"
        if reason:
            session.add(RejectedOrder(run_id=run_id, raw_order_id=raw.id,
                                      reason_code=reason, duplicate_of_raw_id=duplicate_of))
        else:
            assert order is not None
            session.add(CleanOrder(run_id=run_id, raw_order_id=raw.id, **asdict(order)))
            accepted_ids[order.order_id] = raw.id
    session.flush()


def reconcile_counts(session: Session, run_id: str) -> dict[str, int]:
    """Verify actual persisted rows form a disjoint, complete partition of raw rows."""
    session.flush()
    raw_ids = set(session.scalars(select(RawOrder.id).where(RawOrder.run_id == run_id)))
    accepted = list(session.scalars(select(CleanOrder.raw_order_id).where(CleanOrder.run_id == run_id)))
    rejected = session.scalars(select(RejectedOrder).where(RejectedOrder.run_id == run_id)).all()
    rejected_ids = [row.raw_order_id for row in rejected]
    if (set(accepted) & set(rejected_ids) or set(accepted) | set(rejected_ids) != raw_ids
            or len(accepted) != len(set(accepted)) or len(rejected_ids) != len(set(rejected_ids))):
        raise ValueError("Outcomes must partition the raw records exactly once")
    run = session.get(PipelineRun, run_id)
    if run is None or run.source_count != len(raw_ids):
        raise ValueError("Source count differs from persisted raw count")
    return {"source_count": len(raw_ids), "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "duplicate_count": sum(row.reason_code == "DUPLICATE_ORDER_ID" for row in rejected)}


def execute_pipeline(session: Session, source: str, source_name: str, *, omit_demo_order: bool = False,
                     schema_version: str = "orders.v1", column_policy: ColumnPolicy = "reject",
                     ingested_at: datetime | None = None, lateness_seconds: int | None = None) -> str:
    """Ingest once, then atomically process/reconcile or retain a failed run."""
    run_id = persist_raw_orders(session, source, source_name, schema_version=schema_version,
                                column_policy=column_policy, ingested_at=ingested_at, lateness_seconds=lateness_seconds)
    try:
        with session.begin():
            persist_order_outcomes(session, run_id)
            metrics = reconcile_counts(session, run_id)
            run = session.get(PipelineRun, run_id)
            for key, value in metrics.items():
                setattr(run, key, value)
            run.status = "completed"
            run.finished_at = utc_now()
            publish_reporting(session, run_id, omit_demo_order=omit_demo_order)
            detect_incidents(session, run_id)
    except Exception as exc:
        # The processing transaction rolled back: no partial clean/rejected rows.
        with session.begin():
            run = session.get(PipelineRun, run_id)
            run.status = "failed"
            run.finished_at = utc_now()
            run.error_code = "PROCESSING_FAILED"
        raise PipelineProcessingError(run_id) from exc
    return run_id


def run_local_sample(session: Session) -> str:
    with SAMPLE_PATH.open(encoding="utf-8", newline="") as source:
        return execute_pipeline(session, source.read(), SAMPLE_PATH.name)


def get_run_result(session: Session, run_id: str) -> dict | None:
    run = session.get(PipelineRun, run_id)
    if run is None:
        return None
    raw = session.scalars(select(RawOrder).where(RawOrder.run_id == run_id).order_by(RawOrder.row_number)).all()
    clean = session.scalars(select(CleanOrder).where(CleanOrder.run_id == run_id).order_by(CleanOrder.raw_order_id)).all()
    rejected = session.scalars(select(RejectedOrder).where(RejectedOrder.run_id == run_id).order_by(RejectedOrder.raw_order_id)).all()
    return {
        "id": run.id, "status": run.status, "source_name": run.source_name,
        "started_at": run.started_at, "finished_at": run.finished_at, "error_code": run.error_code,
        "counts": {key: getattr(run, key) for key in ("source_count", "accepted_count", "rejected_count", "duplicate_count")},
        "raw_orders": [{"id": r.id, "row_number": r.row_number, "data": r.original_data,
                        "original_record": r.original_record} for r in raw],
        "clean_orders": [{"raw_order_id": c.raw_order_id, "order_id": c.order_id,
                          "customer_id": c.customer_id, "quantity": c.quantity,
                          "unit_price": str(c.unit_price), "order_date": c.order_date} for c in clean],
        "rejected_orders": [{"raw_order_id": r.raw_order_id, "reason_code": r.reason_code,
                             "duplicate_of_raw_id": r.duplicate_of_raw_id} for r in rejected],
    }
