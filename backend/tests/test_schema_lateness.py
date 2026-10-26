from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from alembic import command
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.database import get_session
from app.incidents.detection import detect_incidents
from app.incidents.lateness import arrival_delay, late_arrival_evidence
from app.incidents.schema_drift import schema_drift_evidence
from app.main import app
from app.models import (CleanOrder, Incident, PipelineContract, PipelineRun, RawOrder,
                        RecordArrival, RejectedOrder, utc_now)
from app.pipeline.contracts import get_contract
from app.pipeline.scenarios import V2_HEADER, V2_ROWS, run_fault_scenario
from app.pipeline.service import execute_pipeline, get_run_result, reconcile_counts
from app.pipeline.timestamps import parse_event_timestamp
from conftest import migration_config

ARRIVAL = datetime(2026, 1, 1, 10, 10, tzinfo=timezone.utc)


def execute_v2(session, source, **kwargs):
    return execute_pipeline(session, source, 'synthetic_v2_test.csv', schema_version='orders.v2',
                            ingested_at=ARRIVAL, lateness_seconds=300, **kwargs)


def incidents_for(session, run_id):
    return {row.incident_type: row for row in session.scalars(select(Incident).where(Incident.run_id == run_id))}


@pytest.mark.parametrize('scenario,expected_type', [
    ('schema_drift', 'SCHEMA_DRIFT'), ('late_arrival', 'LATE_ARRIVING_DATA'), ('normal_v2', None)])
def test_v2_scenarios_and_exact_evidence(db_engine, scenario, expected_type):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, scenario)
    with Session(db_engine) as session:
        incidents = incidents_for(session, run_id)
        contract = session.get(PipelineContract, run_id)
        assert contract.schema_version == 'orders.v2'
        assert contract.expected_schema == get_contract('orders.v2').definition()
        assert contract.ingestion_timestamp == ARRIVAL
        assert contract.ingestion_timestamp.tzinfo is not None
        assert contract.time_source == 'injected_clock'
        assert contract.lateness_threshold_seconds == 300
        assert set(incidents) == ({expected_type} if expected_type else set())
        counts = reconcile_counts(session, run_id)
        if scenario == 'schema_drift':
            assert counts == {'source_count': 1, 'accepted_count': 0, 'rejected_count': 1, 'duplicate_count': 0}
            incident = incidents[expected_type]
            evidence = incident.evidence
            assert incident.stage == 'source_to_raw'
            assert incident.affected_count == 3
            assert evidence['schema_version'] == 'orders.v2'
            assert evidence['differences']['missing_required_columns'] == ['customer_id']
            assert evidence['differences']['unexpected_columns'] == ['unexpected_note']
            assert evidence['differences']['incompatible_types'] == [{
                'raw_order_id': session.scalar(select(RawOrder.id)), 'row_number': 1,
                'column': 'quantity', 'expected_type': 'integer', 'actual_type': 'string', 'value': 'two'}]
            assert evidence['counts'] == {'difference_count': 3, 'affected_record_count': 1, 'source_record_count': 1}
            assert 'customer_id' not in evidence['actual_schema']['columns']
            assert 'customer_id' in [c['name'] for c in evidence['expected_schema']['columns']]
            raw = session.scalar(select(RawOrder))
            assert raw.original_data['unexpected_note'] == 'synthetic'
            assert raw.original_data['quantity'] == 'two'
        elif scenario == 'late_arrival':
            assert counts == {'source_count': 3, 'accepted_count': 3, 'rejected_count': 0, 'duplicate_count': 0}
            evidence = incidents[expected_type].evidence
            raw_id = session.scalar(select(RawOrder.id).where(RawOrder.run_id == run_id, RawOrder.row_number == 1))
            assert evidence['counts'] == {'eligible_record_count': 3, 'late_record_count': 1, 'on_time_record_count': 2}
            assert evidence['late_records'] == [{
                'raw_order_id': raw_id, 'order_id': 'TIME-001', 'row_number': 1,
                'event_timestamp': '2026-01-01T10:00:00+00:00',
                'expected_deadline': '2026-01-01T10:05:00+00:00',
                'actual_arrival_timestamp': '2026-01-01T10:10:00+00:00',
                'event_age_seconds': 600.0, 'delay_seconds': 300.0}]
            assert evidence['time_source'] == 'injected_clock'
            assert incidents[expected_type].stage == 'source_to_ingestion'
        else:
            assert counts['accepted_count'] == counts['source_count'] == 2


