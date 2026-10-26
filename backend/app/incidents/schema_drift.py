from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PipelineContract, RawOrder
from app.pipeline.timestamps import parse_event_timestamp


def compatible_type(value: str, expected: str) -> bool:
    text = value.strip()
    if expected == "string":
        return True  # IDs have separate business rules, not inferred numeric types.
    if expected == "integer":
        return re.fullmatch(r"[+-]?[0-9]+", text) is not None
    if expected == "decimal":
        try:
            return bool(text) and Decimal(text).is_finite()
        except InvalidOperation:
            return False
    if expected == "date":
        try:
            return re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text) is not None and bool(date.fromisoformat(text))
        except ValueError:
            return False
    if expected == "timestamp_with_timezone":
        try:
            parse_event_timestamp(text)
            return True
        except ValueError:
            return False
    raise ValueError("Unsupported expected schema type")


def observed_type(value: str) -> str:
    if not value.strip():
        return "empty"
    for candidate in ("integer", "decimal", "date", "timestamp_with_timezone"):
        if compatible_type(value, candidate):
            return candidate
    try:
        timestamp = datetime.fromisoformat(value.strip())
        if timestamp.tzinfo is None:
            return "timestamp_without_timezone"
    except ValueError:
        pass
    return "string"


def compare_schema(expected: dict, header: list[str], rows, policy: str) -> dict:
    """Compare against a saved contract, never infer or revise the expectation."""
    fields = {column["name"]: column for column in expected["columns"]}
    missing = [name for name, column in fields.items() if column["required"] and name not in header]
    extras = sorted(set(header) - set(fields))
    duplicate_columns = sorted(name for name, count in Counter(header).items() if count > 1)
    type_changes = []
    uneven_rows = []
    observed = {name: Counter() for name in header}
    for row in rows:
        data = row.original_data
        if isinstance(data.get("_cells"), list):
            if len(data["_cells"]) != len(header):
                uneven_rows.append({"raw_order_id": row.id, "row_number": row.row_number,
                                    "expected_width": len(header), "actual_width": len(data["_cells"])})
            continue
        for name, value in data.items():
            field = fields.get(name)
            compatible = field is not None and compatible_type(value, field["type"])
            actual = field["type"] if compatible else observed_type(value)
            observed.setdefault(name, Counter())[actual] += 1
            if field is not None and not compatible:
                type_changes.append({"raw_order_id": row.id, "row_number": row.row_number,
                                     "column": name, "expected_type": field["type"],
                                     "actual_type": actual, "value": value})
    prohibited = extras if policy == "reject" else []
    differences = {"missing_required_columns": missing, "unexpected_columns": prohibited,
                   "duplicate_columns": duplicate_columns, "incompatible_types": type_changes,
                   "row_width_mismatches": uneven_rows}
    difference_count = sum(len(items) for items in differences.values())
    structural = bool(missing or prohibited or duplicate_columns)
    affected = len(rows) if structural else len({item["raw_order_id"] for item in type_changes + uneven_rows})
    return {"expected_schema": expected,
            "actual_schema": {"columns": header, "unexpected_columns_observed": extras,
                              "observed_logical_types": {name: dict(sorted(counts.items())) for name, counts in observed.items()}},
            "unexpected_columns_policy": policy, "differences": differences,
            "counts": {"difference_count": difference_count, "affected_record_count": affected,
                       "source_record_count": len(rows)}}


def schema_drift_evidence(session: Session, run_id: str) -> dict | None:
    contract = session.get(PipelineContract, run_id)
    if contract is None or not contract.monitor_schema:
        return None
    rows = session.scalars(select(RawOrder).where(RawOrder.run_id == run_id).order_by(RawOrder.row_number)).all()
    comparison = compare_schema(contract.expected_schema, contract.actual_header, rows, contract.column_policy)
    if comparison["counts"]["difference_count"] == 0:
        return None
    return {"rule_version": "1", "run_id": run_id, "stage": "source_to_raw",
            "schema_version": contract.schema_version, **comparison}
