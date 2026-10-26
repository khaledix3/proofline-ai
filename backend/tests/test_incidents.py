from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_session
from app.incidents.detection import detect_incidents, duplicate_evidence, missing_evidence
from app.main import app
from app.models import (CleanOrder, Incident, PipelineRun, RawOrder, RejectedOrder,
                        ReportedOrder, ReportingExpectedOrder, ReportingStage, utc_now)
from app.pipeline import service
from app.pipeline.reporting import publish_reporting
from app.pipeline.scenarios import HEADER, INVALID_ROW, VALID_ROWS, run_fault_scenario


def incident_map(session, run_id):
    return {row.incident_type: row for row in session.scalars(select(Incident).where(Incident.run_id == run_id))}


@pytest.mark.parametrize('scenario,types,raw_count,rejected_count,duplicate_count', [
    ('duplicate_records', {'DUPLICATE_RECORDS'}, 5, 2, 1),
    ('missing_records', {'MISSING_RECORDS'}, 4, 1, 0),
    ('both', {'DUPLICATE_RECORDS', 'MISSING_RECORDS'}, 5, 2, 1),
])
def test_scenarios_have_exact_evidence(db_engine, scenario, types, raw_count, rejected_count, duplicate_count):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, scenario)
    with Session(db_engine) as session:
        run = session.get(PipelineRun, run_id)
        incidents = incident_map(session, run_id)
        assert set(incidents) == types
        assert service.reconcile_counts(session, run_id) == {
            'source_count': raw_count, 'accepted_count': 3,
            'rejected_count': rejected_count, 'duplicate_count': duplicate_count}
        assert run.status == 'completed'
        raw = session.scalars(select(RawOrder).where(RawOrder.run_id == run_id).order_by(RawOrder.row_number)).all()
        assert len(raw) == raw_count
        assert raw[0].original_record == VALID_ROWS[0]
        assert raw[3].original_record == INVALID_ROW
        assert session.scalar(select(func.count()).select_from(CleanOrder).where(CleanOrder.run_id == run_id)) == 3
        expected_source = HEADER + ''.join(VALID_ROWS) + INVALID_ROW
        if duplicate_count:
            expected_source += VALID_ROWS[0]
            assert raw[0].original_data == raw[4].original_data
            assert raw[0].id != raw[4].id
            duplicate = incidents['DUPLICATE_RECORDS']
            assert duplicate.affected_count == 1
            assert duplicate.evidence['run_id'] == run_id
            assert duplicate.stage == 'raw_to_clean'
            assert duplicate.evidence['counts'] == {
                'raw_count': 5, 'valid_record_count': 4, 'invalid_record_count': 1,
                'clean_count': 3, 'duplicate_record_count': 1, 'affected_order_id_count': 1}
            assert duplicate.evidence['groups'] == [{
                'order_id': 'DEMO-001', 'first_raw_order_id': raw[0].id,
                'first_row_number': 1, 'occurrence_count': 2,
                'duplicates': [{'raw_order_id': raw[4].id, 'row_number': 5}]}]
        assert run.source_csv == expected_source
        if 'MISSING_RECORDS' in types:
            missing = incidents['MISSING_RECORDS']
            assert missing.affected_count == 1
            assert missing.stage == 'clean_to_reporting'
            assert missing.evidence['counts'] == {
                'expected_count': 3, 'actual_count': 2, 'matched_count': 2,
                'missing_count': 1, 'unexpected_count': 0}
            assert missing.evidence['missing_records'] == [{'raw_order_id': raw[1].id, 'order_id': 'DEMO-002'}]
            assert missing.evidence['affected_order_ids'] == ['DEMO-002']
            assert 'DEMO-BAD' not in [row['order_id'] for row in missing.evidence['expected_records']]
            assert session.get(ReportingStage, run_id).fault == 'omit_demo_order'
        else:
            assert missing_evidence(session, run_id) is None


def test_normal_sample_rejections_are_not_missing(db_engine):
    with Session(db_engine) as session:
        run_id = service.run_local_sample(session)
    with Session(db_engine) as session:
        incidents = incident_map(session, run_id)
        assert set(incidents) == {'DUPLICATE_RECORDS'}
        assert incidents['DUPLICATE_RECORDS'].affected_count == 2
        assert incidents['DUPLICATE_RECORDS'].evidence['affected_order_ids'] == ['ORD-001', 'ORD-002']
        assert session.get(ReportingStage, run_id).expected_count == 3
        assert session.scalar(select(func.count()).select_from(ReportedOrder).where(ReportedOrder.run_id == run_id)) == 3
        assert missing_evidence(session, run_id) is None


