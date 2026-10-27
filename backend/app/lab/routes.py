from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from sqlalchemy.orm import Session
from app.database import get_session
from app.analytics.routes import invoke
from app.lab.service import evaluate,retrieve,list_evaluations
router=APIRouter(prefix="/reliability-lab/evaluations",tags=["reliability-lab"])
class EvaluationRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    source_run_id:UUID
    scenario:Literal["all","normal","missing_records","duplicate_outputs","schema_drift","late_arrival"]="all"
    record_limit:StrictInt=Field(default=25,ge=2,le=50)
    seed:StrictInt=Field(default=42,ge=0,le=2147483647)
@router.post("")
def launch(body:EvaluationRequest,session:Session=Depends(get_session)):
    return invoke(evaluate,session.get_bind(),str(body.source_run_id),body.scenario,body.record_limit,body.seed)
@router.get("")
def listing(source_run_id:UUID|None=None,limit:int=Query(10,ge=1,le=50),offset:int=Query(0,ge=0),session:Session=Depends(get_session)):
    return invoke(list_evaluations,session,str(source_run_id) if source_run_id else None,limit,offset)
@router.get("/{evaluation_id}")
def detail(evaluation_id:UUID,session:Session=Depends(get_session)):
    return invoke(retrieve,session,str(evaluation_id))
