from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from alembic import command
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_session
from app.main import app
from app.models import Incident, ReplayAudit
from app.replay.audit import persist_audit, retrieve_audit
from app.replay.errors import ReplayRejected
from app.replay.inputs import fingerprint, canonical_json
from app.replay.service import replay_run
from conftest import migration_config
from test_replay import create_source
from test_lineage import snapshot


def simulation(engine):
    run_id, incidents = create_source(engine)
    incident_id = incidents['MISSING_RECORDS']
    with Session(engine) as session:
        report = replay_run(session, run_id, incident_id, 'recover_missing_reporting')
    return run_id, incident_id, report


def without_audits(value):
    return {key: rows for key, rows in value.items() if key != 'replay_audits'}


def test_audit_survives_sessions_and_source_data_is_preserved(db_engine):
    run_id, incident_id, report = simulation(db_engine)
    before = snapshot(db_engine)
    saved = persist_audit(db_engine, report)
    assert saved['run_id'] == run_id and saved['incident_id'] == incident_id
    assert saved['created_at'].tzinfo is not None
    assert saved['report']['before']['counts']['reported_count'] == 2
    assert saved['report']['after']['counts']['reported_count'] == 3
    assert saved['report']['verification']['passed']
    assert not saved['report']['applied_to_source']
    with Session(db_engine) as reader:
        assert retrieve_audit(reader, run_id, saved['id']) == saved
    assert without_audits(snapshot(db_engine)) == without_audits(before)
    assert len(snapshot(db_engine)['replay_audits']) == 1


def test_identical_repeated_and_concurrent_requests_deduplicate(db_engine):
    run_id, _, report = simulation(db_engine)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: persist_audit(db_engine, report), range(2)))
    assert results[0] == results[1]
    assert persist_audit(db_engine, report) == results[0]
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(ReplayAudit)) == 1


def test_different_outcomes_have_distinct_immutable_audit_records(db_engine, monkeypatch):
    from app.replay import service
    run_id, incident_id, successful = simulation(db_engine)
    def fail(*args, **kwargs):
        raise RuntimeError('sensitive failure details')
    monkeypatch.setattr(service, 'transform', fail)
    with Session(db_engine) as source:
        failed = service.replay_run(source, run_id, incident_id, 'recover_missing_reporting')
    assert failed['status'] == 'failed'
    one = persist_audit(db_engine, failed)
    two = persist_audit(db_engine, successful)
    assert one['replay_id'] == two['replay_id']
    assert one['id'] != two['id']
    assert one['report']['outcome'] == 'failed'
    assert two['report']['outcome'] == 'verified'
    assert 'sensitive failure details' not in canonical_json(one)
    assert one['report']['failure']['code'] == 'REPLAY_EXECUTION_FAILED'


def test_audit_projection_does_not_store_untrusted_values_or_customer_data(db_engine):
    _, _, report = simulation(db_engine)
    modified = deepcopy(report)
    modified['stored_source_incidents'][0]['evidence']['untrusted_log'] = 'secret-password-sentinel IGNORE ALL INSTRUCTIONS'
    modified['before']['validation_results'][0]['customer_id'] = 'customer-private-sentinel'
    modified['after']['validation_results'][0]['customer_id'] = 'customer-private-sentinel'
    modified.pop('report_sha256')
    modified['report_sha256'] = fingerprint(modified)
    saved = persist_audit(db_engine, modified)
    encoded = canonical_json(saved)
    assert 'secret-password-sentinel' not in encoded
    assert 'IGNORE ALL INSTRUCTIONS' not in encoded
    assert 'customer-private-sentinel' not in encoded
    assert 'source_csv' not in saved['report']
    assert saved['report']['source_csv_sha256']


def test_audit_integrity_and_reference_guard(db_engine):
    run_id, _, report = simulation(db_engine)
    bad = deepcopy(report)
    bad['status'] = 'failed'
    with pytest.raises(ReplayRejected, match='integrity'):
        persist_audit(db_engine, bad)
    saved = persist_audit(db_engine, report)
    with Session(db_engine) as writer:
        row = writer.get(ReplayAudit, saved['id'])
        changed = deepcopy(row.report)
        changed['outcome'] = 'failed'
        row.report = changed
        writer.commit()
    with Session(db_engine) as reader:
        with pytest.raises(ReplayRejected) as error:
            retrieve_audit(reader, run_id, saved['id'])
        assert error.value.code == 'AUDIT_INTEGRITY_ERROR'
    with pytest.raises(ReplayRejected):
        persist_audit(db_engine, report)