def test_healthy_and_invalid_only_runs_have_no_incidents(db_engine):
    with Session(db_engine) as session:
        healthy = service.execute_pipeline(session, HEADER + ''.join(VALID_ROWS), 'healthy.csv')
        invalid = service.execute_pipeline(session, HEADER + INVALID_ROW, 'invalid.csv')
    with Session(db_engine) as session:
        assert incident_map(session, healthy) == {}
        assert incident_map(session, invalid) == {}
        assert session.get(ReportingStage, invalid).expected_count == 0
        assert missing_evidence(session, invalid) is None


def test_duplicate_rules_are_case_sensitive_and_validate_first(db_engine):
    source = HEADER + (
        'ID-1,CUST-1,0,1.00,2026-01-01\n'
        ' ID-1 ,CUST-1,1,1.00,2026-01-01\n'
        'ID-1,CUST-1,2,2.00,2026-01-01\n'
        'id-1,CUST-1,1,1.00,2026-01-01\n')
    with Session(db_engine) as session:
        run_id = service.execute_pipeline(session, source, 'rule-test.csv')
    with Session(db_engine) as session:
        evidence = duplicate_evidence(session, run_id)
        assert evidence['counts']['duplicate_record_count'] == 1
        assert evidence['counts']['clean_count'] == 2
        assert evidence['groups'][0]['first_row_number'] == 2
        assert evidence['groups'][0]['duplicates'][0]['row_number'] == 3
        assert evidence['affected_order_ids'] == ['ID-1']


def test_same_counts_wrong_identity_is_missing(db_engine):
    with Session(db_engine) as session:
        run_id = service.execute_pipeline(session, HEADER + ''.join(VALID_ROWS), 'healthy.csv')
    with Session(db_engine) as session:
        row = session.scalar(select(ReportedOrder).where(ReportedOrder.run_id == run_id, ReportedOrder.order_id == 'DEMO-002'))
        raw_id = row.raw_order_id
        row.order_id = 'WRONG-ID'  # Fault limited to this fixture's synthetic run.
        session.commit()
        detect_incidents(session, run_id)
        session.commit()
    with Session(db_engine) as session:
        evidence = incident_map(session, run_id)['MISSING_RECORDS'].evidence
        assert evidence['counts'] == {'expected_count': 3, 'actual_count': 3, 'matched_count': 2,
                                       'missing_count': 1, 'unexpected_count': 1}
        assert evidence['missing_records'] == [{'raw_order_id': raw_id, 'order_id': 'DEMO-002'}]
        assert evidence['unexpected_records'] == [{'raw_order_id': raw_id, 'order_id': 'WRONG-ID'}]


def test_incident_snapshots_are_idempotent_and_reporting_cannot_be_republished(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'both')
    with Session(db_engine) as session:
        before = {kind: (row.id, deepcopy(row.evidence)) for kind, row in incident_map(session, run_id).items()}
        detect_incidents(session, run_id)
        detect_incidents(session, run_id)
        session.commit()
        after = {kind: (row.id, row.evidence) for kind, row in incident_map(session, run_id).items()}
        assert before == after
        with pytest.raises(ValueError, match='already published'):
            publish_reporting(session, run_id)
        assert session.scalar(select(func.count()).select_from(Incident).where(Incident.run_id == run_id)) == 2


def test_historical_unpublished_run_is_not_missing(db_engine):
    with Session(db_engine) as session:
        run_id = service.persist_raw_orders(session, HEADER + ''.join(VALID_ROWS), 'historical.csv')
        with session.begin():
            service.persist_order_outcomes(session, run_id)
            counts = service.reconcile_counts(session, run_id)
            run = session.get(PipelineRun, run_id)
            for key, value in counts.items():
                setattr(run, key, value)
            run.status = 'completed'
            run.finished_at = utc_now()
    with Session(db_engine) as session:
        assert session.get(ReportingStage, run_id) is None
        assert missing_evidence(session, run_id) is None
        assert detect_incidents(session, run_id) == []


