from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.lineage.service import run_lineage
from app.pipeline.scenarios import Scenario, run_fault_scenario
from app.pipeline.service import PipelineProcessingError, get_run_result, run_local_sample

router = APIRouter(prefix="/pipeline-runs", tags=["local pipeline"])


@router.post("/test", status_code=201)
def launch_test_run(session: Session = Depends(get_session)):
    try:
        run_id = run_local_sample(session)
        return get_run_result(session, run_id)
    except PipelineProcessingError as exc:
        raise HTTPException(500, detail={"error": "PROCESSING_FAILED", "run_id": exc.run_id}) from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None


@router.get("/{run_id}")
def inspect_run(run_id: UUID, session: Session = Depends(get_session)):
    try:
        result = get_run_result(session, str(run_id))
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None
    if result is None:
        raise HTTPException(404, detail="Pipeline run not found")
    return result


@router.post("/scenarios/{scenario}", status_code=201)
def launch_scenario(scenario: Scenario, session: Session = Depends(get_session)):
    try:
        run_id = run_fault_scenario(session, scenario)
        return get_run_result(session, run_id)
    except PipelineProcessingError as exc:
        raise HTTPException(500, detail={"error": "PROCESSING_FAILED", "run_id": exc.run_id}) from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None


@router.get("/{run_id}/lineage")
def inspect_run_lineage(run_id: UUID, session: Session = Depends(get_session)):
    try:
        result = run_lineage(session, str(run_id))
    except SQLAlchemyError:
        raise HTTPException(503, detail="Database unavailable or migrations missing; see README.") from None
    if result is None:
        raise HTTPException(404, detail="Pipeline run not found")
    return result