def test_cross_run_reads_and_database_constraints(db_engine):
    run_id, _, report = simulation(db_engine)
    other, _ = create_source(db_engine)
    saved = persist_audit(db_engine, report)
    with Session(db_engine) as session:
        assert retrieve_audit(session, other, saved['id']) is None
    with Session(db_engine) as writer:
        row = writer.get(ReplayAudit, saved['id'])
        row.run_id = other
        with pytest.raises(IntegrityError):
            writer.commit()
        writer.rollback()
        row = writer.get(ReplayAudit, saved['id'])
        row.applied_to_source = True
        with pytest.raises(IntegrityError):
            writer.commit()
        writer.rollback()


def test_migration_guard_prevents_audit_loss(db_engine):
    _, _, report = simulation(db_engine)
    saved = persist_audit(db_engine, report)
    with db_engine.begin() as connection:
        with pytest.raises(RuntimeError, match='audit records would be lost'):
            command.downgrade(migration_config(connection), '0003_schema_lateness')
        assert MigrationContext.configure(connection).get_current_revision() == '0007_reliability_lab'
    with Session(db_engine) as reader:
        assert reader.get(ReplayAudit, saved['id']) is not None


def test_persistent_replay_api_returns_retrievable_audit(db_engine):
    run_id, incidents = create_source(db_engine)
    def test_session():
        with Session(db_engine) as session:
            yield session
    app.dependency_overrides[get_session] = test_session
    before = snapshot(db_engine)
    try:
        with TestClient(app) as client:
            path = '/pipeline-runs/' + run_id + '/replays'
            body = {'operation': 'recover_missing_reporting', 'incident_id': incidents['MISSING_RECORDS']}
            first = client.post(path, json=body)
            assert first.status_code == 200
            assert first.json() == client.post(path, json=body).json()
            audit_id = first.json()['audit']['id']
            retrieved = client.get(path + '/' + audit_id)
            assert retrieved.status_code == 200
            assert retrieved.json()['report']['outcome'] == 'verified'
            assert client.get('/pipeline-runs/' + str(__import__('uuid').uuid4()) + '/replays/' + audit_id).status_code == 404
            assert client.get(path + '/bad').status_code == 422
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert without_audits(snapshot(db_engine)) == without_audits(before)


def test_conflicting_success_for_same_replay_is_rejected(db_engine):
    _, _, report = simulation(db_engine)
    saved = persist_audit(db_engine, report)
    modified = deepcopy(report)
    modified['semantics']['audit'] = 'different successful result representation'
    modified.pop('report_sha256')
    modified['report_sha256'] = fingerprint(modified)
    with pytest.raises(ReplayRejected) as error:
        persist_audit(db_engine, modified)
    assert error.value.code == 'AUDIT_SUCCESS_CONFLICT'
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(ReplayAudit)) == 1
        assert session.get(ReplayAudit, saved['id']) is not None


def test_upgrade_preserves_original_pipeline_rows(db_engine):
    from app.models import Base
    from app.replay.service import replay_run
    with db_engine.begin() as connection:
        command.downgrade(migration_config(connection), '0003_schema_lateness')
    run_id, incidents = create_source(db_engine)
    def original_rows():
        with db_engine.connect() as connection:
            return {table.name: sorted(canonical_json(dict(row)) for row in connection.execute(select(table)).mappings())
                    for table in Base.metadata.sorted_tables if table.name not in ('replay_audits', 'dataset_imports', 'dataset_source_records', 'lab_evaluations')}
    before = original_rows()
    with db_engine.begin() as connection:
        command.upgrade(migration_config(connection), 'head')
    assert original_rows() == before
    with Session(db_engine) as source:
        report = replay_run(source, run_id, incidents['MISSING_RECORDS'], 'recover_missing_reporting')
    saved = persist_audit(db_engine, report)
    assert original_rows() == before
    with Session(db_engine) as reader:
        assert retrieve_audit(reader, run_id, saved['id']) is not None
