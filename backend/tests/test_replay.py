from copy import deepcopy
import hashlib
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.database import get_session
from app.main import app
from app.models import (CleanOrder, Incident, PipelineContract, PipelineRun, RawOrder,
                        RecordArrival, ReportedOrder, utc_now)
from app.pipeline.scenarios import run_fault_scenario
from app.replay import operations, sandbox, service
from app.replay.errors import ReplayRejected
from app.replay.inputs import canonical_json, fingerprint
from test_lineage import snapshot


def create_source(engine, scenario='both'):
    with Session(engine) as session:
        run_id = run_fault_scenario(session, scenario)
    with engine.connect() as connection:
        incidents = dict(connection.execute(select(Incident.incident_type, Incident.id).where(Incident.run_id == run_id)).tuples().all())
    return run_id, incidents


def execute(engine, run_id, incident_id, operation):
    with Session(engine) as session:
        return service.replay_run(session, run_id, incident_id, operation)


def inject_duplicate_projection(engine, run_id):
    with Session(engine) as session:
        output = session.scalar(select(ReportedOrder).where(ReportedOrder.run_id == run_id, ReportedOrder.order_id == 'DEMO-003'))
        output.order_id = 'DEMO-001'  # Fixture-owned data only; original raw/outcomes remain intact.
        session.commit()


def test_missing_replay_corrects_only_temporary_output(db_engine):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    report = execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert report['status'] == 'verified'
    assert report['verification']['passed']
    assert all(report['verification']['checks'].values())
    assert report['before']['counts'] == {'source_count': 5, 'accepted_count': 3, 'rejected_count': 2,
        'duplicate_count': 1, 'expected_reporting_count': 3, 'reported_count': 2, 'duplicate_output_excess_count': 0}
    assert report['after']['counts']['reported_count'] == 3
    assert report['comparison']['count_deltas']['reported_count'] == 1
    assert report['comparison']['removed_reporting_identities'] == []
    added = report['comparison']['added_reporting_identities']
    assert len(added) == 1 and added[0]['order_id'] == 'DEMO-002'
    assert report['before']['incident_status']['MISSING_RECORDS']['detected_in_simulation']
    assert not report['after']['incident_status']['MISSING_RECORDS']['detected_in_simulation']
    assert report['after']['incident_status']['DUPLICATE_RECORDS']['detected_in_simulation']
    assert not report['applied_to_source']
    assert report['isolation']['sandbox_disposed']
    assert snapshot(db_engine) == original
    with Session(db_engine) as session:
        assert session.get(Incident, incidents['MISSING_RECORDS']) is not None
        assert len(session.scalars(select(ReportedOrder).where(ReportedOrder.run_id == run_id)).all()) == 2


def test_duplicate_projection_is_corrected_and_other_missing_record_preserved(db_engine):
    run_id, incidents = create_source(db_engine)
    inject_duplicate_projection(db_engine, run_id)
    original = snapshot(db_engine)
    report = execute(db_engine, run_id, incidents['DUPLICATE_RECORDS'], 'deduplicate_processing')
    assert report['status'] == 'verified'
    assert report['verification']['passed']
    before, after = report['before'], report['after']
    assert before['counts']['duplicate_output_excess_count'] == 1
    assert after['counts']['duplicate_output_excess_count'] == 0
    assert before['counts']['reported_count'] == after['counts']['reported_count'] == 2
    assert before['raw_order_ids'] == after['raw_order_ids']
    assert before['validation_results'] == after['validation_results']
    assert before['counts']['source_count'] == after['counts']['source_count'] == 5
    assert after['counts']['duplicate_count'] == 1
    assert after['incident_status']['DUPLICATE_RECORDS']['detected_in_simulation']
    assert after['incident_status']['MISSING_RECORDS']['evidence']['affected_order_ids'] == ['DEMO-002']
    changes = report['proposed_correction']['reporting_identity_changes']
    assert len(changes) == 1
    assert changes[0]['before_order_id'] == 'DEMO-001' and changes[0]['after_order_id'] == 'DEMO-003'
    assert snapshot(db_engine) == original


