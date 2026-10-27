"""Read-only health metrics from durable rows. No invented monitoring history."""
from collections import Counter
from sqlalchemy import select, func, and_
from app.models import (PipelineRun, RawOrder, CleanOrder, RejectedOrder, ReportedOrder,
                        ReportingExpectedOrder, ReportingStage, PipelineContract, Incident, DatasetImport)
from app.lineage.service import start_read_snapshot
from app.datasets.service import source_summary

TYPES = ("DUPLICATE_RECORDS", "MISSING_RECORDS", "SCHEMA_DRIFT", "LATE_ARRIVING_DATA")
MAX_SCOPE_RUNS = 1000

class AnalyticsError(ValueError):
    def __init__(self, code, status_code=422):
        self.code, self.status_code = code, status_code
        super().__init__(code)


def percent(numerator, denominator):
    return round(numerator * 100 / denominator, 4) if denominator else None


def scoped_runs(session, source_kind):
    query = select(PipelineRun)
    if source_kind:
        dataset_ids = select(DatasetImport.run_id)
        if source_kind != "local_demo":
            dataset_ids = dataset_ids.where(DatasetImport.source_kind == source_kind)
        query = query.where(~PipelineRun.id.in_(dataset_ids) if source_kind == "local_demo" else PipelineRun.id.in_(dataset_ids))
    rows = session.scalars(query.order_by(PipelineRun.started_at.desc(), PipelineRun.id).limit(MAX_SCOPE_RUNS + 1)).all()
    if len(rows) > MAX_SCOPE_RUNS:
        raise AnalyticsError("HEALTH_SCOPE_TOO_BROAD")
    return rows


