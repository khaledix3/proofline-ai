"""Small synthetic fixtures exercise the UCI adapter; never download live data in tests."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import pytest
from alembic import command
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.datasets.source import RetailSubset, map_line, classify_line, read_subset, OFFICIAL_WORKBOOK_SHA256
from app.datasets.service import import_subset
from app.models import DatasetImport, DatasetSourceRecord, Incident, RawOrder, CleanOrder, PipelineContract, RecordArrival, ReportedOrder
from app.lineage.service import incident_lineage
from app.investigation.service import investigate
from app.replay.service import replay_run
from app.replay.audit import persist_audit, retrieve_audit
from app.replay.errors import ReplayRejected
from conftest import migration_config
from test_lineage import snapshot
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_session

@pytest.fixture
def subset():
    rows=[]
    for index in range(6):
        row={"InvoiceNo": "536365", "StockCode": f"P{index}", "Description": "fixture only", "Quantity": 1,
             "InvoiceDate": "2010-12-01T08:26:00", "UnitPrice": 2.5, "CustomerID": 17850, "Country": "United Kingdom"}
        if index==3: row.update(InvoiceNo="C536365", Quantity=-1)
        if index==4: row["CustomerID"]=None
        if index==5: row["Description"]=None
        rows.append({"source_row":index+2,"original":row})
    return RetailSubset(OFFICIAL_WORKBOOK_SHA256,"Online Retail",tuple(rows),"synthetic fixture, not acquired source")


def test_invoice_lines_are_not_duplicate_orders_and_originals_preserved(db_engine, subset):
    result=import_subset(db_engine,subset)
    with Session(db_engine) as session:
        source=session.scalars(select(DatasetSourceRecord).where(DatasetSourceRecord.run_id==result["run_id"]).order_by(DatasetSourceRecord.source_row)).all()
        assert [r.original_values for r in source]==[r["original"] for r in subset.rows]
        assert len({r.source_identity for r in source})==6
        clean=session.scalars(select(CleanOrder).where(CleanOrder.run_id==result["run_id"])).all()
        assert len(clean)==4 and len({r.order_id for r in clean})==4
        assert all(r.customer_id.startswith('UCI_C_') for r in clean)
        assert not session.scalars(select(Incident).where(Incident.run_id==result["run_id"])).all()
        assert session.get(PipelineContract,result['run_id']).schema_version=='orders.v1'
        assert all(r.event_timestamp is None for r in session.scalars(select(RecordArrival).where(RecordArrival.run_id==result['run_id'])))
    evaluation=result['source']['evaluation']
    assert evaluation['counts']==dict(source_count=6,accepted_count=4,rejected_count=2,duplicate_count=0)
    assert evaluation['categories']['cancellation']['rejected']==1
    assert evaluation['source_observations']['missing_customer_reference']==1
    assert evaluation['source_observations']['missing_description']==1


def test_repeated_import_is_idempotent_and_mapping_does_not_mutate(subset,db_engine):
    original=deepcopy(subset.rows)
    one=import_subset(db_engine,subset);before=snapshot(db_engine)
    two=import_subset(db_engine,subset)
    assert two['reused'] and two['run_id']==one['run_id']
    assert snapshot(db_engine)==before and subset.rows==original
    assert map_line(subset,subset.rows[0])['order_id']!=map_line(subset,subset.rows[1])['order_id']
    assert '17850' not in map_line(subset,subset.rows[0])['customer_id']


@pytest.mark.parametrize('experiment,expected',[('missing_reporting',{'MISSING_RECORDS':1}),('duplicate_processing',{'DUPLICATE_RECORDS':1,'MISSING_RECORDS':1}),('schema_drift',{'SCHEMA_DRIFT':3}),('late_arrival',{'LATE_ARRIVING_DATA':1})])
def test_controlled_ground_truth_and_existing_run_isolation(db_engine,subset,experiment,expected):
    original=import_subset(db_engine,subset)
    result=import_subset(db_engine,subset,experiment)
    assert result['run_id']!=original['run_id']
    evaluation=result['source']['evaluation']
    assert evaluation['incident_counts']==expected
    assert evaluation['controlled_ground_truth']['detector_matches_expected']
    assert result['source']['controlled'] and result['source']['provenance']['injected']
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(ReportedOrder).where(ReportedOrder.run_id==original['run_id']))==4
        assert session.get(DatasetImport,original['run_id']).evaluation==original['source']['evaluation']
        cases=session.scalars(select(Incident).where(Incident.run_id==result['run_id'])).all()
        for case in cases:
            trace=incident_lineage(session,case.id)
            assert trace['scope']['run_id']==result['run_id'] and trace['run']['source']['controlled']
            assert all(row['source_reference']['source_row'] in {source['source_row'] for source in subset.rows} for row in trace['records'])
            assert all('original_values' not in row['source_reference'] for row in trace['records'])
            report=investigate(session,case.id)
            assert report['root_cause']['status']=='unknown' and not report['generation']['llm_generated']
            assert report['source_context']['controlled']
            assert any(row['id']=='CTX' for row in report['confirmed_observations'])
            if experiment in ('late_arrival','schema_drift') and trace['records']:
                assert report['source_context']['clock_semantics']=='simulated'
    if experiment=='late_arrival':
        with Session(db_engine) as session:
            evidence=session.scalar(select(Incident).where(Incident.run_id==result['run_id'])).evidence
            assert evidence['late_records'][0]['delay_seconds']==60
            assert 'synthetic' in result['source']['provenance']['arrival_semantics']


@pytest.mark.parametrize('operation,experiment,kind',[('recover_missing_reporting','missing_reporting','MISSING_RECORDS'),('deduplicate_processing','duplicate_processing','DUPLICATE_RECORDS')])
def test_replay_protects_original_source_provenance_and_corrects_only_copy(db_engine,subset,operation,experiment,kind):
    result=import_subset(db_engine,subset,experiment)
    with Session(db_engine) as session:
        incident=session.scalar(select(Incident.id).where(Incident.run_id==result['run_id'],Incident.incident_type==kind))
    before=snapshot(db_engine)
    with Session(db_engine) as session:
        replay=replay_run(session,result['run_id'],incident,operation)
    assert replay['verification']['passed'] and not replay['applied_to_source']
    assert snapshot(db_engine)==before
    if operation=='deduplicate_processing':
        assert replay['before']['counts']['duplicate_output_excess_count']==1
        assert replay['after']['counts']['duplicate_output_excess_count']==0
        assert not replay['after']['incident_status']['DUPLICATE_RECORDS']['detected_in_simulation']
    else:
        assert replay['before']['counts']['reported_count']==3 and replay['after']['counts']['reported_count']==4
    audit=persist_audit(db_engine,replay)
    with Session(db_engine) as session:
        assert retrieve_audit(session,result['run_id'],audit['id'])['report']['verification']['passed']
    after=snapshot(db_engine)
    assert {k:v for k,v in before.items() if k!='replay_audits'}=={k:v for k,v in after.items() if k!='replay_audits'}


def test_provenance_migration_downgrade_refuses_source_loss(db_engine,subset):
    import_subset(db_engine,subset)
    before=snapshot(db_engine)
    with db_engine.begin() as connection:
        with pytest.raises(RuntimeError,match='source provenance'):
            command.downgrade(migration_config(connection),'0004_replay_audit')
    assert snapshot(db_engine)==before


def test_filters_and_summary_do_not_expose_customer_values(db_engine,subset):
    real=import_subset(db_engine,subset);controlled=import_subset(db_engine,subset,'missing_reporting')
    def sessions():
        with Session(db_engine) as session: yield session
    app.dependency_overrides[get_session]=sessions
    try:
        with TestClient(app) as client:
            a=client.get('/pipeline-runs?source_kind=uci_real').json()
            assert [r['id'] for r in a['items']]==[real['run_id']]
            b=client.get('/incidents?source_kind=uci_controlled').json()
            assert {r['run_id'] for r in b['items']}=={controlled['run_id']}
            assert client.get('/incidents?source_kind=uci_real').json()['items']==[]
            assert '17850' not in str(a) and 'CustomerID": 17850' not in str(b)
            assert client.get('/pipeline-runs?source_kind=unknown').status_code==422
    finally: app.dependency_overrides.pop(get_session,None)


def test_reject_unsafe_sources_and_unknown_operations(tmp_path,db_engine,subset):
    p=tmp_path/'not-uci.xlsx';p.write_bytes(b'not a trusted workbook')
    with pytest.raises(ValueError,match='official UCI'): read_subset(p)
    with pytest.raises(ValueError,match='Unsupported'): import_subset(db_engine,subset,'arbitrary')
    with pytest.raises(ValueError,match='source identity'): import_subset(db_engine,replace(subset,rows=(subset.rows[0],subset.rows[0])))


def test_processing_failure_preserves_original_values_and_refuses_implicit_retry(db_engine,subset,monkeypatch):
    from app.datasets import service
    def fail(*args,**kwargs):
        raise RuntimeError("controlled failure")
    monkeypatch.setattr(service,"publish_reporting",fail)
    with pytest.raises(RuntimeError,match="controlled failure"):
        service.import_subset(db_engine,subset)
    with Session(db_engine) as session:
        dataset=session.scalar(select(DatasetImport))
        assert dataset.evaluation['status']=='failed'
        originals=session.scalars(select(DatasetSourceRecord).order_by(DatasetSourceRecord.source_row)).all()
        assert [row.original_values for row in originals]==[row['original'] for row in subset.rows]
        assert session.scalar(select(func.count()).select_from(CleanOrder))==0
    before=snapshot(db_engine)
    with pytest.raises(ValueError,match="not completed"):
        service.import_subset(db_engine,subset)
    assert snapshot(db_engine)==before