def test_already_correct_duplicate_processing_is_honest_no_change(db_engine):
    run_id, incidents = create_source(db_engine)
    report = execute(db_engine, run_id, incidents['DUPLICATE_RECORDS'], 'deduplicate_processing')
    assert report['status'] == 'no_change_required'
    assert report['verification']['passed']
    assert report['comparison']['added_reporting_identities'] == []
    assert report['comparison']['removed_reporting_identities'] == []
    assert report['before'] == report['after']
    assert report['after']['incident_status']['DUPLICATE_RECORDS']['affected_count'] == 1


@pytest.mark.parametrize('operation,incident_type', [('recover_missing_reporting', 'MISSING_RECORDS'), ('deduplicate_processing', 'DUPLICATE_RECORDS')])
def test_repeated_replays_are_deterministic_and_auditable(db_engine, operation, incident_type):
    run_id, incidents = create_source(db_engine)
    if operation == 'deduplicate_processing':
        inject_duplicate_projection(db_engine, run_id)
    original = snapshot(db_engine)
    first = execute(db_engine, run_id, incidents[incident_type], operation)
    second = execute(db_engine, run_id, incidents[incident_type], operation)
    assert canonical_json(first) == canonical_json(second)
    assert first['replay_id'] == second['replay_id']
    unhashed = deepcopy(first)
    digest = unhashed.pop('report_sha256')
    assert fingerprint(unhashed) == digest
    with db_engine.connect() as connection:
        csv = connection.scalar(select(PipelineRun.source_csv).where(PipelineRun.id == run_id))
    assert first['input_references']['source_csv_sha256'] == hashlib.sha256(csv.encode('utf-8')).hexdigest()
    assert first['input_references']['run_id'] == run_id
    assert first['input_references']['incident_id'] == incidents[incident_type]
    assert len(first['input_references']['raw_order_ids']) == 5
    assert snapshot(db_engine) == original


def test_changed_source_creates_different_snapshot_fingerprint(db_engine):
    run_id, incidents = create_source(db_engine)
    first = execute(db_engine, run_id, incidents['DUPLICATE_RECORDS'], 'deduplicate_processing')
    inject_duplicate_projection(db_engine, run_id)
    second = execute(db_engine, run_id, incidents['DUPLICATE_RECORDS'], 'deduplicate_processing')
    assert first['input_references']['source_fingerprint'] != second['input_references']['source_fingerprint']
    assert first['replay_id'] != second['replay_id']


def test_source_session_is_server_read_only_and_no_source_dml_runs(db_engine):
    run_id, incidents = create_source(db_engine)
    statements = []
    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())
    event.listen(db_engine, 'before_cursor_execute', observe)
    try:
        with Session(db_engine) as session:
            report = service.replay_run(session, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
            assert report['verification']['passed']
            if db_engine.dialect.name == 'postgresql':
                assert session.scalar(text('SHOW transaction_read_only')) == 'on'
                assert session.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
        assert set(statements) <= {'SELECT', 'SET', 'SHOW', 'PRAGMA'}
    finally:
        event.remove(db_engine, 'before_cursor_execute', observe)


def test_sandboxes_are_private_and_disposed_on_success_and_failure(db_engine, monkeypatch):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    owned, disposed = [], []
    real_create = sandbox.create_engine
    def create_owned(url, **kwargs):
        assert url == 'sqlite+pysqlite:///:memory:'
        engine = real_create(url, **kwargs)
        assert engine is not db_engine
        owned.append(engine)
        event.listen(engine, 'engine_disposed', lambda target: disposed.append(target))
        return engine
    monkeypatch.setattr(sandbox, 'create_engine', create_owned)
    report = execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert report['status'] == 'verified'
    assert len(owned) == len(disposed) == 2
    real_transform = service.transform
    def fail_after_private_write(*args, **kwargs):
        real_transform(*args, **kwargs)
        raise RuntimeError('private diagnostic sentinel must not be exposed')
    monkeypatch.setattr(service, 'transform', fail_after_private_write)
    report = execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert report['status'] == 'failed'
    assert report['after'] is None and report['comparison'] is None
    assert not report['verification']['passed']
    assert report['isolation']['sandbox_disposed']
    assert 'private diagnostic sentinel' not in canonical_json(report)
    assert len(owned) == len(disposed) == 4
    assert snapshot(db_engine) == original


def test_failed_verification_never_claims_success_or_changes_source(db_engine, monkeypatch):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    monkeypatch.setattr(service, 'transform', lambda *args, **kwargs: None)
    report = execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert report['status'] == 'verification_failed'
    assert not report['verification']['checks']['missing_identities_recovered_in_simulation']
    assert not report['applied_to_source']
    assert report['isolation']['sandbox_disposed']
    assert snapshot(db_engine) == original


def test_clone_failure_disposes_temporary_engine(db_engine, monkeypatch):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    disposed = []
    real_create = sandbox.create_engine
    def broken_engine(url, **kwargs):
        engine = real_create(url, **kwargs)
        event.listen(engine, 'engine_disposed', lambda target: disposed.append(target))
        def block(*args, **kwargs):
            raise RuntimeError('synthetic temporary database failure')
        event.listen(engine, 'before_cursor_execute', block)
        return engine
    monkeypatch.setattr(sandbox, 'create_engine', broken_engine)
    report = execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert report['status'] == 'failed' and report['before'] is None
    assert report['isolation']['sandbox_disposed']
    assert len(disposed) == 1
    assert snapshot(db_engine) == original


@pytest.mark.parametrize('operation', ['apply', 'DROP TABLE raw_orders', 'execute_python', None, {}])
def test_unsupported_operations_are_rejected_explicitly(db_engine, operation):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, run_id, incidents['MISSING_RECORDS'], operation)
    assert error.value.code == 'UNSUPPORTED_OPERATION'
    assert snapshot(db_engine) == original


