from copy import deepcopy
import pytest
from sqlalchemy import select,delete
from sqlalchemy.orm import Session
from app.analytics.health import data_health,compare_runs,AnalyticsError
from app.analytics.impact import business_impact
from app.datasets.service import import_subset
from app.models import Incident,ReportedOrder,LabEvaluation,ReportingStage,ReportingExpectedOrder,PipelineRun
from app.lab.service import evaluate,retrieve,run_case,capture_source
from app.lab.metrics import metrics
from app.pipeline.service import run_local_sample
from test_uci_dataset import subset
from test_lineage import snapshot


def test_health_counts_rates_and_unknown_monitoring(db_engine,subset):
    run=import_subset(db_engine,subset)['run_id'];before=snapshot(db_engine)
    with Session(db_engine) as s:
        result=data_health(s,'uci_real');row=result['items'][0]
        assert row['id']==run and row['counts']==dict(source_count=6,accepted_count=4,rejected_count=2,duplicate_count=0)
        assert row['processed_record_count']==6 and row['validation_failure_count']==2
        assert row['acceptance_percentage']==66.6667 and row['rejection_percentage']==33.3333
        assert row['missing_downstream_record_count']==0
        assert result['summary']['late_arriving_record_count'] is None
        assert result['history']['status']=='insufficient_data'
    assert snapshot(db_engine)==before


def test_health_comparison_and_pagination(db_engine,subset):
    a=import_subset(db_engine,subset)['run_id'];b=import_subset(db_engine,subset,'missing_reporting')['run_id']
    with Session(db_engine) as s:
        r=compare_runs(s,b,a)
        assert r['same_verified_source_subset'] and r['causal_claim'] is False
        assert r['delta_first_minus_second']['missing_downstream_record_count']==1
    with Session(db_engine) as s:
        r=data_health(s,limit=1,offset=1);assert len(r['items'])==1 and r['total']==2
    with Session(db_engine) as s:
        with pytest.raises(AnalyticsError): compare_runs(s,a,a)


def test_verified_value_not_revenue_loss(db_engine,subset):
    run=import_subset(db_engine,subset,'missing_reporting')['run_id'];before=snapshot(db_engine)
    with Session(db_engine) as s:
        id=s.scalar(select(Incident.id).where(Incident.run_id==run,Incident.incident_type=='MISSING_RECORDS'))
    with Session(db_engine) as s:
        r=business_impact(s,id)
        assert r['monetary']['associated_absent_value']=='2.50' and r['monetary']['currency']=='GBP'
        assert r['financial_loss']['amount'] is None and r['root_cause']['status']=='unknown'
        assert r['records'][0]['physical_reporting_row_absent']
        assert r['records'][0]['last_confirmed_stage']=='clean'
        assert r['records'][0]['evidence_refs']
    assert snapshot(db_engine)==before


def test_wrong_identity_does_not_claim_absent_value(db_engine,subset):
    run=import_subset(db_engine,subset,'duplicate_processing')['run_id']
    with Session(db_engine) as s: id=s.scalar(select(Incident.id).where(Incident.run_id==run,Incident.incident_type=='MISSING_RECORDS'))
    with Session(db_engine) as s:
        r=business_impact(s,id)
        assert r['monetary']['associated_absent_value'] is None
        assert r['records'][0]['physical_reporting_row_absent'] is False


