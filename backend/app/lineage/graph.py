"""Project persisted observations into stable graph nodes, never event history."""


def evidence_ref(table: str, **key) -> dict:
    return {"table": table, "key": key}


def build_graph(run_id: str, records: list[dict], incidents: list[dict], run_scoped_ids: set[str]) -> tuple[list, list]:
    nodes = [{"id": f"{run_id}:context", "kind": "run_context", "stage": None,
              "status": "recorded", "evidence_refs": [evidence_ref("pipeline_runs", id=run_id)]}]
    edges = []
    returned_raw_ids = {row["raw_order_id"] for row in records}

    def edge(source, target, relation, evidence_kind, refs=None):
        edges.append({"id": f"{source}->{target}:{relation}", "source": source, "target": target,
                      "relation": relation, "evidence_kind": evidence_kind, "evidence_refs": refs or []})

    for record in records:
        raw_id = record["raw_order_id"]
        ids = {stage: f"{run_id}:{stage}:{raw_id}" for stage in ("raw", "validation", "clean", "reporting_expectation", "reporting")}
        for stage in ("raw", "validation", "clean", "reporting"):
            state = record[stage]
            nodes.append({"id": ids[stage], "raw_order_id": raw_id, "stage": stage,
                          "kind": state["evidence_kind"], "status": state["status"],
                          "evidence_refs": state["evidence_refs"], "execution_timestamp": None})
        if record["validation"]["status"] != "unknown":
            edge(ids["raw"], ids["validation"], "validation_outcome", "persisted_outcome",
                 record["validation"]["evidence_refs"])
        if record["clean"]["status"] == "observed":
            edge(ids["validation"], ids["clean"], "accepted_source", "persisted_relationship", record["clean"]["evidence_refs"])
        elif record["clean"]["status"] == "intentionally_excluded":
            edge(ids["validation"], ids["clean"], "intentionally_excluded", "persisted_rejection", record["validation"]["evidence_refs"])
        expected = record["reporting"]["expected_identity"]
        if expected is not None:
            nodes.append({"id": ids["reporting_expectation"], "raw_order_id": raw_id, "stage": "reporting",
                          "kind": "recorded_expectation", "status": "expected", "identity": expected,
                          "evidence_refs": record["reporting"]["expectation_refs"]})
            edge(ids["clean"], ids["reporting_expectation"], "expected_downstream_identity", "recorded_expectation",
                 record["reporting"]["expectation_refs"])
            if record["reporting"]["missing_expected_identity"] is True:
                edge(ids["reporting_expectation"], ids["reporting"], "expected_identity_not_observed",
                     "identity_comparison", record["reporting"]["expectation_refs"])
            elif record["reporting"]["status"] == "observed":
                edge(ids["reporting_expectation"], ids["reporting"], "matches_expectation", "persisted_observation",
                     record["reporting"]["evidence_refs"])
        elif record["reporting"]["status"] == "observed":
            edge(ids["clean"], ids["reporting"], "reported_source", "persisted_relationship", record["reporting"]["evidence_refs"])
        duplicate = record["duplicate_relationship"]
        if duplicate and duplicate["role"] == "rejected_duplicate":
            target = duplicate["referenced_raw_order_id"]
            if target in returned_raw_ids:
                edge(ids["raw"], f"{run_id}:raw:{target}", "duplicate_of", "persisted_reference", record["validation"]["evidence_refs"])
    for incident in incidents:
        incident_id = f"{run_id}:incident:{incident['id']}"
        nodes.append({"id": incident_id, "kind": "incident_snapshot", "stage": incident["stage"],
                      "status": "recorded", "incident_type": incident["incident_type"],
                      "evidence_refs": [evidence_ref("incidents", id=incident["id"], run_id=run_id)]})
        if incident["id"] in run_scoped_ids:
            edge(incident_id, f"{run_id}:context", "run_schema_evidence", "stored_snapshot_reference")
        for record in records:
            if any(link["incident_id"] == incident["id"] for link in record["incident_links"]):
                edge(incident_id, f"{run_id}:raw:{record['raw_order_id']}", "evidence_reference", "stored_snapshot_reference")
    return nodes, edges
