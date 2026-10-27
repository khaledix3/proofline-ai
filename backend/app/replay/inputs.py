from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.lineage.service import load_run_facts, run_lineage, start_read_snapshot
from app.models import (CleanOrder, Incident, PipelineContract, PipelineRun, RawOrder,
                        RecordArrival, RejectedOrder, ReportedOrder, ReportingExpectedOrder, ReportingStage, DatasetImport, DatasetSourceRecord)
from app.pipeline.contracts import get_contract
from app.pipeline.ingestion import parse_orders_csv
from app.replay.errors import ReplayRejected

MODELS = (PipelineRun, RawOrder, PipelineContract, RecordArrival, CleanOrder, RejectedOrder,
          ReportingStage, ReportingExpectedOrder, ReportedOrder, Incident, DatasetImport, DatasetSourceRecord)
MAX_RECORDS = 10000
MAX_SOURCE_BYTES = 5000000


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError("Unsupported report value")


def canonical_json(value) -> str:
    return json.dumps(value, default=json_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReplayInput:
    run_id: str
    incident_id: str
    tables: dict
    lineage_summary: dict
    source_fingerprint: str


def capture_input(session: Session, run_id: str, incident_id: str) -> ReplayInput:
    bind_transaction = getattr(session.get_bind(), "in_transaction", lambda: False)()
    if session.in_transaction() or bind_transaction or session.new or session.dirty or session.deleted:
        raise ReplayRejected("SOURCE_SESSION_NOT_FRESH", "Replay requires a fresh read-only source session.")
    start_read_snapshot(session)
    with session.no_autoflush:
        facts = load_run_facts(session, run_id)
        if facts is None:
            raise ReplayRejected("RUN_NOT_FOUND", "Pipeline run not found.", 404)
        incident = next((row for row in facts.incidents if row.id == incident_id), None)
        if incident is None:
            raise ReplayRejected("INCIDENT_NOT_IN_RUN", "Incident does not belong to the requested run.", 404)
        if facts.run.status != "completed":
            raise ReplayRejected("RUN_NOT_COMPLETED", "Only completed source runs can be replayed.")
        if len(facts.raw) > MAX_RECORDS or len(facts.run.source_csv.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ReplayRejected("REPLAY_SIZE_LIMIT", "Run exceeds the local replay size limit.")
        if facts.contract is None or facts.reporting_stage is None:
            raise ReplayRejected("MISSING_REPLAY_EVIDENCE", "A recorded schema contract and reporting stage are required.")
        try:
            contract = get_contract(facts.contract.schema_version)
        except ValueError:
            raise ReplayRejected("UNSUPPORTED_CONTRACT", "Recorded schema version is not supported.") from None
        if contract.definition() != facts.contract.expected_schema:
            raise ReplayRejected("CONTRACT_CHANGED", "Recorded expected schema differs from the supported version.")
        try:
            parsed = parse_orders_csv(facts.run.source_csv)
        except (ValueError, TypeError):
            raise ReplayRejected("INVALID_SOURCE_CSV", "Preserved source CSV cannot be parsed safely.") from None
        actual = list(facts.raw.values())
        if (parsed.header != facts.contract.actual_header or len(parsed.rows) != len(actual)
                or any(source.row_number != raw.row_number or source.original_record != raw.original_record
                       or source.data != raw.original_data for source, raw in zip(parsed.rows, actual))):
            raise ReplayRejected("SOURCE_INPUT_MISMATCH", "Original CSV and preserved raw records do not agree.")
        trace = run_lineage(session, run_id)
        if trace["summary"]["evidence_completeness"] != "complete" or trace["integrity_issues"]:
            raise ReplayRejected("INCOMPLETE_LINEAGE", "Complete, consistent source lineage is required.")
        rows = {PipelineRun: [facts.run], RawOrder: list(facts.raw.values()),
                PipelineContract: [facts.contract], RecordArrival: list(facts.arrivals.values()),
                CleanOrder: list(facts.clean.values()), RejectedOrder: list(facts.rejected.values()),
                ReportingStage: [facts.reporting_stage], ReportingExpectedOrder: list(facts.expected.values()),
                ReportedOrder: list(facts.reported.values()), Incident: facts.incidents,
                DatasetImport: list(session.scalars(select(DatasetImport).where(DatasetImport.run_id == run_id))),
                DatasetSourceRecord: list(session.scalars(select(DatasetSourceRecord).where(DatasetSourceRecord.run_id == run_id)))}
        tables = {}
        for model in MODELS:
            values = [deepcopy({column.name: getattr(row, column.name) for column in model.__table__.columns})
                      for row in rows[model]]
            tables[model.__tablename__] = sorted(values, key=canonical_json)
        return ReplayInput(run_id, incident_id, tables, deepcopy(trace["summary"]), fingerprint(tables))
