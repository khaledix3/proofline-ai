from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CleanOrder, ReportedOrder, ReportingExpectedOrder, ReportingStage


def publish_reporting(session: Session, run_id: str, *, omit_demo_order: bool = False) -> None:
    """Publish once in the caller's transaction. Never update an existing stage."""
    if session.get(ReportingStage, run_id) is not None:
        raise ValueError("Reporting stage is already published")
    clean = session.scalars(select(CleanOrder).where(CleanOrder.run_id == run_id).order_by(CleanOrder.raw_order_id)).all()
    if omit_demo_order and not any(row.order_id == "DEMO-002" for row in clean):
        raise ValueError("Fault injection requires the dedicated synthetic scenario")
    session.add(ReportingStage(run_id=run_id, expected_count=len(clean),
                               fault="omit_demo_order" if omit_demo_order else "none"))
    session.flush()
    session.add_all([ReportingExpectedOrder(run_id=run_id, raw_order_id=row.raw_order_id,
                                            order_id=row.order_id) for row in clean])
    session.flush()
    session.add_all([ReportedOrder(run_id=run_id, raw_order_id=row.raw_order_id, order_id=row.order_id)
                     for row in clean if not (omit_demo_order and row.order_id == "DEMO-002")])
    session.flush()
