from typing import Literal
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.pipeline.service import execute_pipeline

Scenario = Literal["duplicate_records", "missing_records", "both", "schema_drift", "late_arrival", "normal_v2"]
HEADER = "order_id,customer_id,quantity,unit_price,order_date\n"
VALID_ROWS = [
    "DEMO-001,CUST-01,1,10.00,2026-01-01\n",
    "DEMO-002,CUST-02,2,20.00,2026-01-02\n",
    "DEMO-003,CUST-03,3,30.00,2026-01-03\n",
]
INVALID_ROW = "DEMO-BAD,CUST-04,0,5.00,2026-01-04\n"


def run_fault_scenario(session: Session, scenario: Scenario) -> str:
    """Create a fresh synthetic run; never mutate an earlier run or source file."""
    if scenario in ("schema_drift", "late_arrival", "normal_v2"):
        return run_v2_scenario(session, scenario)
    if scenario not in ("duplicate_records", "missing_records", "both"):
        raise ValueError("Unknown synthetic scenario")
    source = HEADER + "".join(VALID_ROWS) + INVALID_ROW
    if scenario in ("duplicate_records", "both"):
        source += VALID_ROWS[0]  # Intentionally append a duplicate source record.
    return execute_pipeline(session, source, f"synthetic_scenario_{scenario}.csv",
                            omit_demo_order=scenario in ("missing_records", "both"))


V2_HEADER = HEADER.rstrip("\n") + ",event_timestamp\n"
V2_ROWS = [
    "TIME-001,CUST-01,1,10.00,2026-01-01,2026-01-01T10:00:00Z\n",
    "TIME-002,CUST-02,2,20.00,2026-01-01,2026-01-01T10:06:00Z\n",
    "TIME-003,CUST-03,3,30.00,2026-01-01,2026-01-01T10:10:00Z\n",
]


def run_v2_scenario(session: Session, scenario: str) -> str:
    if scenario == "schema_drift":
        # Missing customer_id, forbidden extra field, and incompatible quantity.
        source = ("order_id,quantity,unit_price,order_date,event_timestamp,unexpected_note\n"
                  "DRIFT-001,two,10.00,2026-01-01,2026-01-01T10:06:00Z,synthetic\n")
    elif scenario in ("late_arrival", "normal_v2"):
        source = V2_HEADER + "".join(V2_ROWS if scenario == "late_arrival" else V2_ROWS[1:])
    else:
        raise ValueError("Unknown v2 scenario")
    return execute_pipeline(session, source, f"synthetic_scenario_{scenario}.csv", schema_version="orders.v2",
                            ingested_at=datetime(2026, 1, 1, 10, 10, tzinfo=timezone.utc), lateness_seconds=300)
