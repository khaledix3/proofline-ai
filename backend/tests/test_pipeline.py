from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_session
from app.main import app
from app.models import Base, CleanOrder, PipelineRun, RawOrder, RejectedOrder
from app.pipeline import service
from app.pipeline.ingestion import read_orders_csv
from app.pipeline.validation import COLUMNS, validate_order
from conftest import migration_config, require_test_database

HEADER = ','.join(COLUMNS) + '\n'
VALID = dict(zip(COLUMNS, ['ORD-1', 'CUST-1', '2', '12.50', '2026-01-01']))
EXPECTED = {'source_count': 8, 'accepted_count': 3, 'rejected_count': 5, 'duplicate_count': 2}


def test_sample_persistence_and_reconciliation(db_engine):
    with Session(db_engine) as session:
        run_id = service.run_local_sample(session)
    # Reopen to verify committed database records rather than in-memory objects.
    with Session(db_engine) as session:
        result = service.get_run_result(session, run_id)
        assert result['status'] == 'completed'
        assert result['started_at'] is not None and result['finished_at'] is not None
        assert result['counts'] == EXPECTED
        assert service.reconcile_counts(session, run_id) == EXPECTED
        raw = result['raw_orders']
        assert len(raw) == 8
        assert raw[0]['data'] == raw[2]['data']
        assert raw[0]['id'] != raw[2]['id']
        reasons = [r['reason_code'] for r in result['rejected_orders']]
        assert reasons == ['DUPLICATE_ORDER_ID', 'INVALID_QUANTITY', 'INVALID_UNIT_PRICE', 'INVALID_ORDER_DATE', 'DUPLICATE_ORDER_ID']
        assert result['rejected_orders'][0]['duplicate_of_raw_id'] == raw[0]['id']
        assert session.get(PipelineRun, run_id).source_csv == service.SAMPLE_PATH.read_bytes().decode('utf-8')


def test_runs_are_independent(db_engine):
    with Session(db_engine) as session:
        first = service.run_local_sample(session)
        second = service.run_local_sample(session)
        assert first != second
    with Session(db_engine) as session:
        assert service.get_run_result(session, first)['counts'] == EXPECTED
        assert service.get_run_result(session, second)['counts'] == EXPECTED
        assert session.scalar(select(func.count()).select_from(RawOrder)) == 16


def test_raw_preservation_and_normalization(db_engine):
    source = HEADER.replace('\n', '\r\n') + ' ORD-1 ,CUST-1,2,12.50,2026-01-01\r\n'
    with Session(db_engine) as session:
        run_id = service.execute_pipeline(session, source, 'whitespace.csv')
    with Session(db_engine) as session:
        raw = session.scalar(select(RawOrder))
        clean = session.scalar(select(CleanOrder))
        assert raw.original_data['order_id'] == ' ORD-1 '
        assert raw.original_record == source.splitlines(keepends=True)[1]
        assert session.get(PipelineRun, run_id).source_csv == source
        assert clean.order_id == 'ORD-1'
        assert clean.unit_price == Decimal('12.50')


def test_invalid_does_not_claim_duplicate_id(db_engine):
    source = HEADER + 'ORD-1,CUST-1,0,1.00,2026-01-01\nORD-1,CUST-1,1,1.00,2026-01-01\nORD-1,CUST-1,1,1.00,2026-01-01\n'
    with Session(db_engine) as session:
        run_id = service.execute_pipeline(session, source, 'repeat.csv')
    with Session(db_engine) as session:
        assert service.get_run_result(session, run_id)['counts'] == {'source_count': 3, 'accepted_count': 1, 'rejected_count': 2, 'duplicate_count': 1}


