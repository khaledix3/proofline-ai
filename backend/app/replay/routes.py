from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.replay.errors import ReplayRejected
from app.replay.operations import Operation
from app.replay.service import replay_run

router = APIRouter(prefix="/pipeline-runs", tags=["safe replay simulation"])


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation
    incident_id: UUID


@router.post("/{run_id}/replays")
def simulate_replay(run_id: UUID, request: ReplayRequest, session: Session = Depends(get_session)):
    try:
        return replay_run(session, str(run_id), str(request.incident_id), request.operation)
    except ReplayRejected as error:
        raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None
