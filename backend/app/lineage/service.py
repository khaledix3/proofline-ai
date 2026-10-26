"""Read-only lineage from durable rows and saved evidence, without replaying rules."""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lineage.evidence import incident_references
from app.lineage.graph import build_graph, evidence_ref
from app.models import (CleanOrder, Incident, PipelineContract, PipelineRun, RawOrder,
                        RecordArrival, RejectedOrder, ReportedOrder, ReportingExpectedOrder, ReportingStage)


@dataclass
class RunFacts:
    run: PipelineRun
    raw: dict
    clean: dict
    rejected: dict
    expected: dict
    reported: dict
    arrivals: dict
    reporting_stage: ReportingStage | None
    contract: PipelineContract | None
    incidents: list


def _start_read_snapshot(session: Session) -> None:
    # Fresh HTTP sessions get one consistent, server-enforced read-only PG snapshot.
    # Caller-owned transactions are respected; no commits or writes occur here.
    if session.new or session.dirty or session.deleted:
        raise ValueError("Lineage requires a clean read session")
    bind = session.get_bind()
    bind_transaction = getattr(bind, "in_transaction", lambda: False)()
    if not session.in_transaction() and not bind_transaction and bind.dialect.name == "postgresql":
        connection = session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")


def _load_facts(session: Session, run_id: str) -> RunFacts | None:
    run = session.get(PipelineRun, run_id)
    if run is None:
        return None

    def rows(model):
        return session.scalars(select(model).where(model.run_id == run_id)).all()

    return RunFacts(run, {row.id: row for row in sorted(rows(RawOrder), key=lambda row: row.row_number)},
                    {row.raw_order_id: row for row in rows(CleanOrder)},
                    {row.raw_order_id: row for row in rows(RejectedOrder)},
                    {row.raw_order_id: row for row in rows(ReportingExpectedOrder)},
                    {row.raw_order_id: row for row in rows(ReportedOrder)},
                    {row.raw_order_id: row for row in rows(RecordArrival)},
                    session.get(ReportingStage, run_id), session.get(PipelineContract, run_id),
                    sorted(rows(Incident), key=lambda row: (row.incident_type, row.id)))


def _identity(row) -> dict | None:
    if row is None:
        return None
    return {"run_id": row.run_id, "raw_order_id": row.raw_order_id, "order_id": row.order_id}


def _observation(status, kind, refs, **attributes):
    return {"status": status, "evidence_kind": kind, "evidence_refs": refs, **attributes}