@pytest.mark.parametrize('source,expected_key', [
    (V2_HEADER.replace('customer_id,', '') + V2_ROWS[1].replace('CUST-02,', ''), 'missing_required_columns'),
    (V2_HEADER.rstrip('\n') + ',extra\n' + V2_ROWS[1].rstrip('\n') + ',keep-me\n', 'unexpected_columns'),
    (V2_HEADER + V2_ROWS[1].replace(',2,20.00,', ',two,20.00,'), 'incompatible_types'),
    (V2_HEADER.replace('customer_id', 'order_id') + V2_ROWS[1], 'duplicate_columns'),
    (V2_HEADER + 'too,few,cells\n', 'row_width_mismatches'),
])
def test_schema_difference_categories_preserve_original_source(db_engine, source, expected_key):
    before = deepcopy(get_contract('orders.v2').definition())
    with Session(db_engine) as session:
        run_id = execute_v2(session, source)
    with Session(db_engine) as session:
        run = session.get(PipelineRun, run_id)
        assert run.source_csv == source
        assert run.source_count == 1
        assert run.status == 'completed'
        incident = incidents_for(session, run_id)['SCHEMA_DRIFT']
        assert incident.evidence['differences'][expected_key]
        assert session.get(PipelineContract, run_id).expected_schema == before
        assert get_contract('orders.v2').definition() == before
        assert 'MISSING_RECORDS' not in incidents_for(session, run_id)
        raw = session.scalar(select(RawOrder).where(RawOrder.run_id == run_id))
        assert raw.original_record == source.splitlines(keepends=True)[1]
        if expected_key == 'duplicate_columns':
            assert raw.original_data['_columns'][0:2] == ['order_id', 'order_id']
            assert len(raw.original_data['_cells']) == 6


def test_allowed_extra_columns_and_reordering_are_not_drift(db_engine):
    source = V2_HEADER.rstrip('\n') + ',extra\n' + V2_ROWS[1].rstrip('\n') + ',retained\n'
    with Session(db_engine) as session:
        run_id = execute_v2(session, source, column_policy='allow')
    with Session(db_engine) as session:
        assert incidents_for(session, run_id) == {}
        assert session.get(PipelineRun, run_id).accepted_count == 1
        assert session.scalar(select(RawOrder)).original_data['extra'] == 'retained'
        assert session.get(PipelineContract, run_id).column_policy == 'allow'
    columns = V2_HEADER.strip().split(',')
    cells = V2_ROWS[1].strip().split(',')
    source = ','.join(reversed(columns)) + '\n' + ','.join(reversed(cells)) + '\n'
    with Session(db_engine) as session:
        reordered = execute_v2(session, source)
    with Session(db_engine) as session:
        assert incidents_for(session, reordered) == {}
        assert session.get(PipelineRun, reordered).accepted_count == 1


def test_schema_header_only_drift_has_no_missing_records(db_engine):
    with Session(db_engine) as session:
        run_id = execute_v2(session, V2_HEADER.replace('customer_id,', ''))
    with Session(db_engine) as session:
        incidents = incidents_for(session, run_id)
        assert set(incidents) == {'SCHEMA_DRIFT'}
        assert incidents['SCHEMA_DRIFT'].affected_count == 1
        assert incidents['SCHEMA_DRIFT'].evidence['counts']['affected_record_count'] == 0
        assert all(count == 0 for count in reconcile_counts(session, run_id).values())


def test_rejected_and_duplicate_rows_are_not_late_or_missing(db_engine):
    source = V2_HEADER + V2_ROWS[1] + V2_ROWS[0].replace('TIME-001', 'TIME-002') + V2_ROWS[0].replace(',1,10.00,', ',0,10.00,')
    with Session(db_engine) as session:
        run_id = execute_v2(session, source)
    with Session(db_engine) as session:
        assert set(incidents_for(session, run_id)) == {'DUPLICATE_RECORDS'}
        assert session.get(PipelineRun, run_id).accepted_count == 1
        assert session.get(PipelineRun, run_id).rejected_count == 2
        assert schema_drift_evidence(session, run_id) is None  # Zero is integer-compatible, but fails business validation.
        assert late_arrival_evidence(session, run_id) is None


