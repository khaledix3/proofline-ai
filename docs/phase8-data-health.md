# Phase 8: health, business impact and isolated reliability evaluation

This phase adds read-only health/impact APIs and a separately persisted reliability lab audit.
Existing pipeline data, incident snapshots and replay audits are not rewritten. The previous
Arabic/RTL interface and UCI adapter remain in place. No external AI calls are required.

## Open the dashboard

From `C:\Users\khale\proofline-ai`, start the existing backend with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8002
```

In another terminal in `frontend`, use `npm.cmd run dev`. Open http://localhost:5174.
The existing Vite proxy forwards `/api` to port 8002. CORS still permits localhost:5174.
Use **مركز صحة البيانات** and filter **بيانات UCI الحقيقية**. Use an incident's
**أثر البيانات على العمل** tab for impact and its lineage button to trace confirmed stages.
Use **مختبر الاعتمادية** to select an existing source run, seed, bounded record count and
supported scenario. Opening saved evaluations is a GET; it does not rerun the experiment.
Repeated POSTs with identical selected inputs/configuration return the original measured report.

## API

- `GET /data-health?source_kind=uci_real&limit=20&offset=0`
- `GET /data-health/compare?run_id=<UUID>&against_run_id=<UUID>`
- `GET /incidents/<UUID>/impact?limit=25&offset=0`
- `POST /reliability-lab/evaluations` with `source_run_id`, `scenario`, `record_limit`, `seed`
- `GET /reliability-lab/evaluations?source_run_id=<UUID>&limit=10&offset=0`
- `GET /reliability-lab/evaluations/<UUID>`

Scenario values: `all`, `normal`, `missing_records`, `duplicate_outputs`, `schema_drift`,
`late_arrival`. Only these operations are accepted; extra fields/code/SQL are rejected.
A single fault evaluation also runs a normal control. `all` runs five independent cases.

## Metric definitions

`source_count` counts actual preserved raw rows. `processed_record_count` counts distinct
raw identities with clean or rejection outcomes. `rejected_count` already includes
`duplicate_count`; do not add them. Validation failures are rejected minus duplicate rejections.
Rates divide accepted/rejected by raw only for completed, complete, nonconflicting partitions.
A failed/partial/empty run has unavailable rates rather than a fabricated zero.

Current missing counts compare exact expected `(raw_order_id, order_id)` identities with
reporting observations. The publication marker, full clean/expected identity manifest and
counts must agree; otherwise the count is unknown. Reporting duplicate excess counts are
separate from raw duplicate rejections. Intentional validation exclusions are never missing.

Schema counts are saved incident counts, not numbers of affected records. Lateness totals
include explicit monitoring coverage: unmonitored runs make the full-scope late total unknown.
Recorded late evidence remains separately available. Stored incident snapshots retain their
original detection-time semantics. History contains actual run observations only, at most
100 points; no calendar filling or historical detection latency is invented. Health aggregates
at most 1,000 runs and rejects larger scopes explicitly; pagination does not silently change totals.
Run comparisons are descriptive, with same-source-subset metadata and no causal claim.

## Business impact evidence

`analytics.impact.verify_source` checks the official acquired workbook fingerprint, mapping
version, source row coverage and recomputed subset digest. `verified_line_value` checks the
original source identity, original-to-raw mapping, persisted clean fields, Quantity and UnitPrice.
Only verified UCI source-line values use GBP: the official UCI field documentation specifies
sterling. Invoice lines are not unique invoices. C-prefix cancellations, returns/nonpositive
quantities, missing customer references, invalid values or mismatched provenance do not acquire
invented monetary meaning. Zero price is a valid known zero.

An exact identity mismatch can occur even while a physical reporting row exists. Such a mismatch
is not proof of an absent monetary value. A full associated absent value is available only if all
current missing rows have verified source values and are physically absent. Partial verified
subtotals are explicitly separate. `reported_orders` stores identities, not amounts: even a
verified source value is **not confirmed revenue loss**, an actual downstream monetary total,
or evidence of fraud/root cause. Both record references and unknowns are returned. Scope is
one stored incident; current observations may differ from its historical snapshot.

## Lab isolation, ground truth and metrics

`lab.service.capture_source` reads a fresh consistent read-only source snapshot, independently
validates accepted input against its stored schema and clean fields, and selects at most 50
valid rows using the supplied seed. It never labels naturally rejected source records as faults.
Source input is bounded at 10,000 raw rows. Customer/raw values are used transiently; only
source references, fingerprints and minimized evidence are retained in audit reports.

`run_case` reuses the existing private SQLite in-memory sandbox, ingestion, validation,
reconciliation, publishing, incident detectors, lineage and replay services. No cloned pipeline
rows are persisted in development PostgreSQL. The lab uses a labeled simulated UTC clock:
normal event 10:00, arrival 10:05, threshold 300 seconds (equality is on time); late event
09:59 is 60 seconds beyond its deadline. Source InvoiceDate is not interpreted as known UTC.

Independent injected truth is established before detection:

| Case | Injection | Expected identity labels |
|---|---|---|
| normal | none | none |
| missing | remove second private reporting row | one missing |
| duplicate output | replace second output business ID with first | both collision identities duplicate; second expected identity missing |
| schema drift | remove customer_id, add lab_extra, set first quantity to text | every selected input identity affected by structural drift; three schema differences |
| late | move first simulated event one minute earlier | one late |

`lab.metrics.score/metrics` compare `(incident_type, source_identity)` sets per isolated case:
TP is intersection, FP predicted-only, FN expected-only. Aggregation prefixes case identity so
the same source record in separate experiments is not collapsed. Precision=TP/(TP+FP),
recall=TP/(TP+FN), F1=2TP/(2TP+FP+FN). Undefined denominators produce null with reason codes,
not perfect scores. The normal control has zero false positives but undefined precision/recall/F1.
These small deterministic fixtures do not estimate production accuracy or broad detector coverage.

Detector execution milliseconds measure only the actual `detect_incidents` call, including its
queries/flushes; copying, lineage, replay and audit persistence are excluded. Timing varies by
machine/load and is not historical detection latency. Cached requests retain original measurements.

Missing-record replay refuses unexpected/replaced output identities. Thus the incidental missing
identity in the duplicate-output case explicitly rejects that operation; duplicate-processing
replay is the supported correction. Replays execute on further private copies and remain unapplied.
Their minimized comparisons live inside the lab report, not production replay_audits with fictitious
foreign keys. Virtual run/incident IDs are clearly scoped to the report; source run navigation is separate.

## Persistence and migration

Migration `0007_reliability_lab` adds only `lab_evaluations`: source run FK, unique evaluation key,
source fingerprint, algorithm/configuration, UTC start/finish, outcome, report and integrity digest.
Repeated requests use the existing advisory-lock mechanism and unique key. Reports are hash-checked
when retrieved; malformed metadata/report combinations are refused. Operational case failures are
saved with safe fixed codes, without driver exception text; incomplete evaluations have unavailable
aggregate accuracy ratios. Failed cached evaluations are retained for audit, not silently overwritten.

Downgrade refuses loss of lab audits and pre-existing replay/provenance evidence before DDL.
Do not reset data to downgrade. Existing migrations/tests retain safety guards. Fingerprint checks
before/after test execution verify development source tables and public test tables are unchanged.
PostgreSQL tests create/drop only their own random schemas in **proofline_test**; SQLite cases use
fixture-owned files and the lab uses private memory. Tests never load DATABASE_URL for their engine.

Run backend tests from the project root (`--basetemp` must be a fresh disposable ignored directory):

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=work/phase8_manual_tests_new
.\.venv\Scripts\python.exe -m compileall -q backend/app backend/tests backend/migrations
.\.venv\Scripts\python.exe -m pip check
```

From `frontend`: `npm.cmd test`, `npm.cmd run build`, `npm.cmd run lint`.
Keep `.env` and `.env.test` ignored. This phase adds no required credentials/dependencies.

## Limits to preserve in future work

No recovery application, continuous monitor, authentication, production concurrency/load benchmark,
independent third-party audit signature, or broad fuzz evaluation is added here. Hashes detect
accidental changes; an actor able to rewrite both database reports and hashes can forge them.
The endpoints are intended for the existing local environment. UCI source history has no actual
arrival timestamps; simulated lateness is always identified as such. Expand fixture/seed coverage
and profile scoped query costs before making broader performance claims.