@pytest.mark.parametrize('scenario,incident_type', [('schema_drift', 'SCHEMA_DRIFT'), ('late_arrival', 'LATE_ARRIVING_DATA')])
def test_unsupported_incident_types_are_not_replayed(db_engine, scenario, incident_type):
    run_id, incidents = create_source(db_engine, scenario)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, run_id, incidents[incident_type], 'recover_missing_reporting')
    assert error.value.code == 'UNSUPPORTED_INCIDENT_OPERATION'


def test_replay_cannot_cross_run_boundaries(db_engine):
    first, incidents = create_source(db_engine)
    second, _ = create_source(db_engine)
    original = snapshot(db_engine)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, second, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert error.value.code == 'INCIDENT_NOT_IN_RUN'
    assert error.value.status_code == 404
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, str(uuid4()), incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert error.value.code == 'RUN_NOT_FOUND'
    assert snapshot(db_engine) == original


def test_stale_missing_evidence_is_rejected(db_engine):
    run_id, incidents = create_source(db_engine)
    with Session(db_engine) as session:
        incident = session.get(Incident, incidents['MISSING_RECORDS'])
        data = deepcopy(incident.evidence)
        data['counts']['actual_count'] = 123
        incident.evidence = data
        session.commit()
    original = snapshot(db_engine)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert error.value.code == 'STALE_INCIDENT_EVIDENCE'
    assert snapshot(db_engine) == original


def test_unrelated_downstream_mismatch_is_not_silently_corrected(db_engine):
    run_id, incidents = create_source(db_engine)
    with Session(db_engine) as session:
        row = session.scalar(select(ReportedOrder).where(ReportedOrder.run_id == run_id, ReportedOrder.order_id == 'DEMO-003'))
        row.order_id = 'UNRELATED-WRONG-ID'
        session.commit()
    original = snapshot(db_engine)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, run_id, incidents['DUPLICATE_RECORDS'], 'deduplicate_processing')
    assert error.value.code == 'UNRELATED_IDENTITY_MISMATCH'
    assert snapshot(db_engine) == original


@pytest.mark.parametrize('mutation,code', [('raw', 'SOURCE_INPUT_MISMATCH'), ('contract', 'CONTRACT_CHANGED'),
    ('arrival', 'INCOMPLETE_LINEAGE'), ('validation', 'SOURCE_VALIDATION_MISMATCH'),
    ('missing_contract', 'MISSING_REPLAY_EVIDENCE'), ('not_completed', 'RUN_NOT_COMPLETED')])