def test_naive_event_time_is_schema_drift_not_late(db_engine):
    source = V2_HEADER + V2_ROWS[0].replace('T10:00:00Z', 'T10:00:00')
    with Session(db_engine) as session:
        run_id = execute_v2(session, source)
    with Session(db_engine) as session:
        incidents = incidents_for(session, run_id)
        assert set(incidents) == {'SCHEMA_DRIFT'}
        assert incidents['SCHEMA_DRIFT'].evidence['differences']['incompatible_types'][0]['actual_type'] == 'timestamp_without_timezone'
        assert session.scalar(select(RejectedOrder)).reason_code == 'INVALID_EVENT_TIMESTAMP'
        arrival = session.scalar(select(RecordArrival))
        assert arrival.event_timestamp is None
        assert arrival.arrival_timestamp == ARRIVAL


@pytest.mark.parametrize('microseconds,expected_delay', [(-1, None), (0, None), (1, 0.000001), (1000000, 1.0)])
def test_persisted_lateness_boundary(db_engine, microseconds, expected_delay):
    event = parse_event_timestamp('2026-01-01T10:00:00Z')
    arrival = event + timedelta(seconds=300, microseconds=microseconds)
    with Session(db_engine) as session:
        run_id = execute_pipeline(session, V2_HEADER + V2_ROWS[0], 'boundary.csv', schema_version='orders.v2',
                                  ingested_at=arrival, lateness_seconds=300)
    with Session(db_engine) as session:
        incidents = incidents_for(session, run_id)
        metadata = session.scalar(select(RecordArrival))
        assert metadata.arrival_timestamp == arrival
        assert metadata.arrival_timestamp.tzinfo is not None
        if expected_delay is None:
            assert incidents == {}
        else:
            assert set(incidents) == {'LATE_ARRIVING_DATA'}
            assert incidents['LATE_ARRIVING_DATA'].evidence['late_records'][0]['delay_seconds'] == expected_delay


def test_utc_offsets_zero_threshold_and_future_event():
    event = parse_event_timestamp('2026-01-01T13:00:00+03:00')
    arrival = datetime(2026, 1, 1, 5, 5, 1, tzinfo=timezone(timedelta(hours=-5)))
    assert event.isoformat() == '2026-01-01T10:00:00+00:00'
    assert arrival_delay(event, arrival, 300)['delay_seconds'] == 1.0
    assert arrival_delay(event, event, 0) is None
    assert arrival_delay(event, event + timedelta(microseconds=1), 0)['delay_seconds'] == 0.000001
    assert arrival_delay(event, event - timedelta(seconds=1), 0) is None
    maximum = datetime.max.replace(tzinfo=timezone.utc)
    assert arrival_delay(maximum, maximum, 300) is None


@pytest.mark.parametrize('threshold', [-1, True, 1.5, 31536001])
def test_invalid_thresholds_are_rejected(threshold):
    with pytest.raises(ValueError):
        arrival_delay(ARRIVAL, ARRIVAL, threshold)


def test_naive_arrival_and_unknown_contract_do_not_write_runs(db_engine):
    with Session(db_engine) as session:
        with pytest.raises(ValueError, match='timezone'):
            execute_pipeline(session, V2_HEADER, 'bad-clock.csv', schema_version='orders.v2', ingested_at=datetime(2026, 1, 1))
        with pytest.raises(ValueError, match='Unknown expected schema'):
            execute_pipeline(session, V2_HEADER, 'unknown.csv', schema_version='orders.v999')
        assert session.scalar(select(func.count()).select_from(PipelineRun)) == 0


def test_real_clock_is_captured_once_and_stored_as_utc(db_engine, monkeypatch):
    monkeypatch.setenv('LATENESS_THRESHOLD_SECONDS', '300')
    now = utc_now()
    source = V2_HEADER + 'NOW-001,CUST-1,1,1.00,2026-01-01,' + now.isoformat() + '\n'
    with Session(db_engine) as session:
        run_id = execute_pipeline(session, source, 'system-clock.csv', schema_version='orders.v2')
    after = utc_now()
    with Session(db_engine) as session:
        contract = session.get(PipelineContract, run_id)
        metadata = session.scalar(select(RecordArrival))
        assert contract.time_source == 'system_clock'
        assert now <= metadata.arrival_timestamp <= after
        assert contract.ingestion_timestamp == metadata.arrival_timestamp
        assert metadata.event_timestamp == now
        assert incidents_for(session, run_id) == {}


def test_contract_registry_and_evidence_are_stable(db_engine):
    expected = get_contract('orders.v2').definition()
    changed = get_contract('orders.v2').definition()
    changed['columns'][0]['type'] = 'invented'
    assert get_contract('orders.v2').definition() == expected
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'schema_drift')
    with Session(db_engine) as session:
        before = {kind: (row.id, deepcopy(row.evidence)) for kind, row in incidents_for(session, run_id).items()}
        detect_incidents(session, run_id)
        session.commit()
        assert {kind: (row.id, row.evidence) for kind, row in incidents_for(session, run_id).items()} == before