def test_failure_keeps_raw_and_rolls_back_outcomes(db_engine, monkeypatch):
    def fail_after_partial_write(session, run_id):
        raw = session.scalar(select(RawOrder).where(RawOrder.run_id == run_id))
        session.add(RejectedOrder(run_id=run_id, raw_order_id=raw.id, reason_code='INVALID_QUANTITY'))
        session.flush()
        raise RuntimeError('injected processing failure')
    monkeypatch.setattr(service, 'persist_order_outcomes', fail_after_partial_write)
    with Session(db_engine) as session:
        with pytest.raises(service.PipelineProcessingError) as error:
            service.run_local_sample(session)
    with Session(db_engine) as session:
        result = service.get_run_result(session, error.value.run_id)
        assert result['status'] == 'failed'
        assert result['error_code'] == 'PROCESSING_FAILED'
        assert len(result['raw_orders']) == 8
        assert result['clean_orders'] == result['rejected_orders'] == []
        assert result['counts'] == {'source_count': 8, 'accepted_count': 0, 'rejected_count': 0, 'duplicate_count': 0}


def test_reconciliation_detects_missing_outcome(db_engine):
    with Session(db_engine) as session:
        run_id = service.persist_raw_orders(session, HEADER + 'ORD-1,CUST-1,2,12.50,2026-01-01\n', 'unprocessed.csv')
        with pytest.raises(ValueError, match='partition'):
            service.reconcile_counts(session, run_id)


def test_database_constraints_and_run_scoping(db_engine):
    with Session(db_engine) as session:
        first = service.run_local_sample(session)
        second = service.run_local_sample(session)
    with Session(db_engine) as session:
        raw = session.scalar(select(RawOrder).where(RawOrder.run_id == first))
        raw_id = raw.id
    with Session(db_engine) as session:
        session.add(RejectedOrder(run_id=second, raw_order_id=raw_id, reason_code='INVALID_QUANTITY'))
        with pytest.raises(IntegrityError):
            session.commit()  # Composite FK prevents cross-run references.
        session.rollback()
    with Session(db_engine) as session:
        original = session.scalar(select(CleanOrder).where(CleanOrder.run_id == first))
        extra_raw = RawOrder(run_id=first, row_number=99, original_record='extra', original_data={})
        session.add(extra_raw)
        session.flush()
        session.add(CleanOrder(run_id=first, raw_order_id=extra_raw.id, order_id=original.order_id,
                               customer_id='CUST-X', quantity=1, unit_price=Decimal('1'), order_date=date(2026, 1, 1)))
        with pytest.raises(IntegrityError):
            session.commit()  # Clean business ID is unique per run.
        session.rollback()
    with Session(db_engine) as session:
        run = session.get(PipelineRun, first)
        run.accepted_count = 100
        with pytest.raises(IntegrityError):
            session.commit()  # Completed metrics must reconcile.
        session.rollback()


def test_api_launch_and_inspect(db_engine):
    def test_session():
        with Session(db_engine) as session:
            yield session
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            launched = client.post('/pipeline-runs/test')
            assert launched.status_code == 201
            result = launched.json()
            assert result['counts'] == EXPECTED
            inspected = client.get('/pipeline-runs/' + result['id'])
            assert inspected.status_code == 200
            assert inspected.json() == result
            assert client.get('/pipeline-runs/' + str(uuid4())).status_code == 404
            assert client.get('/pipeline-runs/not-a-uuid').status_code == 422
            response = client.options('/pipeline-runs/test', headers={
                'Origin': 'http://localhost:5174', 'Access-Control-Request-Method': 'POST'})
            assert response.status_code == 200
            assert response.headers['access-control-allow-origin'] == 'http://localhost:5174'
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_migration_round_trip_and_model_parity(db_engine):
    with db_engine.begin() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        command.downgrade(migration_config(connection), 'base')
        assert 'raw_orders' not in inspect(connection).get_table_names()
        command.upgrade(migration_config(connection), 'head')
        assert set(Base.metadata.tables) <= set(inspect(connection).get_table_names())


@pytest.mark.parametrize('field,value,reason', [
    ('order_id', '', 'INVALID_ORDER_ID'),
    ('customer_id', 'x' * 65, 'INVALID_CUSTOMER_ID'),
    ('quantity', '-1', 'INVALID_QUANTITY'),
    ('quantity', '1.5', 'INVALID_QUANTITY'),
    ('quantity', '1000001', 'INVALID_QUANTITY'),
    ('unit_price', 'NaN', 'INVALID_UNIT_PRICE'),
    ('unit_price', 'Infinity', 'INVALID_UNIT_PRICE'),
    ('unit_price', '-1', 'INVALID_UNIT_PRICE'),
    ('unit_price', '1.001', 'INVALID_UNIT_PRICE'),
    ('unit_price', '100000000', 'INVALID_UNIT_PRICE'),
    ('unit_price', 'abc', 'INVALID_UNIT_PRICE'),
    ('order_date', '2026-02-30', 'INVALID_ORDER_DATE'),
    ('order_date', '20260101', 'INVALID_ORDER_DATE'),
])
def test_validation_reasons(field, value, reason):
    data = {**VALID, field: value}
    snapshot = data.copy()
    assert validate_order(data) == (None, reason)
    assert data == snapshot


