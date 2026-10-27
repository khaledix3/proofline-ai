from copy import deepcopy
from collections import Counter
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import CleanOrder, RejectedOrder, ReportedOrder, ReportingExpectedOrder, ReportingStage
from app.pipeline.reporting import publish_reporting
from app.pipeline.service import persist_order_outcomes
from app.replay.errors import ReplayRejected
from app.replay.inputs import fingerprint
from app.replay.sandbox import isolated_database
from app.replay.state import identities, validation_results

Operation = Literal["recover_missing_reporting", "deduplicate_processing"]
SUPPORTED = {"recover_missing_reporting": "MISSING_RECORDS", "deduplicate_processing": "DUPLICATE_RECORDS"}


def validate_replay(source, operation: str, before: dict, *, lifecycle=None) -> dict:
    incident = next(row for row in source.tables["incidents"] if row["id"] == source.incident_id)
    if incident["incident_type"] != SUPPORTED[operation]:
        raise ReplayRejected("UNSUPPORTED_INCIDENT_OPERATION", "The incident type is not supported by this replay operation.")
    if before["incident_status"]["SCHEMA_DRIFT"]["detected_in_simulation"]:
        raise ReplayRejected("UNSUPPORTED_SCHEMA_DRIFT", "Replay cannot reinterpret schema-drift inputs.")
    current = before["incident_status"][incident["incident_type"]]["evidence"]
    comparable = deepcopy(current)
    # Legacy duplicate snapshots described raw duplicates only. Preserve that meaning;
    # downstream safety still comes from the independently checked manifest below.
    if operation == "deduplicate_processing" and "output_groups" not in incident["evidence"] and comparable is not None:
        for key in ("output_groups", "output_stage", "output_rule"):
            comparable.pop(key, None)
        comparable["counts"].pop("duplicate_output_excess_count", None)
        comparable["affected_order_ids"] = sorted(group["order_id"] for group in comparable["groups"])
        comparable["stage"] = "raw_to_clean"
    if (operation == "deduplicate_processing" and comparable is not None
            and incident["evidence"].get("output_stage") == "reporting"
            and incident["evidence"].get("stage") == "raw_to_clean"
            and comparable.get("output_stage") == "reporting"):
        # Early output snapshots used the legacy category stage plus explicit output_stage.
        # Preserve their stored JSON; normalize only this metadata field for comparison.
        comparable["stage"] = "raw_to_clean"
    if comparable is None or fingerprint(comparable) != fingerprint(incident["evidence"]):
        raise ReplayRejected("STALE_INCIDENT_EVIDENCE", "Stored incident evidence does not match the current source observation.")
    # Independently reuse the real processor against raw-only temporary data.
    with isolated_database(source, inputs_only=True, lifecycle=lifecycle) as validation_session:
        persist_order_outcomes(validation_session, source.run_id)
        expected_validation = validation_results(validation_session, source.run_id)
    if expected_validation != before["validation_results"]:
        raise ReplayRejected("SOURCE_VALIDATION_MISMATCH", "Recorded outputs do not match deterministic validation of preserved input.")
    expected = {row["raw_order_id"]: row["order_id"] for row in before["expected_reporting_identities"]}
    clean = {row["raw_order_id"]: row["order_id"] for row in before["clean_identities"]}
    if expected != clean:
        raise ReplayRejected("MANIFEST_VALIDATION_MISMATCH", "Reporting expectations do not match validated source identities.")
    if operation == "recover_missing_reporting":
        if current["counts"]["unexpected_count"] or before["counts"]["duplicate_output_excess_count"]:
            raise ReplayRejected("UNSAFE_DOWNSTREAM_STATE", "Missing-record replay only supports absent identities, not replacement of unexpected output.")
        return {"kind": "add_missing_reporting_identities", "scope": "temporary_simulation_only",
                "identities": deepcopy(current["missing_records"])}
    business_ids = Counter(row["order_id"] for row in before["reported_identities"])
    duplicated_ids = {order_id for order_id, count in business_ids.items() if count > 1}
    changes = []
    for row in before["reported_identities"]:
        if row["raw_order_id"] not in expected:
            raise ReplayRejected("UNSUPPORTED_REPORTING_SOURCE", "Reporting includes a source not present in the saved manifest.")
        correct_id = expected[row["raw_order_id"]]
        if row["order_id"] != correct_id:
            if row["order_id"] not in duplicated_ids:
                raise ReplayRejected("UNRELATED_IDENTITY_MISMATCH", "Duplicate replay cannot repair unrelated identity substitutions.")
            changes.append({"raw_order_id": row["raw_order_id"], "before_order_id": row["order_id"], "after_order_id": correct_id})
    return {"kind": "revalidate_first_valid_wins_and_correct_duplicate_projection",
            "scope": "temporary_simulation_only", "reporting_identity_changes": changes,
            "preserve_existing_missing_source_ids": sorted(set(expected) - {row["raw_order_id"] for row in before["reported_identities"]}),
            "raw_duplicate_policy": "Preserve every raw row; retain one accepted winner and explicit duplicate rejections."}


def transform(session: Session, source, operation: str, proposal: dict, before: dict) -> None:
    if operation == "recover_missing_reporting":
        session.add_all([ReportedOrder(run_id=source.run_id, raw_order_id=row["raw_order_id"], order_id=row["order_id"])
                         for row in proposal["identities"]])
        session.flush()
        return
    if operation != "deduplicate_processing":
        raise ReplayRejected("UNSUPPORTED_OPERATION", "Unsupported replay operation.", 400)
    present_sources = {row["raw_order_id"] for row in before["reported_identities"]}
    stage = source.tables["reporting_stages"][0]
    # Delete/rebuild only the private sandbox's derived output, never its raw evidence.
    for model in (ReportedOrder, ReportingExpectedOrder, ReportingStage, CleanOrder, RejectedOrder):
        session.execute(delete(model).where(model.run_id == source.run_id), execution_options={"synchronize_session": False})
    session.flush()
    session.expunge_all()
    persist_order_outcomes(session, source.run_id)
    publish_reporting(session, source.run_id)
    session.execute(delete(ReportedOrder).where(ReportedOrder.run_id == source.run_id,
                                              ReportedOrder.raw_order_id.not_in(present_sources)))
    copied_stage = session.get(ReportingStage, source.run_id)
    copied_stage.published_at = stage["published_at"]
    copied_stage.fault = stage["fault"]
    session.flush()