def test_incomplete_runs_cannot_be_detected(db_engine):
    with Session(db_engine) as session:
        run_id = service.persist_raw_orders(session, HEADER, 'running.csv')
        with pytest.raises(ValueError, match='completed'):
            detect_incidents(session, run_id)
        with pytest.raises(ValueError, match='completed'):
            detect_incidents(session, str(uuid4()))


def test_fault_scenario_does_not_change_other_runs(db_engine):
    with Session(db_engine) as session:
        original = service.run_local_sample(session)
    with Session(db_engine) as session:
        before = deepcopy(service.get_run_result(session, original))
        before_evidence = {key: deepcopy(row.evidence) for key, row in incident_map(session, original).items()}
    with Session(db_engine) as session:
        new = run_fault_scenario(session, 'both')
        assert new != original
    with Session(db_engine) as session:
        assert service.get_run_result(session, original) == before
        assert {key: row.evidence for key, row in incident_map(session, original).items()} == before_evidence
        assert missing_evidence(session, original) is None


def test_reporting_failure_rolls_back_clean_reports_and_incidents(db_engine, monkeypatch):
    def fail_reporting(session, run_id, **kwargs):
        session.add(ReportingStage(run_id=run_id, expected_count=3))
        session.flush()
        raise RuntimeError('injected reporting failure')
    monkeypatch.setattr(service, 'publish_reporting', fail_reporting)
    with Session(db_engine) as session:
        with pytest.raises(service.PipelineProcessingError) as error:
            run_fault_scenario(session, 'both')
    with Session(db_engine) as session:
        run_id = error.value.run_id
        assert session.get(PipelineRun, run_id).status == 'failed'
        assert session.scalar(select(func.count()).select_from(RawOrder).where(RawOrder.run_id == run_id)) == 5
        for model in (CleanOrder, RejectedOrder, ReportingStage, ReportingExpectedOrder, ReportedOrder, Incident):
            assert session.scalar(select(func.count()).select_from(model).where(model.run_id == run_id)) == 0


def test_incident_and_reporting_constraints(db_engine):
    with Session(db_engine) as session:
        first = run_fault_scenario(session, 'both')
        second = service.run_local_sample(session)
    with Session(db_engine) as session:
        original = session.scalar(select(Incident).where(Incident.run_id == first))
        session.add(Incident(run_id=first, incident_type=original.incident_type, stage=original.stage,
                             affected_count=1, evidence={}))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    with Session(db_engine) as session:
        other_raw = session.scalar(select(CleanOrder.raw_order_id).where(CleanOrder.run_id == second))
        session.add(ReportedOrder(run_id=first, raw_order_id=other_raw, order_id='ORD-001'))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_incident_api_scenarios_filters_and_errors(db_engine):
    def test_session():
        with Session(db_engine) as session:
            yield session
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            response = client.post('/pipeline-runs/scenarios/both')
            assert response.status_code == 201
            run_id = response.json()['id']
            assert response.json()['counts']['source_count'] == 5
            listed = client.get('/incidents', params={'run_id': run_id})
            assert listed.status_code == 200
            items = listed.json()['items']
            assert len(items) == 2
            assert {row['incident_type'] for row in items} == {'DUPLICATE_RECORDS', 'MISSING_RECORDS'}
            for incident in items:
                inspected = client.get('/incidents/' + incident['id'])
                assert inspected.status_code == 200
                assert inspected.json() == incident
                assert incident['evidence']['run_id'] == run_id
            only_missing = client.get('/incidents', params={'run_id': run_id, 'incident_type': 'MISSING_RECORDS'}).json()
            assert len(only_missing['items']) == 1
            assert len(client.get('/incidents?limit=1').json()['items']) == 1
            assert client.get('/incidents', params={'run_id': str(uuid4())}).json()['items'] == []
            assert client.get('/incidents/' + str(uuid4())).status_code == 404
            assert client.get('/incidents/not-a-uuid').status_code == 422
            assert client.get('/incidents?limit=101').status_code == 422
            assert client.get('/incidents?incident_type=AI').status_code == 422
            assert client.post('/pipeline-runs/scenarios/unknown').status_code == 422
    finally:
        app.dependency_overrides.pop(get_session, None)
