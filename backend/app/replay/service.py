from copy import deepcopy
import hashlib

from sqlalchemy.orm import Session

from app.replay.errors import ReplayRejected
from app.replay.inputs import capture_input, fingerprint
from app.replay.operations import SUPPORTED, transform, validate_replay
from app.replay.sandbox import isolated_database
from app.replay.state import evaluate_state, protected_fingerprint

ALGORITHM_VERSION = "safe-replay.v2"


def _identity_set(rows):
    return {(row["raw_order_id"], row["order_id"]) for row in rows}


def compare_states(before: dict, after: dict) -> dict:
    old, new = _identity_set(before["reported_identities"]), _identity_set(after["reported_identities"])
    records = lambda values: [{"raw_order_id": raw_id, "order_id": order_id} for raw_id, order_id in sorted(values)]
    return {"count_deltas": {key: after["counts"][key] - value for key, value in before["counts"].items()},
            "added_reporting_identities": records(new - old), "removed_reporting_identities": records(old - new),
            "validation_results_changed": before["validation_results"] != after["validation_results"],
            "clean_identities_changed": before["clean_identities"] != after["clean_identities"],
            "incident_detection_changes": {key: {"before": before["incident_status"][key]["detected_in_simulation"],
                                                  "after": after["incident_status"][key]["detected_in_simulation"],
                                                  "before_affected_count": before["incident_status"][key]["affected_count"],
                                                  "after_affected_count": after["incident_status"][key]["affected_count"]}
                                           for key in before["incident_status"]}}


def verify_replay(before, after, protected_before, protected_after, operation) -> dict:
    physical_missing = lambda state: {row["raw_order_id"] for row in state["expected_reporting_identities"]} - {row["raw_order_id"] for row in state["reported_identities"]}
    checks = {"raw_ids_unchanged": before["raw_order_ids"] == after["raw_order_ids"],
              "protected_input_evidence_unchanged": protected_before == protected_after,
              "validation_results_unchanged": before["validation_results"] == after["validation_results"],
              "clean_identities_unchanged": before["clean_identities"] == after["clean_identities"],
              "expected_identities_unchanged": before["expected_reporting_identities"] == after["expected_reporting_identities"],
              "raw_counts_reconcile": after["counts"]["source_count"] == after["counts"]["accepted_count"] + after["counts"]["rejected_count"],
              "outputs_are_unique": after["counts"]["duplicate_output_excess_count"] == 0,
              "other_incident_evidence_preserved": all(before["incident_status"][key] == after["incident_status"][key]
                                                       for key in (("DUPLICATE_RECORDS", "SCHEMA_DRIFT", "LATE_ARRIVING_DATA") if operation == "recover_missing_reporting" else ("SCHEMA_DRIFT", "LATE_ARRIVING_DATA")))}
    if operation == "deduplicate_processing":
        duplicate_groups = lambda state: (state["incident_status"]["DUPLICATE_RECORDS"]["evidence"] or {}).get("groups", [])
        checks["raw_duplicate_evidence_preserved"] = duplicate_groups(before) == duplicate_groups(after)
    if operation == "recover_missing_reporting":
        checks["missing_identities_recovered_in_simulation"] = not after["incident_status"]["MISSING_RECORDS"]["detected_in_simulation"]
        checks["no_existing_reporting_identity_removed"] = _identity_set(before["reported_identities"]) <= _identity_set(after["reported_identities"])
    else:
        checks["unrelated_missing_sources_preserved"] = physical_missing(before) == physical_missing(after)
        checks["reporting_source_ids_unchanged"] = {row["raw_order_id"] for row in before["reported_identities"]} == {row["raw_order_id"] for row in after["reported_identities"]}
    return {"passed": all(checks.values()), "checks": checks}