def build_run_metrics(session, runs):
    ids = [run.id for run in runs]
    if not ids:
        return []
    def counts(model, predicate=None):
        query = select(model.run_id, func.count()).where(model.run_id.in_(ids))
        if predicate is not None: query = query.where(predicate)
        return dict(session.execute(query.group_by(model.run_id)).tuples().all())
    raw, clean, rejected = counts(RawOrder), counts(CleanOrder), counts(RejectedOrder)
    duplicates = counts(RejectedOrder, RejectedOrder.reason_code == "DUPLICATE_ORDER_ID")
    expected, reported = counts(ReportingExpectedOrder), counts(ReportedOrder)
    outcomes = select(CleanOrder.run_id, CleanOrder.raw_order_id).where(CleanOrder.run_id.in_(ids)).union(
        select(RejectedOrder.run_id, RejectedOrder.raw_order_id).where(RejectedOrder.run_id.in_(ids))).subquery()
    processed = dict(session.execute(select(outcomes.c.run_id, func.count()).group_by(outcomes.c.run_id)).tuples().all())
    overlap = dict(session.execute(select(CleanOrder.run_id, func.count()).join(RejectedOrder,
        and_(RejectedOrder.run_id == CleanOrder.run_id, RejectedOrder.raw_order_id == CleanOrder.raw_order_id))
        .where(CleanOrder.run_id.in_(ids)).group_by(CleanOrder.run_id)).tuples().all())
    missing = dict(session.execute(select(ReportingExpectedOrder.run_id, func.count()).outerjoin(ReportedOrder,
        and_(ReportedOrder.run_id == ReportingExpectedOrder.run_id,
             ReportedOrder.raw_order_id == ReportingExpectedOrder.raw_order_id,
             ReportedOrder.order_id == ReportingExpectedOrder.order_id))
        .where(ReportingExpectedOrder.run_id.in_(ids), ReportedOrder.raw_order_id.is_(None))
        .group_by(ReportingExpectedOrder.run_id)).tuples().all())
    matching_manifest = dict(session.execute(select(CleanOrder.run_id, func.count()).join(ReportingExpectedOrder,
        and_(ReportingExpectedOrder.run_id == CleanOrder.run_id,
             ReportingExpectedOrder.raw_order_id == CleanOrder.raw_order_id,
             ReportingExpectedOrder.order_id == CleanOrder.order_id))
        .where(CleanOrder.run_id.in_(ids)).group_by(CleanOrder.run_id)).tuples().all())
    excess = Counter()
    for run_id, _, number in session.execute(select(ReportedOrder.run_id, ReportedOrder.order_id, func.count())
        .where(ReportedOrder.run_id.in_(ids)).group_by(ReportedOrder.run_id, ReportedOrder.order_id).having(func.count() > 1)):
        excess[run_id] += number - 1
    stages = {r.run_id:r for r in session.scalars(select(ReportingStage).where(ReportingStage.run_id.in_(ids)))}
    contracts = {r.run_id:r for r in session.scalars(select(PipelineContract).where(PipelineContract.run_id.in_(ids)))}
    datasets = {r.run_id:r for r in session.scalars(select(DatasetImport).where(DatasetImport.run_id.in_(ids)))}
    incidents = {run.id:[] for run in runs}
    for row in session.scalars(select(Incident).where(Incident.run_id.in_(ids)).order_by(Incident.detected_at, Incident.id)):
        incidents[row.run_id].append({"id":row.id,"incident_type":row.incident_type,"affected_count":row.affected_count,"stage":row.stage,"detected_at":row.detected_at})
    result = []
    for run in runs:
        key = run.id
        source_count, accepted, rejected_count = raw.get(key,0), clean.get(key,0), rejected.get(key,0)
        partition = not overlap.get(key,0) and accepted + rejected_count == source_count and processed.get(key,0) == source_count
        rate_known = bool(source_count) and partition and run.status == "completed"
        stage = stages.get(key)
        reporting_known = bool(stage and stage.expected_count == expected.get(key,0) == accepted == matching_manifest.get(key,0) and run.status == "completed")
        contract = contracts.get(key)
        recorded_late = sum(row['affected_count'] for row in incidents[key] if row['incident_type']=='LATE_ARRIVING_DATA')
        snapshots = {kind:sum(row['incident_type']==kind for row in incidents[key]) for kind in TYPES}
        actual_counts = {"source_count":source_count,"accepted_count":accepted,"rejected_count":rejected_count,"duplicate_count":duplicates.get(key,0)}
        result.append({"id":key,"status":run.status,"source_name":run.source_name,"started_at":run.started_at,
            "finished_at":run.finished_at,"source":source_summary(datasets.get(key)),"counts":actual_counts,
            "processed_record_count":processed.get(key,0),"unprocessed_record_count":max(0,source_count-processed.get(key,0)),
            "validation_failure_count":rejected_count-duplicates.get(key,0),
            "validation_partition_complete":bool(partition),"stored_counts_match":all(getattr(run,k)==v for k,v in actual_counts.items()),
            "acceptance_percentage":percent(accepted,source_count) if rate_known else None,
            "rejection_percentage":percent(rejected_count,source_count) if rate_known else None,
            "percentage_unavailable_reason":None if rate_known else "EMPTY_OR_INCOMPLETE_OUTCOMES",
            "expected_reporting_count":expected.get(key,0),"reported_count":reported.get(key,0),
            "missing_downstream_record_count":missing.get(key,0) if reporting_known else None,
            "reporting_comparison_known":reporting_known,"duplicate_output_excess_count":excess[key],
            "schema_monitoring_enabled":bool(contract and contract.monitor_schema),
            "lateness_monitoring_enabled":bool(contract and contract.lateness_threshold_seconds is not None),
            "recorded_late_record_count":recorded_late,"incident_breakdown":snapshots,"incidents":incidents[key]})
    return result


