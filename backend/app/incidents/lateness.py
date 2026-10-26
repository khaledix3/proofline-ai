from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CleanOrder, PipelineContract, RawOrder, RecordArrival
from app.pipeline.timestamps import as_utc


def arrival_delay(event_timestamp, arrival_timestamp, threshold_seconds: int) -> dict | None:
    """Strictly greater than deadline is late; equality is on time."""
    if isinstance(threshold_seconds, bool) or not isinstance(threshold_seconds, int) or not 0 <= threshold_seconds <= 31536000:
        raise ValueError("Lateness threshold must be an integer from 0 to 31536000 seconds")
    event = as_utc(event_timestamp)
    arrival = as_utc(arrival_timestamp)
    if arrival - event <= timedelta(seconds=threshold_seconds):
        return None
    deadline = event + timedelta(seconds=threshold_seconds)
    return {"event_timestamp": event.isoformat(), "expected_deadline": deadline.isoformat(),
            "actual_arrival_timestamp": arrival.isoformat(),
            "event_age_seconds": (arrival - event).total_seconds(),
            "delay_seconds": (arrival - deadline).total_seconds()}


def late_arrival_evidence(session: Session, run_id: str) -> dict | None:
    contract = session.get(PipelineContract, run_id)
    if contract is None or contract.lateness_threshold_seconds is None:
        return None  # Do not invent event times for date-only legacy records.
    records = session.execute(select(RecordArrival, CleanOrder.order_id, RawOrder.row_number)
                              .join(CleanOrder, CleanOrder.raw_order_id == RecordArrival.raw_order_id)
                              .join(RawOrder, RawOrder.id == RecordArrival.raw_order_id)
                              .where(RecordArrival.run_id == run_id, CleanOrder.run_id == run_id)
                              .order_by(RawOrder.row_number)).all()
    accepted_ids = set(session.scalars(select(CleanOrder.raw_order_id).where(CleanOrder.run_id == run_id)))
    if {arrival.raw_order_id for arrival, _, _ in records} != accepted_ids:
        raise ValueError("Accepted records lack complete arrival evidence")
    late = []
    for arrival, order_id, row_number in records:
        if arrival.event_timestamp is None:
            raise ValueError("Accepted timestamped record lacks event timing evidence")
        delay = arrival_delay(arrival.event_timestamp, arrival.arrival_timestamp, contract.lateness_threshold_seconds)
        if delay is not None:
            late.append({"raw_order_id": arrival.raw_order_id, "order_id": order_id,
                         "row_number": row_number, **delay})
    if not late:
        return None
    return {"rule_version": "1", "run_id": run_id, "stage": "source_to_ingestion",
            "schema_version": contract.schema_version, "time_source": contract.time_source,
            "threshold_seconds": contract.lateness_threshold_seconds,
            "eligibility": "Accepted clean records only; invalid and duplicate rejections excluded.",
            "counts": {"eligible_record_count": len(records), "late_record_count": len(late),
                       "on_time_record_count": len(records) - len(late)},
            "affected_order_ids": [row["order_id"] for row in late], "late_records": late}
