from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.incidents.detection import duplicate_evidence, missing_evidence
from app.incidents.schema_drift import schema_drift_evidence
from app.incidents.lateness import late_arrival_evidence
from app.models import (CleanOrder, Incident, PipelineContract, PipelineRun, RawOrder,
                        RecordArrival, RejectedOrder, ReportedOrder, ReportingExpectedOrder)
from app.pipeline.service import reconcile_counts
from app.replay.inputs import canonical_json, fingerprint

DETECTORS = (("DUPLICATE_RECORDS", duplicate_evidence, "duplicate_record_count"),
             ("MISSING_RECORDS", missing_evidence, "missing_count"),
             ("SCHEMA_DRIFT", schema_drift_evidence, "difference_count"),
             ("LATE_ARRIVING_DATA", late_arrival_evidence, "late_record_count"))
PROTECTED_MODELS = (PipelineRun, RawOrder, PipelineContract, RecordArrival, Incident)


def identities(session: Session, model, run_id: str) -> list[dict]:
    rows = session.scalars(select(model).where(model.run_id == run_id)).all()
    return [{"raw_order_id": row.raw_order_id, "order_id": row.order_id}
            for row in sorted(rows, key=lambda row: (row.raw_order_id, row.order_id))]


def validation_results(session: Session, run_id: str) -> list[dict]:
    clean = session.scalars(select(CleanOrder).where(CleanOrder.run_id == run_id)).all()
    rejected = session.scalars(select(RejectedOrder).where(RejectedOrder.run_id == run_id)).all()
    values = [{"raw_order_id": row.raw_order_id, "status": "accepted", "order_id": row.order_id,
               "customer_id": row.customer_id, "quantity": row.quantity, "unit_price": format(row.unit_price, ".2f"),
               "order_date": row.order_date.isoformat()} for row in clean]
    values += [{"raw_order_id": row.raw_order_id, "status": "rejected", "reason_code": row.reason_code,
                "duplicate_of_raw_id": row.duplicate_of_raw_id} for row in rejected]
    return sorted(values, key=lambda row: row["raw_order_id"])


def protected_fingerprint(session: Session, run_id: str) -> str:
    result = {}
    for model in PROTECTED_MODELS:
        query = select(model).where(model.id == run_id) if model is PipelineRun else select(model).where(model.run_id == run_id)
        result[model.__tablename__] = sorted(
            ({column.name: getattr(row, column.name) for column in model.__table__.columns}
             for row in session.scalars(query)), key=canonical_json)
    return fingerprint(result)


def evaluate_state(session: Session, run_id: str) -> dict:
    counts = reconcile_counts(session, run_id)
    expected = identities(session, ReportingExpectedOrder, run_id)
    reported = identities(session, ReportedOrder, run_id)
    business_counts = Counter(row["order_id"] for row in reported)
    incident_status = {}
    for incident_type, detector, count_key in DETECTORS:
        evidence = detector(session, run_id)
        incident_status[incident_type] = {"detected_in_simulation": evidence is not None,
            "affected_count": evidence["counts"][count_key] if evidence else 0, "evidence": evidence}
    return {"counts": {**counts, "expected_reporting_count": len(expected), "reported_count": len(reported),
                       "duplicate_output_excess_count": sum(max(0, count - 1) for count in business_counts.values())},
            "raw_order_ids": list(session.scalars(select(RawOrder.id).where(RawOrder.run_id == run_id).order_by(RawOrder.row_number))),
            "clean_identities": identities(session, CleanOrder, run_id),
            "expected_reporting_identities": expected, "reported_identities": reported,
            "validation_results": validation_results(session, run_id), "incident_status": incident_status}
