from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.database import get_session
from app.lineage.service import incident_lineage, run_lineage
from app.main import app
from app.models import (Base, CleanOrder, Incident, PipelineRun, RawOrder, RecordArrival,
                        RejectedOrder, ReportedOrder, ReportingExpectedOrder, utc_now)
from app.pipeline import service
from app.pipeline.scenarios import V2_HEADER, run_fault_scenario


def by_order(trace, order_id):
    return next(row for row in trace['records'] if row['source_order_id'] == order_id)


def incident_id_for(session, run_id, incident_type):
    return session.scalar(select(Incident.id).where(Incident.run_id == run_id, Incident.incident_type == incident_type))


def snapshot(engine):
    with engine.connect() as connection:
        return {table.name: sorted(json.dumps(dict(row), default=str, sort_keys=True)
                                  for row in connection.execute(select(table)).mappings())
                for table in Base.metadata.sorted_tables}


def assert_graph_consistent(trace):
    ids = [node['id'] for node in trace['nodes']]
    assert len(ids) == len(set(ids))
    assert all(node_id.startswith(trace['run']['id'] + ':') for node_id in ids)
    assert all(edge['source'] in ids and edge['target'] in ids for edge in trace['edges'])
    assert len({edge['id'] for edge in trace['edges']}) == len(trace['edges'])


def test_normal_lineage_and_recorded_time_semantics(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'normal_v2')
    with Session(db_engine) as session:
        trace = run_lineage(session, run_id)
        assert trace['format_version'] == '1'
        assert trace['summary']['evidence_completeness'] == 'complete'
        assert trace['summary']['integrity_status'] == 'consistent'
        assert len(trace['records']) == 2
        assert trace['schema_contract']['version'] == 'orders.v2'
        for row in trace['records']:
            assert row['raw']['status'] == 'observed'
            assert row['validation']['status'] == 'accepted'
            assert row['validation']['event_timestamp'] is None
            assert not row['validation']['separate_validation_event_recorded']
            assert row['clean']['status'] == 'observed'
            assert row['reporting']['status'] == 'observed'
            assert row['reporting']['expected_identity'] == row['reporting']['observed_identity']
            assert row['last_confirmed_stage'] == 'reporting'
            assert row['timing']['time_source'] == 'injected_clock'
            assert row['timing']['arrival_timestamp'].tzinfo is not None
        assert trace['interpretations'] == []
        assert all(node.get('execution_timestamp') is None for node in trace['nodes'])
        assert_graph_consistent(trace)


def test_missing_lineage_localizes_gap_without_root_cause(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'both')
    with Session(db_engine) as session:
        missing_id = incident_id_for(session, run_id, 'MISSING_RECORDS')
        trace = incident_lineage(session, missing_id)
        assert trace['scope']['kind'] == 'incident'
        assert trace['summary']['returned_record_count'] == 1
        assert trace['summary']['total_run_record_count'] == 5
        row = by_order(trace, 'DEMO-002')
        assert row['validation']['status'] == 'accepted'
        assert row['clean']['status'] == 'observed'
        assert row['reporting']['status'] == 'not_observed'
        assert row['reporting']['expected_identity']['raw_order_id'] == row['raw_order_id']
        assert row['reporting']['observed_identity'] is None
        assert row['reporting']['missing_expected_identity'] is True
        assert row['last_confirmed_stage'] == row['last_confirmed_expected_identity_stage'] == 'clean'
        assert row['incident_links'][0]['role'] == 'missing_at_detection'
        assert trace['interpretations'][0]['root_cause'] is None
        assert trace['interpretations'][0]['classification'] == 'inference'
        assert any(edge['relation'] == 'expected_identity_not_observed' for edge in trace['edges'])
        assert_graph_consistent(trace)