def test_csv_boundaries():
    rows = read_orders_csv(HEADER + 'too,few\n\n')
    assert len(rows) == 2
    assert all(validate_order(r.data)[1] == 'INVALID_COLUMNS' for r in rows)
    with pytest.raises(ValueError):
        read_orders_csv('')
    with pytest.raises(ValueError):
        read_orders_csv('wrong,header\n')
    with pytest.raises(ValueError):
        read_orders_csv(HEADER + '"unterminated')
    rows = read_orders_csv(HEADER + 'ORD-1,"multi\nline",1,1.00,2026-01-01\n')
    assert rows[0].original_record == 'ORD-1,"multi\nline",1,1.00,2026-01-01\n'
    assert rows[0].data['customer_id'] == 'multi\nline'


def test_unsafe_test_database_refused():
    for value in ('postgresql+psycopg://localhost/production', 'sqlite:///existing.sqlite'):
        with pytest.raises(ValueError):
            require_test_database(value)


def test_empty_source_and_bad_header(db_engine):
    with Session(db_engine) as session:
        with pytest.raises(ValueError):
            service.execute_pipeline(session, 'wrong,header\n', 'bad.csv')
        assert session.scalar(select(func.count()).select_from(PipelineRun)) == 0
        session.rollback()
        run_id = service.execute_pipeline(session, HEADER, 'empty.csv')
    with Session(db_engine) as session:
        result = service.get_run_result(session, run_id)
        assert result['status'] == 'completed'
        assert all(count == 0 for count in result['counts'].values())


def test_missing_database_configuration(monkeypatch):
    from app import config
    from app.database import get_engine
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(config, 'load_dotenv', lambda *args, **kwargs: None)
    get_engine.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get('/health').status_code == 200
            response = client.post('/pipeline-runs/test')
            assert response.status_code == 503
            assert 'DATABASE_URL' in response.json()['detail']
    finally:
        get_engine.cache_clear()


def test_api_missing_migrations_returns_safe_error(tmp_path):
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{(tmp_path / 'unmigrated.sqlite').as_posix()}", connect_args={'check_same_thread': False})
    def test_session():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            response = client.post('/pipeline-runs/test')
            assert response.status_code == 503
            assert response.json()['detail'] == 'Database unavailable or migrations missing; see README.'
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()


def test_postgresql_offline_migration(monkeypatch):
    from io import StringIO
    from alembic.config import Config
    from conftest import ROOT
    # Dialect compilation only: no login, connection, schema, or database changes.
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://127.0.0.1/proofline_offline_test')
    output = StringIO()
    config = Config(str(ROOT / 'alembic.ini'), output_buffer=output)
    command.upgrade(config, 'head', sql=True)
    sql = output.getvalue()
    for table in Base.metadata.tables:
        assert f'CREATE TABLE {table}' in sql
    assert 'SERIAL' in sql
    assert 'completed_counts_reconcile' in sql
    assert 'FOREIGN KEY(run_id, raw_order_id)' in sql


def test_reconciliation_detects_double_classification(db_engine):
    with Session(db_engine) as session:
        run_id = service.run_local_sample(session)
        accepted_source = session.scalar(select(CleanOrder.raw_order_id).where(CleanOrder.run_id == run_id))
        session.add(RejectedOrder(run_id=run_id, raw_order_id=accepted_source, reason_code='INVALID_QUANTITY'))
        session.flush()
        with pytest.raises(ValueError, match='partition'):
            service.reconcile_counts(session, run_id)
        session.rollback()  # Only this fixture's isolated database/schema was touched.