def test_lab_all_scenarios_preserve_source_and_durable_idempotence(db_engine,subset):
    run=import_subset(db_engine,subset)['run_id'];before=snapshot(db_engine)
    r=evaluate(db_engine,run,'all',3,42)
    assert r['status']=='completed',[(c['scenario'],c.get('failure_code')) for c in r['cases']]
    assert r['metrics']['false_positives']==r['metrics']['false_negatives']==0
    assert r['metrics']['true_positives']==8 and r['metrics']['f1']==1
    normal=r['cases'][0];assert normal['predictions']==[] and normal['metrics']['precision'] is None
    missing=r['cases'][1];assert missing['incidents'][0]['replay']['outcome']=='verified'
    assert all(c['detector_execution_ms']>=0 for c in r['cases'])
    after=snapshot(db_engine)
    assert {k:v for k,v in before.items() if k!='lab_evaluations'}=={k:v for k,v in after.items() if k!='lab_evaluations'}
    second=evaluate(db_engine,run,'all',3,42)
    assert second['id']==r['id'] and second['reused'] and snapshot(db_engine)==after
    with Session(db_engine) as s: assert retrieve(s,r['id'])['cases']==r['cases']
    with Session(db_engine) as s,s.begin(): s.get(LabEvaluation,r['id']).report={'id':r['id']}
    with Session(db_engine) as s:
        with pytest.raises(AnalyticsError,match='INTEGRITY'): retrieve(s,r['id'])


@pytest.mark.parametrize('scenario',['normal','missing_records','duplicate_outputs','schema_drift','late_arrival'])
def test_individual_scenarios(db_engine,subset,scenario):
    run=import_subset(db_engine,subset)['run_id']
    r=evaluate(db_engine,run,scenario,2,7)
    assert r['status']=='completed',r
    assert r['metrics']['false_positives']==r['metrics']['false_negatives']==0
    assert len(r['cases'])==(1 if scenario=='normal' else 2)


def test_undefined_metrics_are_not_perfect_accuracy():
    r=metrics(set(),set());assert r['precision'] is r['recall'] is r['f1'] is None
    r=metrics({('MISSING_RECORDS','a')},{('MISSING_RECORDS','b')})
    assert r['false_positives']==r['false_negatives']==1 and r['f1']==0


@pytest.mark.parametrize('config',[('arbitrary_sql',2,1),('all',1,1),('all',51,1),('all',2,-1),('all',True,1)])
def test_reject_unsafe_configuration(db_engine,config):
    with pytest.raises(AnalyticsError): evaluate(db_engine,'missing',*config)


def test_empty_health_has_no_fabricated_rates(db_engine):
    with Session(db_engine) as s:
        r=data_health(s)
        assert r['total']==0 and r['history']['status']=='insufficient_data'
        assert r['summary']['acceptance_percentage'] is None
        assert r['summary']['missing_downstream_record_count'] is None
        assert r['summary']['late_arriving_record_count'] is None


def test_unpublished_stage_is_unknown_not_zero(db_engine,subset):
    run=import_subset(db_engine,subset)['run_id']
    with Session(db_engine) as s,s.begin():
        s.execute(delete(ReportedOrder).where(ReportedOrder.run_id==run))
        s.execute(delete(ReportingExpectedOrder).where(ReportingExpectedOrder.run_id==run))
        s.delete(s.get(ReportingStage,run))
    with Session(db_engine) as s:
        r=data_health(s)
        assert r['items'][0]['missing_downstream_record_count'] is None
        assert r['summary']['unknown_reporting_run_count']==1


def test_provenance_tamper_prevents_value_claim(db_engine,subset):
    from app.models import DatasetSourceRecord
    run=import_subset(db_engine,subset,'missing_reporting')['run_id']
    with Session(db_engine) as s,s.begin():
        original=s.scalar(select(DatasetSourceRecord).where(DatasetSourceRecord.run_id==run))
        original.original_values={**original.original_values,'UnitPrice':9999}
        id=s.scalar(select(Incident.id).where(Incident.run_id==run,Incident.incident_type=='MISSING_RECORDS'))
    with Session(db_engine) as s:
        r=business_impact(s,id)
        assert r['monetary']['associated_absent_value'] is None
        assert 'SOURCE_PROVENANCE_FINGERPRINT_MISMATCH' in r['unknowns']


def test_unknown_currency_is_not_invented(db_engine):
    from app.pipeline.scenarios import run_fault_scenario
    with Session(db_engine) as s: run=run_fault_scenario(s,'missing_records')
    with Session(db_engine) as s: id=s.scalar(select(Incident.id).where(Incident.run_id==run,Incident.incident_type=='MISSING_RECORDS'))
    with Session(db_engine) as s:
        r=business_impact(s,id)
        assert r['monetary']['currency'] is None and r['monetary']['associated_absent_value'] is None