def test_duplicate_lineage_includes_accepted_and_rejected_sources(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'both')
    with Session(db_engine) as session:
        incident_id = incident_id_for(session, run_id, 'DUPLICATE_RECORDS')
        trace = incident_lineage(session, incident_id)
        assert len(trace['records']) == 2
        first, duplicate = trace['records']
        assert first['row_number'] == 1 and duplicate['row_number'] == 5
        assert first['validation']['status'] == 'accepted'
        assert first['duplicate_relationship']['role'] == 'accepted_source'
        assert duplicate['validation']['rejection_reason'] == 'DUPLICATE_ORDER_ID'
        assert duplicate['clean']['status'] == duplicate['reporting']['status'] == 'intentionally_excluded'
        assert duplicate['reporting']['missing_expected_identity'] is False
        assert duplicate['duplicate_relationship'] == {'role': 'rejected_duplicate',
            'referenced_raw_order_id': first['raw_order_id'], 'reference_status': 'accepted_source_confirmed'}
        assert first['duplicate_relationship']['rejected_raw_order_ids'] == [duplicate['raw_order_id']]
        assert any(edge['relation'] == 'duplicate_of' for edge in trace['edges'])
        assert_graph_consistent(trace)


def test_invalid_rejection_is_exclusion_not_missing(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'both')
    with Session(db_engine) as session:
        trace = run_lineage(session, run_id)
        row = by_order(trace, 'DEMO-BAD')
        assert row['validation']['status'] == 'rejected'
        assert row['validation']['rejection_reason'] == 'INVALID_QUANTITY'
        assert row['last_confirmed_stage'] == 'validation'
        assert row['clean']['status'] == row['reporting']['status'] == 'intentionally_excluded'
        assert row['reporting']['expected_identity'] is None
        assert row['reporting']['missing_expected_identity'] is False
        assert not any(link['incident_type'] == 'MISSING_RECORDS' for link in row['incident_links'])


@pytest.mark.parametrize('scenario,incident_type', [('schema_drift', 'SCHEMA_DRIFT'), ('late_arrival', 'LATE_ARRIVING_DATA')])
def test_schema_and_late_snapshot_evidence_is_reused(db_engine, scenario, incident_type):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, scenario)
    with Session(db_engine) as session:
        incident_id = incident_id_for(session, run_id, incident_type)
        stored = deepcopy(session.get(Incident, incident_id).evidence)
        trace = incident_lineage(session, incident_id)
        assert trace['incidents'][0]['evidence'] == stored
        assert len(trace['records']) == 1
        row = trace['records'][0]
        if scenario == 'schema_drift':
            assert trace['schema_contract']['expected_schema'] == stored['expected_schema']
            assert row['validation']['rejection_reason'] == 'INVALID_COLUMNS'
            assert row['last_confirmed_stage'] == 'validation'
            assert any(edge['relation'] == 'run_schema_evidence' for edge in trace['edges'])
        else:
            assert row['last_confirmed_stage'] == 'reporting'
            assert row['timing']['event_timestamp'].isoformat() == stored['late_records'][0]['event_timestamp']
            assert row['reporting']['missing_expected_identity'] is False
            assert row['incident_links'][0]['role'] == 'late_at_detection'
        assert_graph_consistent(trace)


def test_header_only_incident_does_not_invent_record_events(db_engine):
    with Session(db_engine) as session:
        run_id = service.execute_pipeline(session, V2_HEADER.replace('customer_id,', ''), 'header-only.csv', schema_version='orders.v2', lateness_seconds=300)
    with Session(db_engine) as session:
        incident_id = incident_id_for(session, run_id, 'SCHEMA_DRIFT')
        trace = incident_lineage(session, incident_id)
        assert trace['records'] == []
        assert {node['kind'] for node in trace['nodes']} == {'run_context', 'incident_snapshot'}
        assert len(trace['edges']) == 1
        assert trace['edges'][0]['relation'] == 'run_schema_evidence'
        assert trace['summary']['total_run_record_count'] == 0


