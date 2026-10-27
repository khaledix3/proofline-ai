"""Read-only dashboard indexes; fixtures own every created record/schema."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.database import get_session
from app.main import app
from app.replay.audit import persist_audit
from app.replay.service import replay_run
from test_replay import create_source
from test_lineage import snapshot
import pytest

@pytest.fixture
def client(db_engine):
    def session():
        with Session(db_engine) as value:
            yield value
    app.dependency_overrides[get_session] = session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)

def test_run_index_pagination_counts_and_no_source_payload(client, db_engine):
    one, _ = create_source(db_engine)
    two, _ = create_source(db_engine)
    before = snapshot(db_engine)
    page = client.get('/pipeline-runs?limit=1').json()
    assert page['total'] == 2 and len(page['items']) == 1
    assert page['items'][0]['id'] == two
    assert page['items'][0]['counts'] == dict(source_count=5, accepted_count=3, rejected_count=2, duplicate_count=1)
    assert client.get('/pipeline-runs?limit=1&offset=1').json()['items'][0]['id'] == one
    assert client.get('/pipeline-runs?status=failed').json()['items'] == []
    assert 'raw_orders' not in page['items'][0] and 'source_csv' not in str(page)
    assert snapshot(db_engine) == before

@pytest.mark.parametrize('path', ['/pipeline-runs?limit=101', '/pipeline-runs?offset=-1', '/pipeline-runs?status=bogus', '/incidents?sort=bogus'])
def test_indexes_reject_invalid_filters(client, path):
    assert client.get(path).status_code == 422

def test_incident_newest_sort_and_run_filter_preserve_old_default(client, db_engine):
    one, _ = create_source(db_engine)
    two, _ = create_source(db_engine)
    rows = client.get('/incidents?sort=newest').json()['items']
    assert all(item['run_id'] == two for item in rows[:2])
    assert client.get('/incidents').json()['items'][0]['run_id'] == one
    assert {item['run_id'] for item in client.get(f'/incidents?run_id={one}&sort=newest').json()['items']} == {one}

def test_audit_index_scopes_pagination_and_preservation(client, db_engine):
    one, incidents = create_source(db_engine)
    two, others = create_source(db_engine)
    audits = []
    for run, incident, operation in [(one, incidents['MISSING_RECORDS'], 'recover_missing_reporting'), (one, incidents['DUPLICATE_RECORDS'], 'deduplicate_processing'), (two, others['MISSING_RECORDS'], 'recover_missing_reporting')]:
        with Session(db_engine) as session:
            report = replay_run(session, run, incident, operation)
        audits.append(persist_audit(db_engine, report))
    before = snapshot(db_engine)
    result = client.get(f'/pipeline-runs/{one}/replays?incident_id={incidents["MISSING_RECORDS"]}').json()
    assert result['total'] == 1 and result['items'][0]['id'] == audits[0]['id']
    assert result['items'][0]['verification_passed'] is True
    assert 'report' not in result['items'][0]
    assert client.get(f'/pipeline-runs/{one}/replays?incident_id={others["MISSING_RECORDS"]}').json()['total'] == 0
    assert len(client.get(f'/pipeline-runs/{one}/replays?limit=1&offset=1').json()['items']) == 1
    assert client.get(f'/pipeline-runs/{one}/replays?offset=-1').status_code == 422
    assert snapshot(db_engine) == before