def data_health(session, source_kind=None, limit=20, offset=0):
    start_read_snapshot(session)
    metrics = build_run_metrics(session,scoped_runs(session,source_kind))
    totals = {name:sum(row['counts'][name] for row in metrics) for name in ('source_count','accepted_count','rejected_count','duplicate_count')}
    complete = bool(metrics) and all(row['validation_partition_complete'] and row['status']=='completed' for row in metrics)
    unknown_reporting = sum(not row['reporting_comparison_known'] for row in metrics)
    known_missing = sum(row['missing_downstream_record_count'] or 0 for row in metrics)
    lateness_covered = sum(row['lateness_monitoring_enabled'] for row in metrics)
    summary = {**totals,"run_count":len(metrics),"processed_record_count":sum(row['processed_record_count'] for row in metrics),
        "unprocessed_record_count":sum(row['unprocessed_record_count'] for row in metrics),
        "validation_failure_count":sum(row['validation_failure_count'] for row in metrics),
        "acceptance_percentage":percent(totals['accepted_count'],totals['source_count']) if complete else None,
        "rejection_percentage":percent(totals['rejected_count'],totals['source_count']) if complete else None,
        "known_missing_downstream_record_count":known_missing,
        "missing_downstream_record_count":known_missing if metrics and not unknown_reporting else None,
        "unknown_reporting_run_count":unknown_reporting,
        "duplicate_output_excess_count":sum(row['duplicate_output_excess_count'] for row in metrics),
        "schema_drift_incident_count":sum(row['incident_breakdown']['SCHEMA_DRIFT'] for row in metrics),
        "schema_monitoring_run_count":sum(row['schema_monitoring_enabled'] for row in metrics),
        "recorded_late_record_count":sum(row['recorded_late_record_count'] for row in metrics),
        "lateness_monitoring_run_count":lateness_covered,
        "late_arriving_record_count":sum(row['recorded_late_record_count'] for row in metrics) if metrics and lateness_covered==len(metrics) else None,
        "status_breakdown":dict(Counter(row['status'] for row in metrics)),
        "incident_breakdown":{kind:sum(row['incident_breakdown'][kind] for row in metrics) for kind in TYPES}}
    history = list(reversed(metrics[:100]))
    return {"format_version":"1","scope":{"source_kind":source_kind,"max_scope_runs":MAX_SCOPE_RUNS},"summary":summary,
        "items":metrics[offset:offset+limit],"total":len(metrics),"limit":limit,"offset":offset,
        "history":{"status":"available" if len(history)>=2 else "insufficient_data","points":history,
                   "observations":len(history),"max_points":100,"truncated":len(metrics)>100,
                   "semantics":"Actual run observations only; no zero-filled dates, extrapolation or production accuracy claim."},
        "definitions":{"source_count":"Count of preserved raw rows, not unique invoices.",
            "processed_record_count":"Distinct raw identities with a clean/rejected outcome; raw duplicates are already inside rejected_count.",
            "percentages":"Accepted/rejected divided by raw source count; unavailable for empty, partial or conflicting outcomes.",
            "missing":"Current expected reporting identities without an exact matching observation; unavailable for absent/inconsistent stage manifests.",
            "incidents":"Stored detection-time snapshots; validation exclusions are separate. Schema affected counts are differences, not record counts.",
            "lateness":"Stored late-record evidence, with explicit monitoring coverage. Unmonitored historical data is unknown, not zero lateness.",
            "source_filter":"Controlled and demonstration runs are not evidence of natural production incident rates."}}


def compare_runs(session, run_id, against_run_id):
    start_read_snapshot(session)
    if run_id==against_run_id: raise AnalyticsError("COMPARE_DISTINCT_RUNS")
    runs = session.scalars(select(PipelineRun).where(PipelineRun.id.in_([run_id,against_run_id]))).all()
    if len(runs)!=2: raise AnalyticsError("RUN_NOT_FOUND",404)
    indexed = {row['id']:row for row in build_run_metrics(session,runs)}
    first, second = indexed[run_id],indexed[against_run_id]
    def subset(row):
        source=row['source'];return (source.get('workbook_sha256'),source.get('subset_sha256'),source.get('provenance',{}).get('mapping_version'))
    comparable = all(subset(first)) and subset(first)==subset(second)
    keys = ['processed_record_count','validation_failure_count','acceptance_percentage','rejection_percentage','missing_downstream_record_count','duplicate_output_excess_count']
    return {"first":first,"second":second,"delta_first_minus_second":{key:first[key]-second[key] if first[key] is not None and second[key] is not None else None for key in keys},
            "same_verified_source_subset":bool(comparable),"causal_claim":False,
            "caveat":"Same source subset does not establish identical policies or real monitoring history; otherwise comparison is descriptive only."}