def _record_lineage(facts: RunFacts, raw_id: int, links: list[dict]) -> dict:
    raw = facts.raw[raw_id]
    run_id = facts.run.id
    clean = facts.clean.get(raw_id)
    rejected = facts.rejected.get(raw_id)
    expected = facts.expected.get(raw_id)
    reported = facts.reported.get(raw_id)
    arrival = facts.arrivals.get(raw_id)
    gaps, issues = [], []
    clean_refs = [evidence_ref("clean_orders", id=clean.id, run_id=run_id)] if clean else []
    reject_refs = [evidence_ref("rejected_orders", id=rejected.id, run_id=run_id)] if rejected else []
    if clean and rejected:
        outcome = "conflicting"
        issues.append("CONFLICTING_VALIDATION_OUTCOMES")
    elif clean:
        outcome = "accepted"
    elif rejected:
        outcome = "rejected"
    else:
        outcome = "unknown"
        gaps.append("VALIDATION_OUTCOME_NOT_RECORDED")
        if facts.run.status == "completed":
            issues.append("COMPLETED_RUN_WITHOUT_VALIDATION_OUTCOME")
    clean_status = "observed" if clean else "intentionally_excluded" if rejected else "not_observed"
    expectation_refs = [evidence_ref("reporting_expected_orders", run_id=run_id, raw_order_id=raw_id)] if expected else []
    report_refs = [evidence_ref("reported_orders", run_id=run_id, raw_order_id=raw_id)] if reported else []
    expected_identity, observed_identity = _identity(expected), _identity(reported)
    missing = None
    if reported:
        report_status = "identity_mismatch" if expected and expected_identity != observed_identity else "observed"
        if expected and facts.reporting_stage:
            missing = expected_identity != observed_identity
        if expected is None:
            issues.append("REPORTED_RECORD_WITHOUT_EXPECTATION")
    elif rejected and not clean and not expected:
        report_status = "intentionally_excluded"
        missing = False
    elif expected and facts.reporting_stage:
        report_status = "not_observed"
        missing = True
    else:
        report_status = "unknown"
        gaps.append("REPORTING_EXPECTATION_NOT_RECORDED" if facts.reporting_stage else "REPORTING_STAGE_NOT_RECORDED")
    if expected and clean and _identity(clean) != expected_identity:
        issues.append("CLEAN_IDENTITY_DIFFERS_FROM_EXPECTATION")
    if expected and not clean:
        issues.append("EXPECTED_SOURCE_CLEAN_RECORD_NOT_OBSERVED")
    if expected and rejected and not clean:
        issues.append("REJECTED_RECORD_HAS_REPORTING_EXPECTATION")
    if (expected or reported) and facts.reporting_stage is None:
        issues.append("REPORTING_RECORDS_WITHOUT_STAGE_MARKER")
    if arrival is None and facts.contract is not None:
        gaps.append("ARRIVAL_METADATA_NOT_RECORDED")
    duplicate = None
    if rejected and rejected.reason_code == "DUPLICATE_ORDER_ID":
        target = rejected.duplicate_of_raw_id
        duplicate = {"role": "rejected_duplicate", "referenced_raw_order_id": target,
                     "reference_status": "accepted_source_confirmed" if target in facts.clean and target in facts.raw else "target_outcome_unavailable"}
        if duplicate["reference_status"] != "accepted_source_confirmed":
            gaps.append("DUPLICATE_TARGET_OUTCOME_UNAVAILABLE")
    else:
        duplicates = sorted(row.raw_order_id for row in facts.rejected.values()
                            if row.reason_code == "DUPLICATE_ORDER_ID" and row.duplicate_of_raw_id == raw_id)
        if duplicates:
            duplicate = {"role": "accepted_source" if clean else "referenced_source_outcome_unknown",
                         "rejected_raw_order_ids": duplicates}
    last = "reporting" if reported else "clean" if clean else "validation" if rejected else "raw"
    last_expected = ("reporting" if missing is False and reported else "clean"
                     if clean and expected and _identity(clean) == expected_identity else None)
    return {
        "raw_order_id": raw_id, "row_number": raw.row_number,
        "source_order_id": raw.original_data.get("order_id"),
        "raw": _observation("observed", "persisted_record", [evidence_ref("raw_orders", id=raw_id, run_id=run_id)]),
        "validation": _observation(outcome, "persisted_outcome" if outcome != "unknown" else "stage_placeholder",
                                   clean_refs + reject_refs, rejection_reason=rejected.reason_code if rejected else None,
                                   event_timestamp=None, separate_validation_event_recorded=False),
        "clean": _observation(clean_status, "persisted_record" if clean else "policy_outcome" if rejected else "stage_placeholder",
                              clean_refs or reject_refs, clean_order_id=clean.id if clean else None, identity=_identity(clean)),
        "reporting": _observation(report_status, "persisted_record" if reported else "absence_observation"
                                  if missing is True else "policy_outcome" if rejected and not clean else "stage_placeholder",
                                  report_refs, expected_identity=expected_identity, observed_identity=observed_identity,
                                  expectation_refs=expectation_refs, missing_expected_identity=missing,
                                  published_stage_recorded=facts.reporting_stage is not None),
        "last_confirmed_stage": last, "last_confirmed_expected_identity_stage": last_expected,
        "duplicate_relationship": duplicate,
        "timing": {"event_timestamp": arrival.event_timestamp, "arrival_timestamp": arrival.arrival_timestamp,
                   "time_source": facts.contract.time_source if facts.contract else None,
                   "evidence_ref": evidence_ref("record_arrivals", raw_order_id=raw_id, run_id=run_id)} if arrival else None,
        "incident_links": links, "evidence_gaps": gaps, "integrity_issues": issues,
    }


