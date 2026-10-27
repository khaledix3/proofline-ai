"""Deterministic faults on private SQLite copies; durable results only on the source DB."""
import csv
import io
import json
import random
from copy import deepcopy
from datetime import datetime, timezone
from time import perf_counter_ns
from types import SimpleNamespace
from uuid import uuid5,NAMESPACE_URL
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.models import (PipelineRun,RawOrder,CleanOrder,PipelineContract,ReportedOrder,Incident,LabEvaluation,DatasetImport,DatasetSourceRecord,utc_now)
from app.lineage.service import start_read_snapshot,incident_lineage
from app.lineage.evidence import incident_references
from app.pipeline.contracts import validate_contract_order
from app.pipeline.validation import COLUMNS
from app.pipeline.service import persist_raw_orders,persist_order_outcomes,reconcile_counts
from app.pipeline.reporting import publish_reporting
from app.incidents.detection import detect_incidents
from app.replay.inputs import fingerprint,canonical_json
from app.replay.sandbox import isolated_database
from app.replay.service import replay_run
from app.replay.audit import audit_projection
from app.replay.errors import ReplayRejected
from app.datasets.service import import_lock,source_summary
from app.analytics.health import AnalyticsError
from app.lab.metrics import metrics

VERSION="reliability-lab.v1"
SCENARIOS=("normal","missing_records","duplicate_outputs","schema_drift","late_arrival")
KINDS={"missing_records":"MISSING_RECORDS","duplicate_outputs":"DUPLICATE_RECORDS","schema_drift":"SCHEMA_DRIFT","late_arrival":"LATE_ARRIVING_DATA"}

def capture_source(session,run_id,limit,seed):
    start_read_snapshot(session)
    run=session.get(PipelineRun,run_id)
    if run is None: raise AnalyticsError("RUN_NOT_FOUND",404)
    if run.status!="completed": raise AnalyticsError("LAB_SOURCE_NOT_COMPLETED")
    if session.scalar(select(func.count()).select_from(RawOrder).where(RawOrder.run_id==run_id))>10000:
        raise AnalyticsError("LAB_SOURCE_TOO_LARGE")
    contract=session.get(PipelineContract,run_id)
    if contract is None: raise AnalyticsError("LAB_SOURCE_CONTRACT_MISSING")
    dataset=session.get(DatasetImport,run_id)
    originals={r.raw_order_id:r for r in session.scalars(select(DatasetSourceRecord).where(DatasetSourceRecord.run_id==run_id))}
    candidates=[]
    for raw,clean in session.execute(select(RawOrder,CleanOrder).join(CleanOrder,CleanOrder.raw_order_id==RawOrder.id).where(RawOrder.run_id==run_id).order_by(RawOrder.row_number)):
        order,reason=validate_contract_order(raw.original_data,contract.schema_version,contract.column_policy)
        if reason or order is None or any(getattr(order,k)!=getattr(clean,k) for k in COLUMNS):
            raise AnalyticsError("LAB_SOURCE_VALIDATION_MISMATCH")
        candidates.append({"raw_order_id":raw.id,"source_identity":originals[raw.id].source_identity if raw.id in originals else f"{run_id}:{raw.id}",
            "values":{k:raw.original_data[k] for k in COLUMNS}})
    if len(candidates)<2: raise AnalyticsError("LAB_REQUIRES_TWO_VALID_SOURCE_RECORDS")
    selected=sorted(random.Random(seed).sample(candidates,min(limit,len(candidates))),key=lambda r:r['raw_order_id'])
    return {"run_id":run_id,"records":selected,"source_fingerprint":fingerprint({"run_id":run_id,"contract":contract.expected_schema,"records":selected}),
        "eligible_record_count":len(candidates),"source_context":source_summary(dataset)}

def labels(rows):
    return [{"incident_type":kind,"source_identity":identity} for kind,identity in sorted(rows)]