def replay_run(session: Session, run_id: str, incident_id: str, operation: str) -> dict:
    if not isinstance(operation, str) or operation not in SUPPORTED:
        raise ReplayRejected("UNSUPPORTED_OPERATION", "Only missing-reporting and duplicate-processing simulations are supported.", 400)
    source = capture_input(session, run_id, incident_id)
    incident = next(row for row in source.tables["incidents"] if row["id"] == incident_id)
    if incident["incident_type"] != SUPPORTED[operation]:
        raise ReplayRejected("UNSUPPORTED_INCIDENT_OPERATION", "Incident type does not match this replay operation.")
    contract = source.tables["pipeline_contracts"][0]
    identity = {"algorithm_version": ALGORITHM_VERSION, "source_fingerprint": source.source_fingerprint,
                "run_id": run_id, "incident_id": incident_id, "operation": operation}
    report = {"format_version": "1", "algorithm_version": ALGORITHM_VERSION,
              "replay_id": fingerprint(identity), "operation": operation, "status": "failed",
              "applied_to_source": False, "input_references": {
                  **identity, "schema_version": contract["schema_version"],
                  "source_csv_sha256": hashlib.sha256(source.tables["pipeline_runs"][0]["source_csv"].encode("utf-8")).hexdigest(),
                  "raw_order_ids": [row["id"] for row in sorted(source.tables["raw_orders"], key=lambda row: row["row_number"])],
                  "contract_sha256": fingerprint(contract), "lineage_summary": deepcopy(source.lineage_summary)},
              "stored_source_incidents": deepcopy(source.tables["incidents"]),
              "before": None, "after": None, "proposed_correction": None, "comparison": None,
              "verification": {"passed": False, "checks": {}},
              "isolation": {"source_access": "read_only", "temporary_database": "private_in_memory_sqlite",
                            "caller_selected_database_or_code": False, "apply_supported": False, "sandbox_disposed": False},
              "semantics": {"incident_status": "Detection in the copied before/after state; source incident rows are never resolved or changed.",
                            "duplicates": "Raw duplicate evidence remains detectable even when output processing is correct.",
                            "audit": "Deterministic returned JSON; save the report for durable audit. Hashes are integrity fingerprints, not signatures."}}
    lifecycle = {"created": 0, "disposed": 0}
    stage = "sandbox_setup"
    try:
        with isolated_database(source, lifecycle=lifecycle) as sandbox:
            stage = "before_evaluation"
            before = evaluate_state(sandbox, run_id)
            report["before"] = before
            stage = "precondition_validation"
            proposal = validate_replay(source, operation, before, lifecycle=lifecycle)
            report["proposed_correction"] = proposal
            protected_before = protected_fingerprint(sandbox, run_id)
            stage = "temporary_transformation"
            transform(sandbox, source, operation, proposal, before)
            stage = "after_evaluation"
            after = evaluate_state(sandbox, run_id)
            protected_after = protected_fingerprint(sandbox, run_id)
            stage = "verification"
            verification = verify_replay(before, after, protected_before, protected_after, operation)
            verification["checks"]["captured_input_unchanged"] = fingerprint(source.tables) == source.source_fingerprint
            verification["passed"] = all(verification["checks"].values())
            report.update(after=after, verification=verification, comparison=compare_states(before, after))
            if verification["passed"]:
                changed = report["comparison"]["added_reporting_identities"] or report["comparison"]["removed_reporting_identities"]
                report["status"] = "verified" if changed else "no_change_required"
            else:
                report["status"] = "verification_failed"
    except ReplayRejected:
        raise
    except Exception:
        # Never expose source data, database URLs or arbitrary exception messages.
        report.update(status="failed", after=None, comparison=None,
                      verification={"passed": False, "checks": {}},
                      failure={"code": "REPLAY_EXECUTION_FAILED", "stage": stage, "message": "Temporary replay failed; no source corrections were applied."})
    report["isolation"]["temporary_databases_created"] = lifecycle["created"]
    report["isolation"]["temporary_databases_disposed"] = lifecycle["disposed"]
    report["isolation"]["sandbox_disposed"] = lifecycle["created"] == lifecycle["disposed"]
    report["report_sha256"] = fingerprint(report)
    return report