def test_all_four_incident_types_coexist_without_misclassification(db_engine):
    source = V2_HEADER + (
        'DEMO-001,CUST-1,1,1.00,2026-01-01,2026-01-01T10:00:00Z\n'
        'DEMO-002,CUST-2,1,1.00,2026-01-01,2026-01-01T10:06:00Z\n'
        'DEMO-001,CUST-1,1,1.00,2026-01-01,2026-01-01T10:00:00Z\n'
        'DEMO-BAD,CUST-3,two,1.00,2026-01-01,2026-01-01T10:06:00Z\n')
    with Session(db_engine) as session:
        run_id = execute_v2(session, source, omit_demo_order=True)
    with Session(db_engine) as session:
        incidents = incidents_for(session, run_id)
        assert set(incidents) == {'DUPLICATE_RECORDS', 'MISSING_RECORDS', 'SCHEMA_DRIFT', 'LATE_ARRIVING_DATA'}
        assert all(row.affected_count == 1 for row in incidents.values())
        assert incidents['MISSING_RECORDS'].evidence['affected_order_ids'] == ['DEMO-002']
        assert incidents['LATE_ARRIVING_DATA'].evidence['affected_order_ids'] == ['DEMO-001']
        assert reconcile_counts(session, run_id) == {'source_count': 4, 'accepted_count': 2, 'rejected_count': 2, 'duplicate_count': 1}


def test_api_reuses_new_incident_types_and_scenarios(db_engine):
    def test_session():
        with Session(db_engine) as session:
            yield session
    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            for scenario, expected_type in [('schema_drift', 'SCHEMA_DRIFT'), ('late_arrival', 'LATE_ARRIVING_DATA')]:
                launched = client.post('/pipeline-runs/scenarios/' + scenario)
                assert launched.status_code == 201
                run_id = launched.json()['id']
                listed = client.get('/incidents', params={'run_id': run_id, 'incident_type': expected_type})
                assert listed.status_code == 200
                items = listed.json()['items']
                assert len(items) == 1
                assert items[0]['evidence']['schema_version'] == 'orders.v2'
                assert items[0]['run_id'] == run_id
                assert client.get('/incidents/' + items[0]['id']).json() == items[0]
            launched = client.post('/pipeline-runs/scenarios/normal_v2')
            assert launched.status_code == 201
            assert client.get('/incidents', params={'run_id': launched.json()['id']}).json()['items'] == []
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_migration_upgrade_preserves_legacy_data_and_downgrade_refuses_evidence(db_engine):
    with db_engine.begin() as connection:
        command.downgrade(migration_config(connection), '0002_incidents')
    legacy_data = {'order_id': 'LEGACY-1', 'customer_id': 'CUST-1', 'quantity': '1',
                   'unit_price': '1.00', 'order_date': '2026-01-01'}
    legacy_record = ','.join(legacy_data.values()) + '\r\n'
    legacy_source = ','.join(legacy_data) + '\r\n' + legacy_record * 2
    with Session(db_engine) as session:
        run = PipelineRun(source_name='legacy.csv', source_csv=legacy_source, source_count=2,
                          accepted_count=1, rejected_count=1, duplicate_count=1,
                          status='completed', finished_at=utc_now())
        session.add(run)
        session.flush()
        old_id = run.id
        first = RawOrder(run_id=old_id, row_number=1, original_record=legacy_record, original_data=legacy_data)
        duplicate = RawOrder(run_id=old_id, row_number=2, original_record=legacy_record, original_data=legacy_data)
        session.add_all([first, duplicate])
        session.flush()
        session.add(CleanOrder(run_id=old_id, raw_order_id=first.id, order_id='LEGACY-1', customer_id='CUST-1',
                               quantity=1, unit_price=Decimal('1.00'), order_date=date(2026, 1, 1)))
        session.add(RejectedOrder(run_id=old_id, raw_order_id=duplicate.id, reason_code='DUPLICATE_ORDER_ID',
                                  duplicate_of_raw_id=first.id))
        legacy_evidence = {'run_id': old_id, 'first_raw_order_id': first.id, 'duplicate_raw_order_ids': [duplicate.id]}
        session.add(Incident(run_id=old_id, incident_type='DUPLICATE_RECORDS', stage='raw_to_clean',
                             affected_count=1, evidence=legacy_evidence))
        session.commit()
    with db_engine.begin() as connection:
        command.upgrade(migration_config(connection), 'head')
    with Session(db_engine) as session:
        assert session.get(PipelineRun, old_id).source_csv == legacy_source
        assert reconcile_counts(session, old_id) == {'source_count': 2, 'accepted_count': 1, 'rejected_count': 1, 'duplicate_count': 1}
        assert incidents_for(session, old_id)['DUPLICATE_RECORDS'].evidence == legacy_evidence
        assert session.get(PipelineContract, old_id) is None
        # Reading began a transaction, so finish it before invoking the pipeline.
        session.rollback()
        new_id = run_fault_scenario(session, 'late_arrival')
    with db_engine.begin() as connection:
        with pytest.raises(RuntimeError, match='evidence would be lost'):
            command.downgrade(migration_config(connection), '0002_incidents')
        assert MigrationContext.configure(connection).get_current_revision() == '0003_schema_lateness'
        assert 'record_arrivals' in inspect(connection).get_table_names()
    with Session(db_engine) as session:
        assert session.get(PipelineContract, new_id) is not None
        assert 'LATE_ARRIVING_DATA' in incidents_for(session, new_id)