def run_case(source,scenario):
    selected=deepcopy(source['records'])
    columns=[*COLUMNS,"event_timestamp"]
    values=[{**r['values'],"event_timestamp":"2026-01-01T10:00:00Z"} for r in selected]
    if scenario=="schema_drift":
        columns.remove("customer_id");columns.append("lab_extra")
        for row in values: row.pop("customer_id");row['lab_extra']='controlled'
        values[0]['quantity']='two'
    if scenario=="late_arrival": values[0]['event_timestamp']='2026-01-01T09:59:00Z'
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader();writer.writerows(values)
    expected=set()
    def truth(kind,indices):
        expected.update((kind,selected[i]['source_identity']) for i in indices)
    if scenario=='missing_records': truth('MISSING_RECORDS',[1])
    if scenario=='duplicate_outputs': truth('DUPLICATE_RECORDS',[0,1]);truth('MISSING_RECORDS',[1])
    if scenario=='schema_drift': truth('SCHEMA_DRIFT',range(len(selected)))
    if scenario=='late_arrival': truth('LATE_ARRIVING_DATA',[0])
    with isolated_database(SimpleNamespace(tables={})) as sandbox:
        run_id=persist_raw_orders(sandbox,stream.getvalue(),'isolated-reliability-lab',schema_version='orders.v2',
            ingested_at=datetime(2026,1,1,10,5,tzinfo=timezone.utc),lateness_seconds=300)
        with sandbox.begin():
            persist_order_outcomes(sandbox,run_id)
            run=sandbox.get(PipelineRun,run_id)
            for key,value in reconcile_counts(sandbox,run_id).items(): setattr(run,key,value)
            run.status='completed';run.finished_at=utc_now();publish_reporting(sandbox,run_id)
            raw=sandbox.scalars(select(RawOrder).where(RawOrder.run_id==run_id).order_by(RawOrder.row_number)).all()
            refs={r.id:selected[i]['source_identity'] for i,r in enumerate(raw)}
            if scenario=='missing_records': sandbox.delete(sandbox.get(ReportedOrder,(run_id,raw[1].id)))
            if scenario=='duplicate_outputs': sandbox.get(ReportedOrder,(run_id,raw[1].id)).order_id=raw[0].original_data['order_id']
            sandbox.flush()
            begin=perf_counter_ns();detect_incidents(sandbox,run_id);duration=(perf_counter_ns()-begin)/1_000_000
        incidents=sandbox.scalars(select(Incident).where(Incident.run_id==run_id).order_by(Incident.incident_type)).all()
        predicted=set();results=[]
        for incident in incidents:
            references=incident_references(incident,set(refs))
            if references.issues: raise AnalyticsError("LAB_UNRESOLVED_PREDICTION_REFERENCES")
            predicted.update((incident.incident_type,refs[r]) for r in references.records)
            with Session(sandbox.get_bind()) as reader:
                trace=incident_lineage(reader,incident.id)
            replay=None
            operation={'MISSING_RECORDS':'recover_missing_reporting','DUPLICATE_RECORDS':'deduplicate_processing'}.get(incident.incident_type)
            replay_rejection=None
            if operation:
                try:
                    with Session(sandbox.get_bind()) as reader:
                        replay=audit_projection(replay_run(reader,run_id,incident.id,operation))
                except ReplayRejected as exc:
                    replay_rejection=exc.code
            results.append({'id':incident.id,'incident_type':incident.incident_type,'affected_count':incident.affected_count,
                'evidence':incident.evidence,'lineage':trace,'replay':replay,'replay_rejection':replay_rejection,'replay_availability':'rejected_unsafe_state' if replay_rejection else 'supported' if operation else 'unsupported_incident_type'})
        return json.loads(canonical_json({'scenario':scenario,'status':'completed','isolated_run_id':run_id,
            'ground_truth':labels(expected),'predictions':labels(predicted),'metrics':metrics(expected,predicted),
            'detector_execution_ms':duration,'records':[{"isolated_raw_order_id":r.id,"source_identity":refs[r.id],"source_raw_order_id":selected[i]['raw_order_id']} for i,r in enumerate(raw)],
            'injection':{'kind':scenario,'clock_semantics':'simulated UTC; arrival 10:05; normal event 10:00; threshold 300 seconds; equality is on time',
                'details':{'normal':'No injected fault; clean baseline at lateness boundary.','missing_records':'Remove the second isolated reporting observation.',
                'duplicate_outputs':'Replace second isolated output identity with the first; expect both duplicate and missing-identity evidence.',
                'schema_drift':'Remove customer_id, add lab_extra and replace first quantity with two.',
                'late_arrival':'First event moved to 09:59, arriving 60 seconds after deadline.'}[scenario]},'incidents':results}))