@pytest.mark.parametrize('status', ['running', 'failed'])
def test_no_committed_validation_outcome_is_unknown(db_engine, status):
    with Session(db_engine) as session:
        run_id = service.persist_raw_orders(session, service.SAMPLE_PATH.read_text(), 'unfinished.csv')
    if status == 'failed':
        with Session(db_engine) as session:
            run = session.get(PipelineRun, run_id)
            run.status = 'failed'
            run.finished_at = utc_now()
            run.error_code = 'PROCESSING_FAILED'
            session.commit()
    with Session(db_engine) as session:
        trace = run_lineage(session, run_id)
        assert trace['run']['status'] == status
        for row in trace['records']:
            assert row['last_confirmed_stage'] == 'raw'
            assert row['validation']['status'] == 'unknown'
            assert row['reporting']['status'] == 'unknown'
            assert row['reporting']['missing_expected_identity'] is None
        assert trace['interpretations'] == []
        assert trace['summary']['evidence_completeness'] == 'incomplete'
        assert not any(edge['relation'] == 'validation_outcome' for edge in trace['edges'])


def test_failed_processing_does_not_fabricate_rolled_back_stages(db_engine, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic reporting failure')
    monkeypatch.setattr(service, 'publish_reporting', fail)
    with Session(db_engine) as session:
        with pytest.raises(service.PipelineProcessingError) as error:
            run_fault_scenario(session, 'both')
    with Session(db_engine) as session:
        trace = run_lineage(session, error.value.run_id)
        assert len(trace['records']) == 5
        assert all(row['last_confirmed_stage'] == 'raw' for row in trace['records'])
        assert all(row['validation']['status'] == 'unknown' for row in trace['records'])
        assert trace['reporting_stage'] is None
        assert trace['incidents'] == []


def test_absent_historical_reporting_marker_is_unknown_not_missing(db_engine):
    with Session(db_engine) as session:
        run_id = service.persist_raw_orders(session, service.SAMPLE_PATH.read_text(), 'historical.csv')
        with session.begin():
            service.persist_order_outcomes(session, run_id)
            run = session.get(PipelineRun, run_id)
            for key, value in service.reconcile_counts(session, run_id).items():
                setattr(run, key, value)
            run.status = 'completed'
            run.finished_at = utc_now()
    with Session(db_engine) as session:
        trace = run_lineage(session, run_id)
        accepted = [row for row in trace['records'] if row['clean']['status'] == 'observed']
        assert len(accepted) == 3
        assert all(row['last_confirmed_stage'] == 'clean' for row in accepted)
        assert all(row['reporting']['status'] == 'unknown' for row in accepted)
        assert all(row['reporting']['missing_expected_identity'] is None for row in accepted)
        assert trace['interpretations'] == []


def test_stored_incident_and_current_observation_remain_distinct(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'missing_records')
    with Session(db_engine) as session:
        incident_id = incident_id_for(session, run_id, 'MISSING_RECORDS')
        evidence = deepcopy(session.get(Incident, incident_id).evidence)
        expected = session.scalar(select(ReportingExpectedOrder).where(ReportingExpectedOrder.run_id == run_id, ReportingExpectedOrder.order_id == 'DEMO-002'))
        # Simulate an external change only in this test fixture; no recovery API exists.
        session.add(ReportedOrder(run_id=run_id, raw_order_id=expected.raw_order_id, order_id=expected.order_id))
        session.commit()
    with Session(db_engine) as session:
        trace = incident_lineage(session, incident_id)
        assert trace['incidents'][0]['evidence'] == evidence
        row = trace['records'][0]
        assert row['incident_links'][0]['role'] == 'missing_at_detection'
        assert row['reporting']['status'] == 'observed'
        assert row['last_confirmed_stage'] == 'reporting'
        assert trace['summary']['missing_expected_identity_count'] == 0
        assert trace['interpretations'] == []


def test_wrong_downstream_identity_does_not_hide_observed_stage(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'normal_v2')
    with Session(db_engine) as session:
        row = session.scalar(select(ReportedOrder).where(ReportedOrder.run_id == run_id))
        raw_id = row.raw_order_id
        expected_id = row.order_id
        row.order_id = 'WRONG-ID'
        session.commit()
    with Session(db_engine) as session:
        trace = run_lineage(session, run_id)
        row = next(row for row in trace['records'] if row['raw_order_id'] == raw_id)
        assert row['last_confirmed_stage'] == 'reporting'
        assert row['last_confirmed_expected_identity_stage'] == 'clean'
        assert row['reporting']['status'] == 'identity_mismatch'
        assert row['reporting']['observed_identity']['order_id'] == 'WRONG-ID'
        assert row['reporting']['expected_identity']['order_id'] == expected_id
        assert trace['interpretations'][0]['root_cause'] is None


def test_incomplete_manifest_does_not_invent_missing_expectation(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'normal_v2')
    with Session(db_engine) as session:
        reported = session.scalar(select(ReportedOrder).where(ReportedOrder.run_id == run_id))
        raw_id = reported.raw_order_id
        session.delete(reported)
        session.flush()
        session.delete(session.get(ReportingExpectedOrder, (run_id, raw_id)))
        session.commit()
    with Session(db_engine) as session:
        trace = run_lineage(session, run_id)
        row = next(row for row in trace['records'] if row['raw_order_id'] == raw_id)
        assert row['reporting']['status'] == 'unknown'
        assert row['reporting']['expected_identity'] is None
        assert row['reporting']['missing_expected_identity'] is None
        assert row['last_confirmed_stage'] == 'clean'
        assert any(issue['code'] == 'REPORTING_MANIFEST_COUNT_MISMATCH' for issue in trace['integrity_issues'])


def test_conflicting_outcomes_are_reported_without_relabeling_as_exclusion(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'normal_v2')
    with Session(db_engine) as session:
        raw_id = session.scalar(select(CleanOrder.raw_order_id).where(CleanOrder.run_id == run_id))
        session.add(RejectedOrder(run_id=run_id, raw_order_id=raw_id, reason_code='INVALID_QUANTITY'))
        session.commit()
    with Session(db_engine) as session:
        trace = run_lineage(session, run_id)
        row = next(row for row in trace['records'] if row['raw_order_id'] == raw_id)
        assert row['validation']['status'] == 'conflicting'
        assert row['clean']['status'] == 'observed'
        assert row['reporting']['status'] == 'observed'
        assert trace['summary']['integrity_status'] == 'inconsistent'
        assert 'CONFLICTING_VALIDATION_OUTCOMES' in row['integrity_issues']


def test_missing_current_timing_is_not_filled_from_incident_snapshot(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'late_arrival')
    with Session(db_engine) as session:
        incident_id = incident_id_for(session, run_id, 'LATE_ARRIVING_DATA')
        incident = session.get(Incident, incident_id)
        raw_id = incident.evidence['late_records'][0]['raw_order_id']
        session.delete(session.get(RecordArrival, raw_id))
        session.commit()
    with Session(db_engine) as session:
        trace = incident_lineage(session, incident_id)
        assert trace['records'][0]['timing'] is None
        assert 'ARRIVAL_METADATA_NOT_RECORDED' in trace['records'][0]['evidence_gaps']
        assert trace['incidents'][0]['evidence']['late_records'][0]['event_timestamp']
        assert trace['records'][0]['last_confirmed_stage'] == 'reporting'


def test_run_isolation_and_unresolvable_snapshot_source(db_engine):
    with Session(db_engine) as session:
        first = run_fault_scenario(session, 'both')
        second = run_fault_scenario(session, 'late_arrival')
    with Session(db_engine) as session:
        incident_id = incident_id_for(session, first, 'MISSING_RECORDS')
        foreign_raw_id = session.scalar(select(RawOrder.id).where(RawOrder.run_id == second))
        incident = session.get(Incident, incident_id)
        evidence = deepcopy(incident.evidence)
        evidence['missing_records'][0]['raw_order_id'] = foreign_raw_id
        incident.evidence = evidence
        session.commit()
    with Session(db_engine) as session:
        trace = incident_lineage(session, incident_id)
        assert trace['run']['id'] == first
        assert trace['records'] == []
        assert any(issue['code'] == 'UNRESOLVED_INCIDENT_SOURCE' for issue in trace['integrity_issues'])
        assert all(node.get('raw_order_id') != foreign_raw_id for node in trace['nodes'])
        first_trace = run_lineage(session, first)
        assert all(row['source_order_id'] != 'TIME-001' for row in first_trace['records'])


@pytest.mark.parametrize('payload,code', [({}, 'INCIDENT_SOURCE_REFERENCES_NOT_RECORDED'),
    ([], 'INVALID_INCIDENT_EVIDENCE_SHAPE'), ({'run_id': 'another-run'}, 'INCIDENT_EVIDENCE_RUN_MISMATCH')])
def test_incomplete_incident_evidence_is_explicit(db_engine, payload, code):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'both')
    with Session(db_engine) as session:
        incident_id = incident_id_for(session, run_id, 'MISSING_RECORDS')
        session.get(Incident, incident_id).evidence = payload
        session.commit()
    with Session(db_engine) as session:
        trace = incident_lineage(session, incident_id)
        assert trace['records'] == []
        assert any(issue['code'] == code for issue in trace['integrity_issues'])
        assert trace['summary']['evidence_completeness'] == 'incomplete'


