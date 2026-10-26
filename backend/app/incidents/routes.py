from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Incident
from app.lineage.service import incident_lineage

router = APIRouter(prefix="/incidents", tags=["incidents"])


def incident_result(incident: Incident) -> dict:
    return {"id": incident.id, "run_id": incident.run_id, "incident_type": incident.incident_type,
            "stage": incident.stage, "affected_count": incident.affected_count,
            "detected_at": incident.detected_at, "evidence": incident.evidence}


@router.get("")
def list_incidents(run_id: UUID | None = None,
                   incident_type: Literal["DUPLICATE_RECORDS", "MISSING_RECORDS", "SCHEMA_DRIFT", "LATE_ARRIVING_DATA"] | None = None,
                   limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                   session: Session = Depends(get_session)):
    query = select(Incident)
    if run_id is not None:
        query = query.where(Incident.run_id == str(run_id))
    if incident_type is not None:
        query = query.where(Incident.incident_type == incident_type)
    try:
        rows = session.scalars(query.order_by(Incident.detected_at, Incident.id).limit(limit).offset(offset)).all()
        return {"items": [incident_result(row) for row in rows], "limit": limit, "offset": offset}
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None


@router.get("/{incident_id}")
def inspect_incident(incident_id: UUID, session: Session = Depends(get_session)):
    try:
        incident = session.get(Incident, str(incident_id))
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None
    if incident is None:
        raise HTTPException(404, detail="Incident not found")
    return incident_result(incident)


@router.get("/{incident_id}/lineage")
def inspect_incident_lineage(incident_id: UUID, session: Session = Depends(get_session)):
    try:
        result = incident_lineage(session, str(incident_id))
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None
    if result is None:
        raise HTTPException(404, detail="Incident not found")
    return result
