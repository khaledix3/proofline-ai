from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.database import get_session
from app.analytics.health import data_health,compare_runs,AnalyticsError
from app.analytics.impact import business_impact
router=APIRouter(tags=["analytics"])

def invoke(function,*args,**kwargs):
    try: return function(*args,**kwargs)
    except AnalyticsError as exc: raise HTTPException(exc.status_code,detail={"code":exc.code}) from None
    except SQLAlchemyError: raise HTTPException(503,detail={"code":"ANALYTICS_UNAVAILABLE"}) from None

@router.get("/data-health")
def health(source_kind:Literal["local_demo","uci_real","uci_controlled"]|None=None,
           limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0),session:Session=Depends(get_session)):
    return invoke(data_health,session,source_kind,limit,offset)

@router.get("/data-health/compare")
def compare(run_id:UUID,against_run_id:UUID,session:Session=Depends(get_session)):
    return invoke(compare_runs,session,str(run_id),str(against_run_id))

@router.get("/incidents/{incident_id}/impact")
def impact(incident_id:UUID,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),session:Session=Depends(get_session)):
    return invoke(business_impact,session,str(incident_id),limit,offset)
