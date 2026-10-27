"""Explicit, repeatable UCI experiments; never mutate a pre-existing pipeline run."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import io
from threading import RLock
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.datasets.source import (RetailSubset, DATASET_URL, DOWNLOAD_URL, CITATION, MAPPING_VERSION,
                                 source_identity, map_line, classify_line)
from app.models import (DatasetImport, DatasetSourceRecord, RawOrder, CleanOrder, RejectedOrder,
                        PipelineRun, ReportedOrder, Incident, utc_now)
from app.pipeline.service import persist_raw_orders, persist_order_outcomes, reconcile_counts
from app.pipeline.reporting import publish_reporting
from app.pipeline.validation import COLUMNS
from app.pipeline.contracts import validate_contract_order
from app.incidents.detection import detect_incidents
from app.replay.inputs import fingerprint

Experiment = Literal["real", "missing_reporting", "duplicate_processing", "schema_drift", "late_arrival"]
EXPERIMENTS = {"real", "missing_reporting", "duplicate_processing", "schema_drift", "late_arrival"}
_LOCAL_LOCK = RLock()

@contextmanager
def import_lock(engine, key):
    # Hold a dedicated PG advisory lock across the ingestion/processing transactions.
    with _LOCAL_LOCK:
        if engine.dialect.name != "postgresql":
            yield
            return
        with engine.connect() as lock_connection:
            lock_id = int(key[:16], 16) - 2**63
            lock_connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_id})
            lock_connection.commit()
            try:
                yield
            finally:
                lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
                lock_connection.commit()


def source_summary(dataset):
    if dataset is None:
        return {"kind": "local_demo", "controlled": False}
    return {"kind": dataset.source_kind, "controlled": dataset.source_kind == "uci_controlled",
            "dataset": "UCI Online Retail (352)", "provenance": dataset.provenance,
            "evaluation": dataset.evaluation, "workbook_sha256": dataset.workbook_sha256,
            "subset_sha256": dataset.subset_sha256}


def get_source_summary(session, run_id):
    return source_summary(session.get(DatasetImport, run_id))


def import_subset(engine, subset: RetailSubset, experiment: Experiment = "real") -> dict:
    if experiment not in EXPERIMENTS or not 1 <= len(subset.rows) <= 2000:
        raise ValueError("Unsupported or oversized dataset experiment")
    row_numbers = [row["source_row"] for row in subset.rows]
    if len(set(row_numbers)) != len(row_numbers):
        raise ValueError("Subset repeats a worksheet source identity")
    key = fingerprint({"workbook": subset.workbook_sha256, "subset": subset.digest,
                       "mapping": MAPPING_VERSION, "experiment": experiment, "fault_version": "1"})
    with import_lock(engine, key):
        with Session(engine) as session:
            existing = session.scalar(select(DatasetImport).where(DatasetImport.import_key == key))
            if existing is not None:
                if session.get(PipelineRun, existing.run_id).status != "completed":
                    raise ValueError("This import already exists but is not completed; inspect it rather than duplicating it")
                return {"run_id": existing.run_id, "reused": True, "source": source_summary(existing)}
            session.rollback()
            mapped = [map_line(subset, row) for row in subset.rows]
            eligible = [index for index, row in enumerate(mapped) if validate_contract_order(row)[1] is None]
            if experiment != "real" and len(eligible) < 2:
                raise ValueError("Controlled experiments require at least two accepted source lines")
            columns = list(COLUMNS)
            clock = None
            threshold = None
            version = "orders.v1"
            if experiment in ("schema_drift", "late_arrival"):
                version = "orders.v2"
                columns.append("event_timestamp")
                clock = datetime(2026, 1, 1, 10, 5, tzinfo=timezone.utc)
                threshold = 300
                for row in mapped:
                    row["event_timestamp"] = "2026-01-01T10:00:00Z"
                if experiment == "late_arrival":
                    mapped[eligible[0]]["event_timestamp"] = "2026-01-01T09:59:00Z"
                else:
                    columns.remove("customer_id")
                    columns.append("controlled_extra")
                    for row in mapped:
                        row["controlled_extra"] = "injected"
                    mapped[eligible[0]]["quantity"] = "two"
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            writer.writerows(mapped)
            run_id = persist_raw_orders(session, stream.getvalue(), f"uci352_{experiment}_{subset.digest[:10]}.csv",
                        schema_version=version, ingested_at=clock, lateness_seconds=threshold)
            provenance = {"dataset_id": 352, "source_url": DATASET_URL, "download_url": DOWNLOAD_URL,
                "citation": CITATION, "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "sheet": subset.sheet, "selection": subset.selection, "source_row_count": len(subset.rows),
                "mapping_version": MAPPING_VERSION, "identity_semantics": "worksheet source line, not unique invoice/order",
                "experiment": experiment, "injected": experiment != "real",
                "source_timezone": "unspecified; original naive InvoiceDate preserved",
                "arrival_semantics": "captured import time; historical delivery lateness unavailable" if clock is None else "entire event/arrival clock is synthetic, independent of InvoiceDate",
                "customer_semantics": "deterministic derived reference; original customer values retained privately, not exposed by source summary",
                "mapping": {"order_id": "workbook hash prefix + Excel row number", "customer_id": "derived CustomerID hash reference or blank",
                            "quantity": "Quantity without alteration", "unit_price": "UnitPrice without rounding",
                            "order_date": "InvoiceDate calendar date; original full naive value retained"}}
            # Preserve original workbook values before processing, just as the pipeline
            # preserves mapped raw input. A processing failure must not lose provenance.
            with session.begin():
                raw = session.scalars(select(RawOrder).where(RawOrder.run_id == run_id).order_by(RawOrder.row_number)).all()
                dataset = DatasetImport(run_id=run_id, import_key=key, workbook_sha256=subset.workbook_sha256,
                    subset_sha256=subset.digest, source_kind="uci_real" if experiment == "real" else "uci_controlled",
                    provenance=provenance, evaluation={"status": "pending", "counts": {"source_count":len(raw),"accepted_count":0,"rejected_count":0,"duplicate_count":0}, "categories":{}, "rejection_reasons":{}, "source_observations":{}, "incident_counts":{}, "controlled_ground_truth":None})
                session.add(dataset)
                session.flush()
                session.add_all([DatasetSourceRecord(raw_order_id=record.id, run_id=run_id,
                    source_row=source_row["source_row"], source_identity=source_identity(subset, source_row),
                    original_values=source_row["original"]) for source_row, record in zip(subset.rows, raw)])
            try:
                with session.begin():
                    persist_order_outcomes(session, run_id)
                    counts = reconcile_counts(session, run_id)
                    run = session.get(PipelineRun, run_id)
                    for name, value in counts.items():
                        setattr(run, name, value)
                    run.status = "completed"
                    run.finished_at = utc_now()
                    publish_reporting(session, run_id)
                    raw = session.scalars(select(RawOrder).where(RawOrder.run_id == run_id).order_by(RawOrder.row_number)).all()
                    reports = session.scalars(select(ReportedOrder).where(ReportedOrder.run_id == run_id).order_by(ReportedOrder.raw_order_id)).all()
                    target_ids = []
                    if experiment == "missing_reporting":
                        target_ids = [reports[0].raw_order_id]
                        session.delete(reports[0])  # This newly created, explicitly controlled run ONLY.
                    elif experiment == "duplicate_processing":
                        target_ids = [reports[0].raw_order_id, reports[1].raw_order_id]
                        reports[1].order_id = reports[0].order_id  # Inject an output identity collision, not a repeated invoice.
                    elif experiment in ("schema_drift", "late_arrival"):
                        target_ids = [raw[eligible[0]].id]
                    session.flush()
                    incidents = detect_incidents(session, run_id)
                    accepted = set(session.scalars(select(CleanOrder.raw_order_id).where(CleanOrder.run_id == run_id)))
                    categories = {}
                    for source_row, record in zip(subset.rows, raw):
                        category = classify_line(source_row["original"])
                        bucket = categories.setdefault(category, {"source": 0, "accepted": 0, "rejected": 0})
                        bucket["source"] += 1
                        bucket["accepted" if record.id in accepted else "rejected"] += 1
                    expected = {"real": {}, "missing_reporting": {"MISSING_RECORDS": 1},
                                "duplicate_processing": {"DUPLICATE_RECORDS": 1, "MISSING_RECORDS": 1},
                                "schema_drift": {"SCHEMA_DRIFT": 3}, "late_arrival": {"LATE_ARRIVING_DATA": 1}}[experiment]
                    actual = {row.incident_type: row.affected_count for row in incidents}
                    invoices = Counter(str(row["original"].get("InvoiceNo")) for row in subset.rows)
                    evaluation = {"status": "completed", "counts": counts, "categories": categories,
                        "rejection_reasons": dict(Counter(session.scalars(select(RejectedOrder.reason_code).where(RejectedOrder.run_id == run_id)))),
                        "source_observations": {"missing_customer_reference": sum(row["original"].get("CustomerID") is None for row in subset.rows),
                            "missing_description": sum(row["original"].get("Description") is None for row in subset.rows),
                            "repeated_invoice_excess_lines": sum(n - 1 for n in invoices.values() if n > 1),
                            "repeated_invoice_semantics": "legitimate multi-line invoice candidates, not duplicate incidents",
                            "exact_content_repeat_candidates": len(subset.rows) - len({fingerprint(row["original"]) for row in subset.rows})},
                        "incident_counts": actual, "controlled_ground_truth": {"expected_incident_counts": expected, "target_raw_ids": target_ids,
                            "detector_matches_expected": actual == expected} if experiment != "real" else None,
                        "caveat": "Contract exclusions and missing source fields are not evidence of fraud or pipeline corruption."}
                    dataset = session.get(DatasetImport, run_id)
                    dataset.evaluation = evaluation
                return {"run_id": run_id, "reused": False, "source": source_summary(dataset)}
            except Exception:
                with session.begin():
                    run = session.get(PipelineRun, run_id)
                    run.status, run.error_code, run.finished_at = "failed", "DATASET_PROCESSING_FAILED", utc_now()
                    dataset = session.get(DatasetImport, run_id)
                    dataset.evaluation = {**dataset.evaluation, "status": "failed", "error_code": "DATASET_PROCESSING_FAILED"}
                raise