def _assemble(facts: RunFacts, incident: Incident | None = None) -> dict:
    run_id = facts.run.id
    incidents = [incident] if incident else facts.incidents
    raw_ids = set(facts.raw)
    references = {row.id: incident_references(row, raw_ids) for row in incidents}
    selected = set(references[incident.id].records) if incident else raw_ids
    records = [_record_lineage(facts, raw_id, [link for ref in references.values() for link in ref.records.get(raw_id, [])])
               for raw_id in facts.raw if raw_id in selected]
    snapshots = [{"id": row.id, "run_id": row.run_id, "incident_type": row.incident_type,
                  "stage": row.stage, "detected_at": row.detected_at, "affected_count": row.affected_count,
                  "evidence": row.evidence, "semantics": "stored_detection_snapshot"} for row in incidents]
    gaps = []
    if facts.contract is None:
        gaps.append({"code": "SCHEMA_CONTRACT_NOT_RECORDED"})
    issues = [issue for ref in references.values() for issue in ref.issues]
    actual = {"source_count": len(facts.raw), "accepted_count": len(facts.clean),
              "rejected_count": len(facts.rejected), "duplicate_count": sum(
                  row.reason_code == "DUPLICATE_ORDER_ID" for row in facts.rejected.values())}
    stored = {key: getattr(facts.run, key) for key in actual}
    if stored != actual:
        issues.append({"code": "STORED_COUNTS_DIFFER_FROM_CURRENT_RECORDS", "stored": stored, "observed": actual})
    for table, rows in (("clean_orders", facts.clean), ("rejected_orders", facts.rejected),
                        ("reporting_expected_orders", facts.expected), ("reported_orders", facts.reported),
                        ("record_arrivals", facts.arrivals)):
        if set(rows) - raw_ids:
            issues.append({"code": "SOURCE_RECORD_NOT_OBSERVED", "table": table,
                           "raw_order_ids": sorted(set(rows) - raw_ids)})
    if facts.reporting_stage and facts.reporting_stage.expected_count != len(facts.expected):
        issues.append({"code": "REPORTING_MANIFEST_COUNT_MISMATCH", "recorded_count": facts.reporting_stage.expected_count,
                       "observed_count": len(facts.expected)})
    for record in records:
        gaps.extend({"code": code, "raw_order_id": record["raw_order_id"]} for code in record["evidence_gaps"])
        issues.extend({"code": code, "raw_order_id": record["raw_order_id"]} for code in record["integrity_issues"])
    interpretations = [{"classification": "inference", "raw_order_id": row["raw_order_id"],
                        "code": "EXPECTED_IDENTITY_NOT_OBSERVED",
                        "last_confirmed_expected_identity_stage": row["last_confirmed_expected_identity_stage"],
                        "root_cause": None,
                        "statement": "A recorded expectation has no matching current reporting identity. The cause and historical arrival are not recorded."}
                       for row in records if row["reporting"]["missing_expected_identity"] is True]
    nodes, edges = build_graph(run_id, records, snapshots, {key for key, ref in references.items() if ref.run_scoped})
    return {
        "format_version": "1", "scope": {"kind": "incident" if incident else "run", "run_id": run_id,
                                          "incident_id": incident.id if incident else None},
        "run": {"id": run_id, "status": facts.run.status, "source_name": facts.run.source_name,
                "started_at": facts.run.started_at, "finished_at": facts.run.finished_at, "error_code": facts.run.error_code, "counts": stored},
        "stage_definitions": [{"id": key, "label": label} for key, label in (
            ("raw", "Raw Orders"), ("validation", "Validation outcome"), ("clean", "Clean Orders"), ("reporting", "Reporting"))],
        "schema_contract": {"version": facts.contract.schema_version, "expected_schema": facts.contract.expected_schema,
                            "actual_header": facts.contract.actual_header, "column_policy": facts.contract.column_policy} if facts.contract else None,
        "reporting_stage": {"published_at": facts.reporting_stage.published_at,
                            "expected_count": facts.reporting_stage.expected_count} if facts.reporting_stage else None,
        "records": records, "nodes": nodes, "edges": edges, "incidents": snapshots,
        "summary": {"returned_record_count": len(records), "total_run_record_count": len(facts.raw),
                    "observed_run_counts": actual, "stored_counts_match": stored == actual,
                    "missing_expected_identity_count": sum(row["reporting"]["missing_expected_identity"] is True for row in records),
                    "evidence_completeness": "incomplete" if gaps or issues else "complete",
                    "integrity_status": "inconsistent" if issues else "consistent"},
        "evidence_gaps": gaps, "integrity_issues": issues, "interpretations": interpretations,
        "semantics": {"observations": "Current durable rows, scoped to this run. Incident snapshots retain their detection-time meaning.",
                      "validation": "Outcome evidence comes from clean/rejected rows, not a separate validation event log.",
                      "placeholders": "Unknown or absent graph nodes are not claims that a stage executed or never executed.",
                      "timestamps": "No per-record validation, clean-write, or reporting-write timestamps are recorded; published_at is batch metadata.",
                      "root_cause": "Not determined by lineage; absence does not establish why or whether a record arrived historically."},
    }


def run_lineage(session: Session, run_id: str) -> dict | None:
    _start_read_snapshot(session)
    with session.no_autoflush:
        facts = _load_facts(session, run_id)
        return _assemble(facts) if facts else None


def incident_lineage(session: Session, incident_id: str) -> dict | None:
    _start_read_snapshot(session)
    with session.no_autoflush:
        incident = session.get(Incident, incident_id)
        if incident is None:
            return None
        facts = _load_facts(session, incident.run_id)
        return _assemble(facts, incident) if facts else None