def test_missing_arrival_metadata_is_not_silently_ignored(db_engine):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'normal_v2')
    with Session(db_engine) as session:
        metadata = session.scalar(select(RecordArrival).where(RecordArrival.run_id == run_id))
        session.delete(metadata)  # Only this fixture's synthetic run.
        session.flush()
        with pytest.raises(ValueError, match='complete arrival evidence'):
            late_arrival_evidence(session, run_id)
        session.rollback()


@pytest.mark.parametrize('configured,expected', [('0', 0), ('300', 300), ('31536000', 31536000)])
def test_configured_thresholds(monkeypatch, configured, expected):
    from app import config
    monkeypatch.setattr(config, 'load_dotenv', lambda *args, **kwargs: None)
    monkeypatch.setenv('LATENESS_THRESHOLD_SECONDS', configured)
    assert config.lateness_threshold_seconds() == expected


@pytest.mark.parametrize('configured', ['-1', 'abc', '1.5', '31536001'])
def test_bad_configured_thresholds(monkeypatch, configured):
    from app import config
    monkeypatch.setattr(config, 'load_dotenv', lambda *args, **kwargs: None)
    monkeypatch.setenv('LATENESS_THRESHOLD_SECONDS', configured)
    with pytest.raises(config.ConfigurationError):
        config.lateness_threshold_seconds()


def test_threshold_loader_whitelists_only_its_setting(tmp_path, monkeypatch):
    import os
    from app import config
    monkeypatch.setattr(config, 'PROJECT_ROOT', tmp_path)
    monkeypatch.delenv('LATENESS_THRESHOLD_SECONDS', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'unchanged-sentinel')
    (tmp_path / '.env').write_text('LATENESS_THRESHOLD_SECONDS=60\nDATABASE_URL=do-not-load\n', encoding='utf-8')
    assert config.lateness_threshold_seconds() == 60
    assert os.environ['DATABASE_URL'] == 'unchanged-sentinel'
    assert 'LATENESS_THRESHOLD_SECONDS' not in os.environ


def test_saved_threshold_does_not_change_when_configuration_changes(db_engine, monkeypatch):
    with Session(db_engine) as session:
        run_id = run_fault_scenario(session, 'normal_v2')
    monkeypatch.setenv('LATENESS_THRESHOLD_SECONDS', '0')
    with Session(db_engine) as session:
        assert session.get(PipelineContract, run_id).lateness_threshold_seconds == 300
        assert late_arrival_evidence(session, run_id) is None


def test_utf8_and_crlf_source_records_are_preserved(db_engine):
    source = V2_HEADER.rstrip('\n') + ',extra\r\n' + V2_ROWS[1].rstrip('\n') + ',unicode-\u2028-text\r\n'
    with Session(db_engine) as session:
        run_id = execute_v2(session, source, column_policy='allow')
    with Session(db_engine) as session:
        raw = session.scalar(select(RawOrder).where(RawOrder.run_id == run_id))
        assert raw.original_record == V2_ROWS[1].rstrip('\n') + ',unicode-\u2028-text\r\n'
        assert raw.original_data['extra'] == 'unicode-\u2028-text'
        assert session.get(PipelineRun, run_id).source_csv == source