def test_unsafe_or_incomplete_sources_are_rejected(db_engine, mutation, code):
    run_id, incidents = create_source(db_engine)
    with Session(db_engine) as session:
        if mutation == 'raw':
            raw = session.scalar(select(RawOrder).where(RawOrder.run_id == run_id))
            raw.original_record = 'altered original text'
        elif mutation == 'contract':
            contract = session.get(PipelineContract, run_id)
            definition = deepcopy(contract.expected_schema)
            definition['columns'][0]['type'] = 'invented'
            contract.expected_schema = definition
        elif mutation == 'arrival':
            session.delete(session.scalar(select(RecordArrival).where(RecordArrival.run_id == run_id)))
        elif mutation == 'validation':
            session.scalar(select(CleanOrder).where(CleanOrder.run_id == run_id)).quantity = 99
        elif mutation == 'missing_contract':
            for arrival in session.scalars(select(RecordArrival).where(RecordArrival.run_id == run_id)):
                session.delete(arrival)
            session.flush()
            session.delete(session.get(PipelineContract, run_id))
        else:
            session.get(PipelineRun, run_id).status = 'failed'
        session.commit()
    original = snapshot(db_engine)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert error.value.code == code
    assert snapshot(db_engine) == original


def test_active_source_transactions_are_refused(db_engine):
    run_id, incidents = create_source(db_engine)
    with Session(db_engine) as session:
        session.get(PipelineRun, run_id)
        with pytest.raises(ReplayRejected) as error:
            service.replay_run(session, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
        assert error.value.code == 'SOURCE_SESSION_NOT_FRESH'


def test_no_migration_or_persistent_replay_rows(db_engine):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    with db_engine.connect() as connection:
        before_version = MigrationContext.configure(connection).get_current_revision()
    execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    with db_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == before_version == '0003_schema_lateness'
    assert snapshot(db_engine) == original


def test_v2_late_evidence_and_timestamps_are_preserved(db_engine):
    from app.pipeline.service import execute_pipeline
    from app.pipeline.scenarios import V2_HEADER
    from datetime import datetime, timezone
    source = V2_HEADER + (
        'DEMO-001,CUST-1,1,1.00,2026-01-01,2026-01-01T10:00:00Z\n'
        'DEMO-002,CUST-2,1,1.00,2026-01-01,2026-01-01T10:06:00Z\n')
    with Session(db_engine) as session:
        run_id = execute_pipeline(session, source, 'synthetic_v2_missing.csv', schema_version='orders.v2',
            ingested_at=datetime(2026, 1, 1, 10, 10, tzinfo=timezone.utc), lateness_seconds=300, omit_demo_order=True)
    with db_engine.connect() as connection:
        incident_id = connection.scalar(select(Incident.id).where(Incident.run_id == run_id, Incident.incident_type == 'MISSING_RECORDS'))
    original = snapshot(db_engine)
    report = execute(db_engine, run_id, incident_id, 'recover_missing_reporting')
    assert report['status'] == 'verified'
    assert report['before']['incident_status']['LATE_ARRIVING_DATA'] == report['after']['incident_status']['LATE_ARRIVING_DATA']
    assert report['after']['incident_status']['LATE_ARRIVING_DATA']['evidence']['late_records'][0]['delay_seconds'] == 300
    assert snapshot(db_engine) == original


def test_api_simulates_and_rejects_apply_code_paths_and_extra_fields(db_engine):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    def test_session():
        with Session(db_engine) as session:
            yield session
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            path = '/pipeline-runs/' + run_id + '/replays'
            body = {'operation': 'recover_missing_reporting', 'incident_id': incidents['MISSING_RECORDS']}
            response = client.post(path, json=body)
            assert response.status_code == 200
            assert response.json()['status'] == 'verified'
            assert response.json() == client.post(path, json=body).json()
            for extra in ('apply', 'code', 'sql', 'database_url', 'output_path'):
                assert client.post(path, json={**body, extra: 'not-allowed'}).status_code == 422
            assert client.post(path, json={**body, 'operation': 'apply'}).status_code == 422
            assert client.post(path, json={**body, 'operation': 'deduplicate_processing'}).status_code == 409
            assert client.post('/pipeline-runs/' + str(uuid4()) + '/replays', json=body).status_code == 404
            assert client.post('/pipeline-runs/bad/replays', json=body).status_code == 422
            assert client.post(path, json={**body, 'incident_id': 'bad'}).status_code == 422
            assert client.get(path).status_code == 405
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert snapshot(db_engine) == original


def test_already_recovered_missing_snapshot_is_stale_not_reapplied(db_engine):
    from app.models import ReportingExpectedOrder
    run_id, incidents = create_source(db_engine)
    with Session(db_engine) as session:
        expected = session.scalar(select(ReportingExpectedOrder).where(ReportingExpectedOrder.run_id == run_id, ReportingExpectedOrder.order_id == 'DEMO-002'))
        session.add(ReportedOrder(run_id=run_id, raw_order_id=expected.raw_order_id, order_id=expected.order_id))
        session.commit()
    original = snapshot(db_engine)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert error.value.code == 'STALE_INCIDENT_EVIDENCE'
    assert snapshot(db_engine) == original


def test_missing_replay_refuses_replacement_of_wrong_output(db_engine):
    from app.incidents.detection import missing_evidence
    run_id, incidents = create_source(db_engine)
    inject_duplicate_projection(db_engine, run_id)
    with Session(db_engine) as session:
        current = missing_evidence(session, run_id)
        stored = session.get(Incident, incidents['MISSING_RECORDS'])
        stored.evidence = current
        stored.affected_count = current['counts']['missing_count']
        session.commit()
    original = snapshot(db_engine)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert error.value.code == 'UNSAFE_DOWNSTREAM_STATE'
    assert snapshot(db_engine) == original


def test_malformed_csv_and_size_limits_fail_closed(db_engine, monkeypatch):
    from app.replay import inputs
    run_id, incidents = create_source(db_engine)
    monkeypatch.setattr(inputs, 'MAX_RECORDS', 1)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert error.value.code == 'REPLAY_SIZE_LIMIT'
    monkeypatch.setattr(inputs, 'MAX_RECORDS', 10000)
    with Session(db_engine) as session:
        session.get(PipelineRun, run_id).source_csv = '"unterminated'
        session.commit()
    original = snapshot(db_engine)
    with pytest.raises(ReplayRejected) as error:
        execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert error.value.code == 'INVALID_SOURCE_CSV'
    assert snapshot(db_engine) == original


def test_transform_that_changes_raw_evidence_fails_verification(db_engine, monkeypatch):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    real_transform = service.transform
    def unsafe_internal_transform(session, *args, **kwargs):
        real_transform(session, *args, **kwargs)
        session.scalar(select(RawOrder)).original_record = 'sandbox corruption'
        session.flush()
    monkeypatch.setattr(service, 'transform', unsafe_internal_transform)
    report = execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert report['status'] == 'verification_failed'
    assert not report['verification']['checks']['protected_input_evidence_unchanged']
    assert snapshot(db_engine) == original


def test_cleanup_errors_are_not_reported_as_successful_disposal(db_engine, monkeypatch):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    real_create = sandbox.create_engine
    def cleanup_error(url, **kwargs):
        engine = real_create(url, **kwargs)
        real_dispose = engine.dispose
        def dispose_then_error(*args, **kwargs):
            real_dispose(*args, **kwargs)
            raise RuntimeError('synthetic disposal diagnostic')
        engine.dispose = dispose_then_error
        return engine
    monkeypatch.setattr(sandbox, 'create_engine', cleanup_error)
    report = execute(db_engine, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    assert report['status'] == 'failed'
    assert not report['isolation']['sandbox_disposed']
    assert not report['verification']['passed']
    assert report['isolation']['temporary_databases_disposed'] < report['isolation']['temporary_databases_created']
    assert snapshot(db_engine) == original


def test_duplicate_replay_preserves_even_completely_absent_reporting(db_engine):
    run_id, incidents = create_source(db_engine)
    with Session(db_engine) as session:
        for row in session.scalars(select(ReportedOrder).where(ReportedOrder.run_id == run_id)):
            session.delete(row)
        session.commit()
    original = snapshot(db_engine)
    report = execute(db_engine, run_id, incidents['DUPLICATE_RECORDS'], 'deduplicate_processing')
    assert report['status'] == 'no_change_required'
    assert report['before']['counts']['reported_count'] == report['after']['counts']['reported_count'] == 0
    assert report['verification']['checks']['unrelated_missing_sources_preserved']
    assert snapshot(db_engine) == original


def test_caller_owned_connection_transaction_is_not_used_as_source(db_engine):
    run_id, incidents = create_source(db_engine)
    original = snapshot(db_engine)
    with db_engine.begin() as connection:
        with Session(bind=connection) as session:
            with pytest.raises(ReplayRejected) as error:
                service.replay_run(session, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
            assert error.value.code == 'SOURCE_SESSION_NOT_FRESH'
    assert snapshot(db_engine) == original