def checked(row):
    if fingerprint(row.report)!=row.report_sha256 or row.report.get('id')!=row.id or row.report.get('source_run_id')!=row.source_run_id or row.report.get('source_fingerprint')!=row.source_fingerprint or row.report.get('config')!=row.config or row.report.get('status')!=row.status or row.report.get('algorithm_version')!=row.algorithm_version:
        raise AnalyticsError('LAB_AUDIT_INTEGRITY_FAILURE',409)
    return deepcopy(row.report)

def evaluate(engine,run_id,scenario='all',record_limit=25,seed=42):
    if scenario not in (*SCENARIOS,'all') or type(record_limit) is not int or not 2<=record_limit<=50 or type(seed) is not int or not 0<=seed<=2147483647:
        raise AnalyticsError('UNSUPPORTED_LAB_CONFIGURATION')
    with Session(engine) as reader: source=capture_source(reader,run_id,record_limit,seed)
    config={'scenario':scenario,'record_limit':record_limit,'seed':seed}
    key=fingerprint({'version':VERSION,'fingerprint':source['source_fingerprint'],'config':config})
    with import_lock(engine,key):
        with Session(engine) as reader:
            existing=reader.scalar(select(LabEvaluation).where(LabEvaluation.evaluation_key==key))
            if existing is not None: return {**checked(existing),'reused':True}
        started=utc_now();cases=[]
        chosen=SCENARIOS if scenario=='all' else ('normal',scenario) if scenario!='normal' else ('normal',)
        for name in chosen:
            try: cases.append(run_case(source,name))
            except Exception:
                # Never persist credentials, raw values, or exception text from drivers.
                cases.append({'scenario':name,'status':'failed','failure_code':'LAB_CASE_EXECUTION_FAILED'})
        expected=set();predicted=set()
        for index,case in enumerate(cases):
            if case['status']!='completed': continue
            expected.update((r['incident_type'],f"{index}:{r['source_identity']}") for r in case['ground_truth'])
            predicted.update((r['incident_type'],f"{index}:{r['source_identity']}") for r in case['predictions'])
        complete=all(c['status']=='completed' for c in cases)
        result=metrics(expected,predicted)
        if not complete:
            result.update(precision=None,recall=None,f1=None,undefined_reasons={k:'INCOMPLETE_EVALUATION' for k in ('precision','recall','f1')})
        identifier=str(uuid5(NAMESPACE_URL,key));finished=utc_now()
        report=json.loads(canonical_json({'id':identifier,'source_run_id':run_id,'source_fingerprint':source['source_fingerprint'],
            'algorithm_version':VERSION,'config':config,'started_at':started,'finished_at':finished,
            'status':'completed' if complete else 'failed','metrics':result,'cases':cases,
            'source_context':source['source_context'],'selected_record_count':len(source['records']),'eligible_record_count':source['eligible_record_count'],
            'source_applied':False,'scope':'isolated_controlled_experiment',
            'limitations':['Controlled injected faults are not production accuracy estimates.','Natural source problems are not ground-truth labeled.',
                'Execution time measures the detector call only; it is not historical detection latency.',
                'Schema labels apply to affected input identities, not number of schema differences.',
                'Replays and lineage reference temporary run IDs; source data is unchanged.']}))
        with Session(engine) as writer,writer.begin():
            writer.add(LabEvaluation(id=identifier,evaluation_key=key,source_run_id=run_id,source_fingerprint=source['source_fingerprint'],
                algorithm_version=VERSION,config=config,status=report['status'],started_at=started,finished_at=finished,report=report,report_sha256=fingerprint(report)))
        return {**report,'reused':False}

def retrieve(session,identifier):
    start_read_snapshot(session);row=session.get(LabEvaluation,identifier)
    if row is None: raise AnalyticsError('LAB_EVALUATION_NOT_FOUND',404)
    return checked(row)

def list_evaluations(session,run_id=None,limit=10,offset=0):
    start_read_snapshot(session)
    query=select(LabEvaluation)
    if run_id: query=query.where(LabEvaluation.source_run_id==run_id)
    total=session.scalar(select(func.count()).select_from(query.subquery()))
    rows=session.scalars(query.order_by(LabEvaluation.started_at.desc(),LabEvaluation.id).limit(limit).offset(offset)).all()
    return {'items':[{'id':r.id,'source_run_id':r.source_run_id,'status':r.status,'config':r.config,'started_at':r.started_at,'metrics':checked(r)['metrics']} for r in rows],
        'total':total,'limit':limit,'offset':offset}