def test_lineage_reads_preserve_data_schema_and_incidents(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'both')
    before = snapshot(db_engine)
    with db_engine.connect() as connection:
        version = MigrationContext.configure(connection).get_current_revision()
        tables = sorted(inspect(connection).get_table_names())
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    with Session(db_engine) as session:
        one = run_lineage(session, run_id)
        two = run_lineage(session, run_id)
        assert one == two
        for incident in one['incidents']:
            assert incident_lineage(session, incident['id'])['incidents'][0]['evidence'] == incident['evidence']
    assert snapshot(db_engine) == before
    with db_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == version == '0004_replay_audit'
        assert sorted(inspect(connection).get_table_names()) == tables
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_read_snapshot_and_pending_changes_guard(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'normal_v2')
    with Session(db_engine) as session:
        run_lineage(session, run_id)
        if db_engine.dialect.name == 'postgresql':
            assert session.scalar(text('SHOW transaction_read_only')) == 'on'
            assert session.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
            with pytest.raises(DBAPIError):
                session.execute(text('UPDATE pipeline_runs SET source_name = source_name WHERE id = :id'), {'id': run_id})
            session.rollback()
    with Session(db_engine) as session:
        session.get(PipelineRun, run_id).source_name = 'uncommitted-change'
        with pytest.raises(ValueError, match='clean read session'):
            run_lineage(session, run_id)
        session.rollback()
    # A new pooled connection can still write after the read-only transaction closes.
    with Session(db_engine) as session:
        another = run_fault_scenario(session, 'normal_v2')
        assert another != run_id


