"""Audit persistence is separate from the source snapshot and sandbox writes."""
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.lineage.service import start_read_snapshot
from app.models import Incident, ReplayAudit
from app.replay.errors import ReplayRejected
from app.replay.inputs import canonical_json, fingerprint
from app.replay.operations import SUPPORTED


def _state(state):
    if state is None:
        return None
    return {"counts": deepcopy(state["counts"]),
            "identities": {key: [{"raw_order_id": row["raw_order_id"], "identity_sha256": fingerprint(row)} for row in state[key]]
                           for key in ("clean_identities", "expected_reporting_identities", "reported_identities")},
            "validation": [{"raw_order_id": row["raw_order_id"], "status": row["status"],
                            "reason_code": row.get("reason_code"), "duplicate_of_raw_id": row.get("duplicate_of_raw_id")}
                           for row in state["validation_results"]],
            "incident_status": {key: {"detected_in_simulation": value["detected_in_simulation"], "affected_count": value["affected_count"]}
                                for key, value in state["incident_status"].items()}}


def audit_projection(report: dict) -> dict:
    unhashed = deepcopy(report)
    digest = unhashed.pop("report_sha256", None)
    if digest != fingerprint(unhashed) or report.get("applied_to_source") is not False:
        raise ReplayRejected("INVALID_AUDIT_REPORT", "Simulation report failed integrity verification.")
    refs = report["input_references"]
    expected_identity = {"algorithm_version": report["algorithm_version"], "source_fingerprint": refs["source_fingerprint"],
                         "run_id": refs["run_id"], "incident_id": refs["incident_id"], "operation": report["operation"]}
    if (report["operation"] not in SUPPORTED or report["replay_id"] != fingerprint(expected_identity)
            or refs.get("operation") != report["operation"] or refs.get("algorithm_version") != report["algorithm_version"]):
        raise ReplayRejected("INVALID_AUDIT_REPORT", "Simulation identity or operation is inconsistent.")
    proposal = report.get("proposed_correction") or {}
    # Do not retain raw CSV, customer IDs, source values, model text, or free-form exception strings.
    return {"format_version": "1", "replay_id": report["replay_id"], "run_id": refs["run_id"],
            "incident_id": refs["incident_id"], "operation": report["operation"], "outcome": report["status"],
            "algorithm_version": report["algorithm_version"], "input_fingerprint": refs["source_fingerprint"],
            "source_csv_sha256": refs["source_csv_sha256"], "contract_sha256": refs["contract_sha256"],
            "simulation_sha256": digest, "applied_to_source": False,
            "before": _state(report["before"]), "after": _state(report["after"]),
            "verification": deepcopy(report["verification"]), "isolation": deepcopy(report["isolation"]),
            "proposal": {"kind": proposal.get("kind"), "scope": "temporary_simulation_only",
                         "raw_order_ids": [row["raw_order_id"] for row in proposal.get("identities", proposal.get("reporting_identity_changes", []))]},
            "comparison": {"count_deltas": deepcopy((report.get("comparison") or {}).get("count_deltas", {})),
                           "validation_results_changed": (report.get("comparison") or {}).get("validation_results_changed")},
            "failure": {"code": "REPLAY_EXECUTION_FAILED", "stage": report["failure"].get("stage")}
                       if report.get("failure") else None,
            "source_evidence": [{"incident_id": row["id"], "incident_type": row["incident_type"],
                                 "evidence_sha256": fingerprint(row["evidence"])} for row in report["stored_source_incidents"]]}


def _checked(row: ReplayAudit) -> dict:
    body = row.report
    if not isinstance(body, dict):
        raise ReplayRejected("AUDIT_INTEGRITY_ERROR", "Stored audit record failed integrity verification.")
    identity = fingerprint({"replay_id": row.replay_id, "simulation_sha256": row.simulation_sha256})
    if (identity != row.id or fingerprint(body) != row.audit_sha256 or body.get("run_id") != row.run_id
            or body.get("incident_id") != row.incident_id or body.get("operation") != row.operation
            or body.get("outcome") != row.outcome or body.get("input_fingerprint") != row.input_fingerprint
            or body.get("replay_id") != row.replay_id or body.get("simulation_sha256") != row.simulation_sha256
            or body.get("algorithm_version") != row.algorithm_version or row.applied_to_source is not False
            or body.get("applied_to_source") is not False):
        raise ReplayRejected("AUDIT_INTEGRITY_ERROR", "Stored audit record failed integrity verification.")
    return {"id": row.id, "replay_id": row.replay_id, "run_id": row.run_id, "incident_id": row.incident_id,
            "created_at": row.created_at, "audit_sha256": row.audit_sha256, "report": deepcopy(body)}


def persist_audit(bind, report: dict) -> dict:
    """Write only replay_audits using a new transaction, never the source session."""
    projection = audit_projection(report)
    audit_id = fingerprint({"replay_id": projection["replay_id"], "simulation_sha256": projection["simulation_sha256"]})
    with Session(bind, expire_on_commit=False) as writer:
        with writer.begin():
            incident = writer.get(Incident, projection["incident_id"])
            if incident is None or incident.run_id != projection["run_id"] or incident.incident_type != SUPPORTED[projection["operation"]]:
                raise ReplayRejected("AUDIT_REFERENCE_MISMATCH", "Audit source references are inconsistent.")
            existing = writer.get(ReplayAudit, audit_id)
            if existing is None:
                existing = ReplayAudit(id=audit_id, replay_id=projection["replay_id"], run_id=projection["run_id"],
                    incident_id=projection["incident_id"], operation=projection["operation"],
                    input_fingerprint=projection["input_fingerprint"], algorithm_version=projection["algorithm_version"],
                    outcome=projection["outcome"], simulation_sha256=projection["simulation_sha256"],
                    audit_sha256=fingerprint(projection), applied_to_source=False, report=projection)
                try:
                    with writer.begin_nested():
                        writer.add(existing)
                        writer.flush()
                except IntegrityError:
                    existing = writer.get(ReplayAudit, audit_id)
                    if existing is None:
                        verified = writer.scalar(select(ReplayAudit).where(ReplayAudit.replay_id == projection["replay_id"],
                            ReplayAudit.outcome.in_(["verified", "no_change_required"])))
                        if verified is not None and projection["outcome"] in ("verified", "no_change_required"):
                            raise ReplayRejected("AUDIT_SUCCESS_CONFLICT", "This replay input already has a different verified result.") from None
                        raise ReplayRejected("AUDIT_REFERENCE_MISMATCH", "Audit references changed before persistence.") from None
            result = _checked(existing)
            if result["report"] != projection:
                raise ReplayRejected("AUDIT_CONFLICT", "An audit identifier has inconsistent stored content.")
        return result


def retrieve_audit(session: Session, run_id: str, audit_id: str) -> dict | None:
    start_read_snapshot(session)
    row = session.scalar(select(ReplayAudit).where(ReplayAudit.id == audit_id, ReplayAudit.run_id == run_id))
    return _checked(row) if row else None


def relevant_audits(session: Session, run_id: str, incident_id: str) -> list[dict]:
    rows = session.scalars(select(ReplayAudit).where(ReplayAudit.run_id == run_id, ReplayAudit.incident_id == incident_id)
                           .order_by(ReplayAudit.created_at, ReplayAudit.id).limit(20)).all()
    return [_checked(row) for row in rows]
