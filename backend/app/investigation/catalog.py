from dataclasses import dataclass, field
import math
import re

from app.replay.inputs import fingerprint
from app.pipeline.timestamps import parse_event_timestamp

TYPES = {"MISSING_RECORDS", "DUPLICATE_RECORDS", "SCHEMA_DRIFT", "LATE_ARRIVING_DATA"}
HYPOTHESES = {
    "DOWNSTREAM_PROCESSING_GAP": "Reporting delivery, filtering, or identity handling could explain the gap; the cause is unconfirmed.",
    "REPEATED_SOURCE_SUBMISSION": "Repeated source submission or business-ID reuse could explain the duplicate input; neither is established.",
    "PRODUCER_SCHEMA_CHANGE": "A producer schema or serialization change could explain the contract differences; this is unconfirmed.",
    "DELAY_OR_CLOCK_DIFFERENCE": "Delayed delivery or a clock discrepancy could explain lateness; the evidence does not distinguish them.",
}
ACTIONS = {
    "COLLECT_MISSING_EVIDENCE": "Collect the missing contract, manifest, stage, or timing observations before drawing conclusions; do not invent absent events.",
    "INSPECT_REPORTING_PATH": "Inspect reporting delivery/filtering logs and identity mapping for the referenced source, without changing data.",
    "INSPECT_SOURCE_SUBMISSIONS": "Compare source submission identifiers and ingestion records for the duplicate group.",
    "COMPARE_SCHEMA_CONTRACT": "Compare the recorded producer schema with the versioned expected contract.",
    "CHECK_EVENT_AND_ARRIVAL_CLOCKS": "Check event-time provenance, arrival capture, and clock synchronization.",
    "REVIEW_REPLAY_AUDIT": "Review the referenced simulation verification; do not apply a correction automatically.",
}
UNKNOWNS = {
    "ROOT_CAUSE_UNKNOWN": "The recorded evidence does not establish a root cause.",
    "VALIDATION_TIME_NOT_RECORDED": "A separate per-record validation execution timestamp is not recorded.",
    "INCOMPLETE_EVIDENCE": "Some source references or observations are unavailable or inconsistent.",
    "REPLAY_IS_NOT_A_SOURCE_FIX": "Replay audit results describe a simulation, not a correction applied to source data.",
    "CATALOG_TRUNCATED": "The incident exceeds the bounded record context; not all records are included.",
    "INVALID_TIMING_EVIDENCE": "Some stored timing values could not be verified safely.",
}


@dataclass
class EvidenceCatalog:
    run_id: str
    incident_id: str
    incident_type: str
    facts: dict = field(default_factory=dict)
    hypotheses: dict = field(default_factory=dict)
    actions: dict = field(default_factory=dict)
    unknowns: list = field(default_factory=lambda: ["ROOT_CAUSE_UNKNOWN", "VALIDATION_TIME_NOT_RECORDED"])
    records: list = field(default_factory=list)
    replays: list = field(default_factory=list)
    source_context: dict = field(default_factory=dict)

    def add(self, key, text, references, payload):
        self.facts[key] = {"id": key, "statement": text, "evidence_refs": references, "payload": payload}

    def allow(self, hypothesis, action, ids):
        self.hypotheses.setdefault(hypothesis, []).extend(ids)
        self.actions.setdefault(action, []).extend(ids)

    def packet(self):
        # No raw CSV, business/customer IDs, source strings, logs, or model instructions from data.
        return {"incident_type": self.incident_type, "source_context": self.source_context,
                "facts": [{"id": key, **value["payload"]} for key, value in self.facts.items()],
                "allowed_hypotheses": self.hypotheses, "allowed_actions": self.actions,
                "mandatory_unknown_codes": self.unknowns}


def _reference(table, key, **extra):
    return {"kind": "database_row", "table": table, "key": key, **extra}


def _safe_count(value):
    return value if type(value) is int and 0 <= value <= 1000000000 else None