def test_lineage_api_read_only_graph_and_errors(db_engine):
    def test_session():
        with Session(db_engine) as session:
            yield session
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            run = client.post('/pipeline-runs/scenarios/both').json()
            before = snapshot(db_engine)
            response = client.get('/pipeline-runs/' + run['id'] + '/lineage')
            assert response.status_code == 200
            trace = response.json()
            assert_graph_consistent(trace)
            assert client.get('/pipeline-runs/' + run['id'] + '/lineage').json() == trace
            for incident in trace['incidents']:
                result = client.get('/incidents/' + incident['id'] + '/lineage')
                assert result.status_code == 200
                assert result.json()['scope']['incident_id'] == incident['id']
            assert snapshot(db_engine) == before
            assert client.get('/pipeline-runs/' + str(uuid4()) + '/lineage').status_code == 404
            assert client.get('/incidents/' + str(uuid4()) + '/lineage').status_code == 404
            assert client.get('/pipeline-runs/bad/lineage').status_code == 422
            assert client.get('/incidents/bad/lineage').status_code == 422
            assert client.post('/pipeline-runs/' + run['id'] + '/lineage').status_code == 405
            assert client.post('/incidents/' + trace['incidents'][0]['id'] + '/lineage').status_code == 405
            assert client.post('/pipeline-runs/scenarios/normal_v2').status_code == 201
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_unmigrated_database_returns_safe_lineage_errors(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'unmigrated_lineage.sqlite').as_posix()}", connect_args={'check_same_thread': False})
    def test_session():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            for path in ('/pipeline-runs/', '/incidents/'):
                response = client.get(path + str(uuid4()) + '/lineage')
                assert response.status_code == 503
                assert response.json()['detail'] == 'Database unavailable or migrations missing; see README.'
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()


