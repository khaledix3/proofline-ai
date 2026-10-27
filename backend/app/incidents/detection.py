from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (CleanOrder, Incident, PipelineRun, RawOrder, RejectedOrder,
                        ReportedOrder, ReportingExpectedOrder, ReportingStage, PipelineContract)
from app.pipeline.validation import find_duplicate
from app.pipeline.contracts import validate_contract_order
from app.incidents.schema_drift import schema_drift_evidence
from app.incidents.lateness import late_arrival_evidence


def duplicate_evidence(session: Session, run_id: str) -> dict | None:
    """Recompute duplicate identities from raw fields using the processing rules."""
    raw = session.scalars(select(RawOrder).where(RawOrder.run_id == run_id).order_by(RawOrder.row_number)).all()
    contract = session.get(PipelineContract, run_id)
    version = contract.schema_version if contract else "orders.v1"
    policy = contract.column_policy if contract else "reject"

    def validate(data):
        return validate_contract_order(data, version, policy)

    first: dict[str, int] = {}
    groups = defaultdict(list)
    invalid_count = 0
    for row in raw:
        order, reason = validate(row.original_data)
        if reason:
            invalid_count += 1
            continue
        duplicate_of = find_duplicate(order, first)
        if duplicate_of is None:
            first[order.order_id] = row.id
        else:
            groups[order.order_id].append({"raw_order_id": row.id, "row_number": row.row_number})
    expected_duplicates = {item["raw_order_id"] for rows in groups.values() for item in rows}
    actual_duplicates = session.scalars(select(RejectedOrder).where(
        RejectedOrder.run_id == run_id, RejectedOrder.reason_code == "DUPLICATE_ORDER_ID")).all()
    clean = session.scalars(select(CleanOrder).where(CleanOrder.run_id == run_id)).all()
    if (expected_duplicates != {row.raw_order_id for row in actual_duplicates}
            or {(row.order_id, row.raw_order_id) for row in clean} != set(first.items())):
        raise ValueError("Duplicate evidence does not match persisted processing outcomes")
    for row in actual_duplicates:
        order, _ = validate(next(source.original_data for source in raw if source.id == row.raw_order_id))
        if row.duplicate_of_raw_id != first[order.order_id]:
            raise ValueError("Duplicate rejection does not reference the first accepted source")
    output_rows = session.scalars(select(ReportedOrder).where(ReportedOrder.run_id == run_id)).all()
    output_by_id = defaultdict(list)
    for output in output_rows:
        output_by_id[output.order_id].append(output.raw_order_id)
    output_groups = [{"order_id": key, "raw_order_ids": sorted(ids), "occurrence_count": len(ids)}
                     for key, ids in sorted(output_by_id.items()) if len(ids) > 1]
    output_excess = sum(row["occurrence_count"] - 1 for row in output_groups)
    if not expected_duplicates and not output_excess:
        return None
    row_numbers = {row.id: row.row_number for row in raw}
    evidence = {
        "rule_version": "1", "run_id": run_id, "stage": "raw_to_clean",
        "rule": "Validate first; strip IDs, compare case-sensitively within a run; first valid occurrence wins.",
        "counts": {"raw_count": len(raw), "valid_record_count": len(raw) - invalid_count,
                   "invalid_record_count": invalid_count, "clean_count": len(clean),
                   "duplicate_record_count": len(expected_duplicates), "affected_order_id_count": len(groups)},
        "affected_order_ids": sorted(groups),
        "groups": [{"order_id": order_id, "first_raw_order_id": first[order_id],
                    "first_row_number": row_numbers[first[order_id]],
                    "occurrence_count": 1 + len(groups[order_id]), "duplicates": groups[order_id]}
                   for order_id in sorted(groups)],
    }
    if output_excess:
        evidence["counts"]["duplicate_output_excess_count"] = output_excess
        evidence["output_stage"] = "reporting"
        if not expected_duplicates:
            evidence["stage"] = "reporting"
        evidence["output_groups"] = output_groups
        evidence["affected_order_ids"] = sorted(set(evidence["affected_order_ids"]) | {row["order_id"] for row in output_groups})
        evidence["output_rule"] = "Distinct source-line reporting rows must not collide on the validated per-run identity. Repeated invoice numbers alone are not duplicates."
    return evidence



def missing_evidence(session: Session, run_id: str) -> dict | None:
    """Compare saved accepted upstream identities with the published downstream set."""
    stage = session.get(ReportingStage, run_id)
    if stage is None:
        return None  # Historical/unpublished runs are not proof of missing records.
    expected = set(session.execute(select(ReportingExpectedOrder.raw_order_id, ReportingExpectedOrder.order_id)
                                   .where(ReportingExpectedOrder.run_id == run_id)).tuples())
    actual = set(session.execute(select(ReportedOrder.raw_order_id, ReportedOrder.order_id)
                                 .where(ReportedOrder.run_id == run_id)).tuples())
    if stage.expected_count != len(expected):
        raise ValueError("Upstream manifest count is inconsistent")
    missing = expected - actual
    if not missing:
        return None

    def records(identities):
        return [{"raw_order_id": raw_id, "order_id": order_id} for raw_id, order_id in sorted(identities)]

    return {
        "rule_version": "1", "run_id": run_id, "stage": "clean_to_reporting",
        "upstream_table": "reporting_expected_orders", "downstream_table": "reported_orders",
        "identity_fields": ["raw_order_id", "order_id"],
        "counts": {"expected_count": len(expected), "actual_count": len(actual),
                   "matched_count": len(expected & actual), "missing_count": len(missing),
                   "unexpected_count": len(actual - expected)},
        "affected_order_ids": sorted(order_id for _, order_id in missing),
        "expected_records": records(expected), "actual_records": records(actual),
        "missing_records": records(missing), "unexpected_records": records(actual - expected),
    }


def detect_incidents(session: Session, run_id: str) -> list[Incident]:
    """Append one immutable evidence snapshot per incident type/run. Caller commits."""
    run = session.get(PipelineRun, run_id)
    if run is None or run.status != "completed":
        raise ValueError("Incident detection requires a completed run")
    # Serialize concurrent detection for the same run on PostgreSQL.
    session.execute(select(PipelineRun.id).where(PipelineRun.id == run_id).with_for_update()).one()
    existing = {row.incident_type: row for row in session.scalars(select(Incident).where(Incident.run_id == run_id))}
    checks = [("DUPLICATE_RECORDS", duplicate_evidence, "duplicate_record_count"),
              ("MISSING_RECORDS", missing_evidence, "missing_count"),
              ("SCHEMA_DRIFT", schema_drift_evidence, "difference_count"),
              ("LATE_ARRIVING_DATA", late_arrival_evidence, "late_record_count")]
    for incident_type, detector, count_key in checks:
        if incident_type in existing:
            continue  # Preserve the original evidence rather than silently rewriting it.
        evidence = detector(session, run_id)
        if evidence is not None:
            incident = Incident(run_id=run_id, incident_type=incident_type, stage=evidence["stage"],
                                affected_count=evidence["counts"][count_key] + (evidence["counts"].get("duplicate_output_excess_count", 0) if incident_type == "DUPLICATE_RECORDS" else 0), evidence=evidence)
            session.add(incident)
            existing[incident_type] = incident
    session.flush()
    return [existing[key] for key in sorted(existing)]