def build_catalog(trace: dict, audits: list[dict]) -> EvidenceCatalog:
    incident = trace["incidents"][0]
    incident_type = incident["incident_type"]
    if incident_type not in TYPES:
        raise ValueError("Unsupported incident type")
    catalog = EvidenceCatalog(trace["run"]["id"], incident["id"], incident_type)
    source = trace["run"].get("source", {})
    kind = source.get("kind") if source.get("kind") in ("local_demo", "uci_real", "uci_controlled") else "local_demo"
    provenance = source.get("provenance", {})
    experiment = provenance.get("experiment")
    if experiment not in ("real", "missing_reporting", "duplicate_processing", "schema_drift", "late_arrival"):
        experiment = None
    simulated_clock = any((row.get("timing") or {}).get("time_source") == "injected_clock" for row in trace["records"])
    catalog.source_context = {"source_kind": kind, "controlled": kind == "uci_controlled",
                              "experiment": experiment, "clock_semantics": "simulated" if simulated_clock else "captured_without_historical_delivery_claim"}
    if kind in ("uci_real", "uci_controlled"):
        context_ref = _reference("dataset_imports", {"run_id": catalog.run_id})
        statement = ("This run is a controlled UCI experiment; injected incidents are not findings about historical source failures."
                     if kind == "uci_controlled" else "This run evaluates preserved UCI worksheet lines; invoice-number repetition alone is not duplicate evidence.")
        catalog.add("CTX", statement, [context_ref], {"kind": "dataset_context", "source_kind": kind, "controlled": kind == "uci_controlled"})
        if simulated_clock:
            catalog.add("CLOCK", "Event and arrival clocks for this run were explicitly simulated; they do not measure historical delivery.",
                        [context_ref], {"kind": "simulated_clock_context"})
    incident_ref = _reference("incidents", {"id": incident["id"], "run_id": catalog.run_id})
    catalog.add("I", f"Stored incident {incident_type} affects {incident['affected_count']} evidence items.",
                [incident_ref], {"kind": "incident_snapshot", "affected_count": incident["affected_count"]})
    evidence = incident["evidence"] if isinstance(incident["evidence"], dict) else {}
    counts = evidence.get("counts", {}) if isinstance(evidence.get("counts", {}), dict) else {}
    if trace["summary"]["evidence_completeness"] != "complete":
        catalog.unknowns.append("INCOMPLETE_EVIDENCE")
        catalog.add("U", "Lineage reports incomplete or inconsistent recorded evidence.",
                    [{"kind": "lineage_response", "run_id": catalog.run_id, "incident_id": catalog.incident_id,
                      "path": "/summary/evidence_completeness"}], {"kind": "evidence_gap"})
        catalog.actions.setdefault("COLLECT_MISSING_EVIDENCE", []).append("U")
    if len(trace["records"]) > 25:
        catalog.unknowns.append("CATALOG_TRUNCATED")
    for index, row in enumerate(trace["records"][:25]):
        raw_id = row["raw_order_id"]
        label = row["source_order_id"]
        safe_label = label if (isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", label)
                               and not re.match(r"(?:sk-|AKIA)", label)) else None
        catalog.records.append({"raw_order_id": raw_id, "display_order_id": safe_label,
                                "last_confirmed_stage": row["last_confirmed_stage"]})
        source_reference = row.get("source_reference")
        raw_statement = (f"Mapped pipeline row {row['row_number']} is stored as raw record {raw_id}; original worksheet row is {source_reference['source_row']}."
                         if source_reference else f"Source row {row['row_number']} is stored as raw record {raw_id}.")
        raw_references = row["raw"]["evidence_refs"] + ([source_reference["evidence_ref"]] if source_reference else [])
        catalog.add(f"R{raw_id}", raw_statement, raw_references,
                    {"kind": "raw_observation", "raw_order_id": raw_id, "row_number": row["row_number"],
                     "worksheet_row": source_reference["source_row"] if source_reference else None})
        if row["validation"]["status"] in ("accepted", "rejected"):
            outcome = row["validation"]["status"]
            catalog.add(f"V{raw_id}", f"Record {raw_id} has a committed {outcome} validation outcome; no separate execution time is recorded.",
                        row["validation"]["evidence_refs"], {"kind": "validation_outcome", "raw_order_id": raw_id, "outcome": outcome})
        if row["clean"]["status"] == "observed":
            catalog.add(f"C{raw_id}", f"Clean record {row['clean']['clean_order_id']} references raw record {raw_id}.",
                        row["clean"]["evidence_refs"], {"kind": "clean_observation", "raw_order_id": raw_id})
        if row["reporting"]["expected_identity"] is not None:
            catalog.add(f"E{raw_id}", f"A reporting manifest records an expected downstream identity for raw record {raw_id}.",
                        row["reporting"]["expectation_refs"], {"kind": "reporting_expectation", "raw_order_id": raw_id})
        if row["reporting"]["observed_identity"] is not None:
            catalog.add(f"P{raw_id}", f"Reporting currently contains a row referencing raw record {raw_id}.",
                        row["reporting"]["evidence_refs"], {"kind": "reporting_observation", "raw_order_id": raw_id})
        if row["reporting"]["missing_expected_identity"] is True:
            ref = {"kind": "lineage_comparison", "run_id": catalog.run_id, "raw_order_id": raw_id,
                   "path": f"/records/{index}/reporting", "identity_sha256": fingerprint(row["reporting"]["expected_identity"])}
            catalog.add(f"G{raw_id}", f"No current reporting row matches the recorded expected identity for raw record {raw_id}; the cause is unknown.",
                        row["reporting"]["expectation_refs"] + [ref], {"kind": "missing_expected_identity", "raw_order_id": raw_id})
            catalog.allow("DOWNSTREAM_PROCESSING_GAP", "INSPECT_REPORTING_PATH", [f"G{raw_id}"])
    if incident_type == "DUPLICATE_RECORDS" and _safe_count(counts.get("duplicate_record_count")):
        number = counts["duplicate_record_count"]
        catalog.add("D", f"The stored snapshot identifies {number} excess valid raw duplicate records; input preservation does not imply duplicated clean output.",
                    [{**incident_ref, "path": "/evidence/counts/duplicate_record_count"}], {"kind": "raw_duplicates", "duplicate_record_count": number})
        catalog.allow("REPEATED_SOURCE_SUBMISSION", "INSPECT_SOURCE_SUBMISSIONS", ["D"])
    if incident_type == "DUPLICATE_RECORDS" and _safe_count(counts.get("duplicate_output_excess_count")):
        number = counts["duplicate_output_excess_count"]
        catalog.add("OD", f"The stored snapshot records {number} excess reporting identity collisions; invoice-number repetition alone is not duplicate evidence.",
                    [{**incident_ref, "path": "/evidence/output_groups"}], {"kind": "reporting_duplicates", "duplicate_output_excess_count": number})
        catalog.allow("DOWNSTREAM_PROCESSING_GAP", "INSPECT_REPORTING_PATH", ["OD"])
    if incident_type == "SCHEMA_DRIFT" and _safe_count(counts.get("difference_count")):
        number = counts["difference_count"]
        catalog.add("S", f"The stored schema snapshot records {number} exact differences against its expected versioned contract.",
                    [{**incident_ref, "path": "/evidence/differences"}], {"kind": "schema_differences", "difference_count": number})
        catalog.allow("PRODUCER_SCHEMA_CHANGE", "COMPARE_SCHEMA_CONTRACT", ["S"])
    if incident_type == "LATE_ARRIVING_DATA":
        rows = evidence.get("late_records", [])
        valid = 0
        for index, late in enumerate(rows[:25] if isinstance(rows, list) else []):
            if not isinstance(late, dict) or not all(isinstance(late.get(key), str) for key in ("event_timestamp", "expected_deadline", "actual_arrival_timestamp")):
                continue
            try:
                event = parse_event_timestamp(late["event_timestamp"])
                deadline = parse_event_timestamp(late["expected_deadline"])
                arrival = parse_event_timestamp(late["actual_arrival_timestamp"])
                delay = late["delay_seconds"]
                threshold = evidence["threshold_seconds"]
                if (type(delay) not in (int, float) or not math.isfinite(delay) or delay <= 0
                        or _safe_count(threshold) is None or (deadline - event).total_seconds() != threshold
                        or (arrival - deadline).total_seconds() != delay or type(late["raw_order_id"]) is not int
                        or late["raw_order_id"] not in {r["raw_order_id"] for r in trace["records"]}):
                    raise ValueError
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            key = f"T{index}"
            catalog.add(key, f"Stored timing evidence records raw record {late['raw_order_id']} arriving {delay:g} seconds beyond its deadline.",
                        [{**incident_ref, "path": f"/evidence/late_records/{index}"}],
                        {"kind": "late_arrival_snapshot", "raw_order_id": late["raw_order_id"], "delay_seconds": delay, "threshold_seconds": threshold})
            catalog.allow("DELAY_OR_CLOCK_DIFFERENCE", "CHECK_EVENT_AND_ARRIVAL_CLOCKS", [key])
            valid += 1
        if not valid:
            catalog.unknowns.append("INVALID_TIMING_EVIDENCE")
    for index, audit in enumerate(audits):
        body = audit["report"]
        key = f"A{index}"
        catalog.add(key, f"Durable replay audit {audit['id'][:12]} records outcome {body['outcome']} for a simulation; no source correction was applied.",
                    [_reference("replay_audits", {"id": audit["id"], "run_id": catalog.run_id, "incident_id": catalog.incident_id})],
                    {"kind": "replay_simulation", "outcome": body["outcome"], "verification_passed": body["verification"]["passed"], "applied_to_source": False})
        catalog.actions.setdefault("REVIEW_REPLAY_AUDIT", []).append(key)
        catalog.replays.append({"audit_id": audit["id"], "replay_id": audit["replay_id"], "outcome": body["outcome"],
                               "operation": body["operation"], "verification": body["verification"],
                               "before_counts": (body.get("before") or {}).get("counts"),
                               "after_counts": (body.get("after") or {}).get("counts"), "applied_to_source": False,
                               "applicability": "Captured-input simulation; current applicability is not assumed."})
    if audits:
        catalog.unknowns.append("REPLAY_IS_NOT_A_SOURCE_FIX")
    return catalog