def test_duplicate_reference_without_clean_target_is_not_claimed_accepted(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'both')
    with Session(db_engine) as session:
        incident_id = incident_id_for(session, run_id, 'DUPLICATE_RECORDS')
        target = session.scalar(select(CleanOrder).where(CleanOrder.run_id == run_id, CleanOrder.order_id == 'DEMO-001'))
        raw_id = target.raw_order_id
        session.delete(session.get(ReportedOrder, (run_id, raw_id)))
        session.flush()
        session.delete(session.get(ReportingExpectedOrder, (run_id, raw_id)))
        session.flush()
        session.delete(target)
        session.commit()
    with Session(db_engine) as session:
        trace = incident_lineage(session, incident_id)
        first, duplicate = trace['records']
        assert first['validation']['status'] == 'unknown'
        assert first['duplicate_relationship']['role'] == 'referenced_source_outcome_unknown'
        assert duplicate['duplicate_relationship']['reference_status'] == 'target_outcome_unavailable'
        assert 'DUPLICATE_TARGET_OUTCOME_UNAVAILABLE' in duplicate['evidence_gaps']
        assert trace['summary']['evidence_completeness'] == 'incomplete'


def test_legacy_rows_without_contract_do_not_receive_invented_metadata(db_engine):
    with Session(db_engine) as session:
        run = PipelineRun(source_name='synthetic_legacy.csv', source_csv='legacy source', status='completed',
                          finished_at=utc_now(), source_count=1, accepted_count=1, rejected_count=0, duplicate_count=0)
        session.add(run)
        session.flush()
        raw = RawOrder(run_id=run.id, row_number=1, original_record='legacy source', original_data={'order_id': 'OLD-1'})
        session.add(raw)
        session.flush()
        session.add(CleanOrder(run_id=run.id, raw_order_id=raw.id, order_id='OLD-1', customer_id='CUST-1',
                               quantity=1, unit_price=Decimal('1.00'), order_date=date(2026, 1, 1)))
        run_id = run.id
        session.commit()
    with Session(db_engine) as session:
        trace = run_lineage(session, run_id)
        assert trace['schema_contract'] is None
        assert trace['records'][0]['timing'] is None
        assert trace['records'][0]['reporting']['status'] == 'unknown'
        assert trace['records'][0]['last_confirmed_stage'] == 'clean'
        assert trace['records'][0]['reporting']['missing_expected_identity'] is None
        assert trace['interpretations'] == []
        assert any(gap['code'] == 'SCHEMA_CONTRACT_NOT_RECORDED' for gap in trace['evidence_gaps'])


def test_inspection_never_reexecutes_validation_or_detection(db_engine, monkeypatch):
    from app.incidents import detection
    from app.pipeline import contracts
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'both')
    def forbidden(*args, **kwargs):
        raise AssertionError('Read-only lineage must not run pipeline rules')
    monkeypatch.setattr(contracts, 'validate_contract_order', forbidden)
    monkeypatch.setattr(detection, 'detect_incidents', forbidden)
    with Session(db_engine) as session:
        trace = run_lineage(session, run_id)
        assert len(trace['records']) == 5
        assert trace['summary']['stored_counts_match']