def test_zero_value_is_known_zero_not_missing(db_engine,subset):
    from dataclasses import replace
    rows=deepcopy(subset.rows);rows[0]['original']['UnitPrice']=0
    run=import_subset(db_engine,replace(subset,rows=rows),'missing_reporting')['run_id']
    with Session(db_engine) as s: id=s.scalar(select(Incident.id).where(Incident.run_id==run,Incident.incident_type=='MISSING_RECORDS'))
    with Session(db_engine) as s:
        r=business_impact(s,id)
        assert r['monetary']['associated_absent_value']=='0.00'
        assert r['monetary']['status']=='verified_associated_value'


def test_lab_failure_saved_without_fabricated_metrics_or_exception_text(db_engine,subset,monkeypatch):
    import app.lab.service as lab
    run=import_subset(db_engine,subset)['run_id'];before=snapshot(db_engine)
    def fail(*args): raise RuntimeError('secret-driver-password-sentinel')
    monkeypatch.setattr(lab,'run_case',fail)
    r=evaluate(db_engine,run,'normal',2,123)
    assert r['status']=='failed' and r['metrics']['f1'] is None
    assert 'secret-driver-password-sentinel' not in str(r)
    assert r['cases'][0]['failure_code']=='LAB_CASE_EXECUTION_FAILED'
    after=snapshot(db_engine)
    assert {k:v for k,v in before.items() if k!='lab_evaluations'}=={k:v for k,v in after.items() if k!='lab_evaluations'}


def test_lab_downgrade_refuses_audit_loss(db_engine,subset):
    from alembic import command
    from alembic.migration import MigrationContext
    from conftest import migration_config
    run=import_subset(db_engine,subset)['run_id'];evaluate(db_engine,run,'normal',2,17);before=snapshot(db_engine)
    with db_engine.connect() as c:
        with pytest.raises(RuntimeError,match='lab audit'): command.downgrade(migration_config(c),'0006_reporting_collision_stage')
    assert snapshot(db_engine)==before
    with db_engine.connect() as c: assert MigrationContext.configure(c).get_current_revision()=='0007_reliability_lab'


def test_api_scope_validation_and_audit_retrieval(db_engine,subset):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_session
    run=import_subset(db_engine,subset)['run_id']
    def sessions():
        with Session(db_engine) as s: yield s
    app.dependency_overrides[get_session]=sessions
    try:
        with TestClient(app) as client:
            assert client.get('/data-health?source_kind=bad').status_code==422
            assert client.get('/data-health?limit=100000').status_code==422
            for body in [{'source_run_id':run,'seed':True},{'source_run_id':run,'sql':'DELETE ALL'},{'source_run_id':run,'scenario':'execute_code'}]:
                assert client.post('/reliability-lab/evaluations',json=body).status_code==422
            result=client.post('/reliability-lab/evaluations',json={'source_run_id':run,'scenario':'normal','record_limit':2})
            assert result.status_code==200 and result.json()['source_applied'] is False
            id=result.json()['id']
            assert client.get('/reliability-lab/evaluations/'+id).json()['id']==id
            assert client.get('/reliability-lab/evaluations?source_run_id='+run).json()['total']==1
            assert client.get('/reliability-lab/evaluations/00000000-0000-0000-0000-000000000000').status_code==404
    finally: app.dependency_overrides.clear()


def test_incomplete_expected_manifest_is_unknown_not_healthy(db_engine,subset):
    run=import_subset(db_engine,subset)['run_id']
    with Session(db_engine) as s,s.begin():
        row=s.scalar(select(ReportingExpectedOrder).where(ReportingExpectedOrder.run_id==run))
        s.delete(s.get(ReportedOrder,(run,row.raw_order_id)));s.flush();s.delete(row)
        s.get(ReportingStage,run).expected_count-=1
    with Session(db_engine) as s:
        r=data_health(s)
        assert r['items'][0]['missing_downstream_record_count'] is None
        assert not r['items'][0]['reporting_comparison_known']
