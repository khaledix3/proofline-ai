from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select, func
from app.models import ReplayAudit
from app.replay.audit import _checked
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.replay.errors import ReplayRejected
from app.replay.operations import Operation
from app.replay.service import replay_run
from app.replay.audit import persist_audit, retrieve_audit

router = APIRouter(prefix="/pipeline-runs", tags=["safe replay simulation"])


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation
    incident_id: UUID


@router.post("/{run_id}/replays")
def simulate_replay(run_id: UUID, request: ReplayRequest, session: Session = Depends(get_session)):
    try:
        report = replay_run(session, str(run_id), str(request.incident_id), request.operation)
        audit = persist_audit(session.get_bind(), report)
        return {**report, "audit": {"id": audit["id"], "created_at": audit["created_at"], "audit_sha256": audit["audit_sha256"]}}
    except ReplayRejected as error:
        raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None


@router.get("/{run_id}/replays/{audit_id}")
def inspect_replay_audit(run_id: UUID, audit_id: str = Path(pattern=r"^[0-9a-f]{64}$"), session: Session = Depends(get_session)):
    try:
        result = retrieve_audit(session, str(run_id), audit_id)
    except ReplayRejected as error:
        raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None
    if result is None:
        raise HTTPException(404, detail="Replay audit not found")
    return result


@router.get("/{run_id}/replays")
def list_replay_audits(run_id: UUID, incident_id: UUID | None = None,
                      limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                      session: Session = Depends(get_session)):
    query = select(ReplayAudit).where(ReplayAudit.run_id == str(run_id))
    count = select(func.count()).select_from(ReplayAudit).where(ReplayAudit.run_id == str(run_id))
    if incident_id is not None:
        query = query.where(ReplayAudit.incident_id == str(incident_id))
        count = count.where(ReplayAudit.incident_id == str(incident_id))
    try:
        rows = session.scalars(query.order_by(ReplayAudit.created_at.desc(), ReplayAudit.id).limit(limit).offset(offset)).all()
        checked = [_checked(row) for row in rows]
        return {"items": [{"id": item["id"], "replay_id": item["replay_id"], "run_id": item["run_id"],
                           "incident_id": item["incident_id"], "created_at": item["created_at"],
                           "operation": item["report"]["operation"], "outcome": item["report"]["outcome"],
                           "verification_passed": item["report"]["verification"]["passed"]} for item in checked],
                "total": session.scalar(count), "limit": limit, "offset": offset}
    except ReplayRejected as error:
        raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None
