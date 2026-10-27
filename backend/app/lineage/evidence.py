"""Locate source identities explicitly referenced by stored incident evidence."""
from dataclasses import dataclass, field

from app.models import Incident


@dataclass
class IncidentReferences:
    records: dict[int, list[dict]] = field(default_factory=dict)
    run_scoped: bool = False
    issues: list[dict] = field(default_factory=list)


def incident_references(incident: Incident, run_raw_ids: set[int]) -> IncidentReferences:
    result = IncidentReferences()
    evidence = incident.evidence
    if not isinstance(evidence, dict):
        result.issues.append({"code": "INVALID_INCIDENT_EVIDENCE_SHAPE", "incident_id": incident.id})
        return result
    if evidence.get("run_id", incident.run_id) != incident.run_id:
        result.issues.append({"code": "INCIDENT_EVIDENCE_RUN_MISMATCH", "incident_id": incident.id})
        return result

    def add(raw_id, path, role):
        if type(raw_id) is not int or raw_id not in run_raw_ids:
            result.issues.append({"code": "UNRESOLVED_INCIDENT_SOURCE", "incident_id": incident.id,
                                  "raw_order_id": raw_id if type(raw_id) is int else None, "evidence_path": path})
            return
        result.records.setdefault(raw_id, []).append({"incident_id": incident.id,
            "incident_type": incident.incident_type, "role": role, "evidence_path": path})

    def items(container, key):
        value = container.get(key, []) if isinstance(container, dict) else []
        return value if isinstance(value, list) else []

    if incident.incident_type == "DUPLICATE_RECORDS":
        for index, group in enumerate(items(evidence, "groups")):
            if not isinstance(group, dict):
                continue
            add(group.get("first_raw_order_id"), f"/groups/{index}/first_raw_order_id", "first_source_at_detection")
            for number, duplicate in enumerate(items(group, "duplicates")):
                if isinstance(duplicate, dict):
                    add(duplicate.get("raw_order_id"), f"/groups/{index}/duplicates/{number}", "duplicate_at_detection")
        for index, group in enumerate(items(evidence, "output_groups")):
            for raw_id in items(group, "raw_order_ids"):
                add(raw_id, f"/output_groups/{index}", "duplicate_reporting_identity_at_detection")
    elif incident.incident_type == "MISSING_RECORDS":
        for key, role in (("missing_records", "missing_at_detection"), ("unexpected_records", "unexpected_at_detection")):
            for index, row in enumerate(items(evidence, key)):
                if isinstance(row, dict):
                    add(row.get("raw_order_id"), f"/{key}/{index}", role)
    elif incident.incident_type == "LATE_ARRIVING_DATA":
        for index, row in enumerate(items(evidence, "late_records")):
            if isinstance(row, dict):
                add(row.get("raw_order_id"), f"/late_records/{index}", "late_at_detection")
    elif incident.incident_type == "SCHEMA_DRIFT":
        differences = evidence.get("differences", {})
        result.run_scoped = any(items(differences, key) for key in (
            "missing_required_columns", "unexpected_columns", "duplicate_columns"))
        if result.run_scoped:
            for raw_id in sorted(run_raw_ids):
                add(raw_id, "/differences", "run_input_schema_scope")
        for key in ("incompatible_types", "row_width_mismatches"):
            for index, row in enumerate(items(differences, key)):
                if isinstance(row, dict):
                    add(row.get("raw_order_id"), f"/differences/{key}/{index}", "schema_difference_at_detection")
    if not result.records and not result.run_scoped and not result.issues:
        result.issues.append({"code": "INCIDENT_SOURCE_REFERENCES_NOT_RECORDED", "incident_id": incident.id})
    return result
