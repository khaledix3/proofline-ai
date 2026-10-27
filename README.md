# ProofLine AI

AI Data Incident Investigation & Recovery Platform.

Phase 6 adds persistent replay audits and an optional evidence-grounded LLM investigation adapter to the PostgreSQL pipeline, four incident types, lineage, and isolated Safe Replay. It includes an identity-only downstream reporting stage, deterministic synthetic fault scenarios, and read-only incident inspection. LLM requests are opt-in and disabled by default. No corrections are automatically applied, and there is no public deployment or production pipeline scheduling.

## Existing local services

Use PowerShell in `C:\Users\khale\proofline-ai`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8002
```

In a second terminal:

```powershell
cd C:\Users\khale\proofline-ai\frontend
npm.cmd run dev
```

Open http://localhost:5174. The existing welcome page checks `/api/health`, forwarded by Vite to `http://127.0.0.1:8002/health`. CORS allows `http://localhost:5174` and `http://127.0.0.1:5174`, including POST requests. The page checks on load; refresh after starting the backend. No frontend pipeline interface has been added.

Port 8000 was unavailable during Phase 1. The verified startup command intentionally omits `--reload`: its Windows worker failed with a named-pipe error in the execution environment. No administrator access, PowerShell activation, or execution-policy changes are required.

## PostgreSQL configuration

PostgreSQL 18 is installed locally. Authenticated connections to `proofline_dev` and `proofline_test` have been verified. The two dedicated databases were created with explicit user permission during setup. Phase 3B upgrades both to Alembic revision `0003_schema_lateness` and preserves existing application rows. Credentials remain only in ignored local files.

1. The project databases are `proofline_dev` and `proofline_test`, as supplied by the user. Use your existing PostgreSQL login with access to these databases. No database creation is required if they already exist; this configuration step does not recreate or clear them.
2. The project-root `.env` and `.env.test` files have been created with blank values if they did not already exist. Fill DATABASE_URL in `.env` for `proofline_dev` and TEST_DATABASE_URL in `.env.test` for `proofline_test`, using the placeholders in `.env.example`. Existing local files are preserved. This is the one setup step requiring credentials that the project cannot infer. Do not commit `.env` or share credentials in chat. Percent-encode special characters in URL credentials.
3. Install dependencies and apply the migration from the project root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-lock.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

`.env` is loaded from the project root independently of the current directory. An existing `DATABASE_URL` environment variable takes precedence. Only `postgresql+psycopg` URLs naming a database are accepted in application configuration; there is no silent SQLite fallback. Tables are created by Alembic, never automatically at application startup.

If an installation encounters temporary-folder permissions in this execution environment, use a writable project folder for that terminal session:

```powershell
New-Item -ItemType Directory -Force work/tmp | Out-Null
$env:TEMP = "$PWD\work\tmp"
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
```

Restart the backend after database configuration changes. `GET /health` remains independent of the database. Pipeline endpoints return HTTP 503 with a safe message when configuration, connectivity, or migrated tables are missing; `/health` alone does not establish database readiness.

## Launch and inspect the local sample

After successful PostgreSQL provisioning and migration, run in PowerShell:

```powershell
$run = Invoke-RestMethod -Method Post http://127.0.0.1:8002/pipeline-runs/test
$run.counts
Invoke-RestMethod "http://127.0.0.1:8002/pipeline-runs/$($run.id)"
```

The same endpoints are accessible through Vite at `/api/pipeline-runs/test` and `/api/pipeline-runs/{id}`. API documentation is available at http://127.0.0.1:8002/docs.

- `POST /pipeline-runs/test`: synchronously processes only `backend/data/synthetic_orders.csv`, returning HTTP 201 and a persisted result. It takes no file paths or uploads. Every invocation creates a new run, even for identical input.
- `GET /pipeline-runs/{id}`: returns status, timestamps, counts, raw records, accepted records, and rejected reasons; an unknown UUID returns 404 and a malformed UUID returns 422.

These are local development endpoints without authentication. Keep the backend bound to `127.0.0.1`.

## Data and count definitions

The bundled CSV is explicitly synthetic, deterministic, and contains eight source records. Its expected result is **8 source, 3 accepted, 5 rejected, including 2 duplicates**. These counts have been verified in the development PostgreSQL database and in isolated SQLite/PostgreSQL integration tests.

| Count | Definition |
| --- | --- |
| source_count | Number of parsed data records after the required header, including duplicate and invalid rows. |
| accepted_count | Valid records retained as clean orders, with one order ID per run. |
| rejected_count | Records rejected for validation or duplication. One primary reason per record. |
| duplicate_count | Otherwise valid records whose normalized order ID was previously accepted in this run. A subset of rejected_count. |

Completed runs satisfy `source_count = accepted_count + rejected_count`. Do not add duplicate_count again. Validation runs before duplicate detection: an invalid occurrence does not reserve its order ID. A later valid row may be accepted. Subsequent valid occurrences of the same stripped, case-sensitive ID are duplicates even when other values differ. Deduplication is scoped to a run, not across historical runs.

The source CSV text (including header and line endings), each original record text, and unmodified cell strings are retained. Blank/uneven data records are counted and rejected as `INVALID_COLUMNS`. For legacy orders.v1, wrong headers, empty files, and malformed CSV syntax fail before a run is created; the source file is not modified. Header-only files complete with zero records. The legacy orders.v1 CSV contract is UTF-8 without BOM, with exactly this header and order:

```csv
order_id,customer_id,quantity,unit_price,order_date
```

Validation trims only the clean copy. IDs must contain 1–64 ASCII letters, digits, underscores, or hyphens. Quantity must be an integer between 1 and 1,000,000. Unit price must be a finite decimal between 0 and 99,999,999.99 with at most two fractional digits; it is never silently rounded. Dates must be real calendar dates in `YYYY-MM-DD` form. Validation returns the first failure in field order as an explicit reason code.

## Tables and transactions

- `pipeline_runs`: UUID execution ID, running/completed/failed status, UTC start/end timestamps, original CSV, counts, and safe failure code.
- `raw_orders`: generated row ID, run ID, one-based record number, exact record text, and original JSON fields. Only `(run_id, row_number)` is unique; business order IDs are deliberately not unique here.
- `clean_orders`: source record/run references and typed validated fields. Order IDs are unique only within a run. Prices use fixed-precision decimals.
- `rejected_orders`: source record/run references, explicit reason code, and the first accepted source record for duplicate rejections.

Composite foreign keys prevent cross-run source references. Database checks enforce nonnegative counts, completed-run count arithmetic, valid status/reason codes, numeric ranges, and duplicate-reference requirements. Source records have at most one outcome within each outcome table. The reconciliation function additionally verifies that clean and rejected records form a disjoint, complete partition of raw records across both tables.

Raw ingestion commits first. Processing, outcomes, reconciliation, downstream publication, incident evidence, and completion then commit atomically in a second transaction. An ordinary processing/reporting/detection exception rolls back outcomes, reporting rows, and incidents and records a failed run while preserving raw evidence. Failed/running runs can have unprocessed records: `source_count - accepted_count - rejected_count`; completed-run reconciliation does not apply until processing succeeds. A process crash or database outage can leave a run running; automatic retries/recovery are outside this phase.

## Tests and data safety

From the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe -m compileall -q backend
.\.venv\Scripts\python.exe -m pip check
cd frontend
npm.cmd run build
npm.cmd run lint
```

Default persistence tests use a fresh temporary SQLite file per test with foreign-key enforcement and the actual Alembic migration. They never read `DATABASE_URL`, never migrate the development database, and never delete existing project/application data. PostgreSQL-dialect migration SQL is compiled offline as a separate check; that is not an online PostgreSQL test.

Optional PostgreSQL integration tests require a **dedicated** database named exactly `proofline_test`. Use the existing dedicated test database and a role allowed to create schemas there. Set `TEST_DATABASE_URL` in the ignored project-root `.env.test` using the template in `.env.example`; do not use the development or production database. A terminal environment variable takes precedence, including a blank value that disables PostgreSQL integration tests. The fixture refuses other database names and driver schemes before connecting. The runner reads only TEST_DATABASE_URL from `.env.test`; it never loads `.env`, imports other settings from the test file, expands `${...}` variables, or writes these values to the process environment.

An autouse test connection guard rejects every SQLAlchemy PostgreSQL connection that does not name `proofline_test`, before network access. A negative test proves `proofline_dev` is blocked. For each PostgreSQL test, the fixture creates a uniquely named `proofline_test_<uuid>` schema, runs migrations using a search path containing only that schema, and removes only its own schema on completion. It never drops the database or migrates/drops public tables. The test role needs permission to create schemas in that dedicated database. With a blank/unset TEST_DATABASE_URL in both the terminal environment and `.env.test`, PostgreSQL cases are explicitly skipped; they are not reported as passed.

The existing dependency emits a Starlette/httpx deprecation warning during tests; it does not fail them.

## Learning map

| Responsibility | Function / file |
| --- | --- |
| CSV parsing and original text preservation | `read_orders_csv()` in `backend/app/pipeline/ingestion.py` |
| Field validation, typed values, reason codes | `validate_order()` in `backend/app/pipeline/validation.py` |
| Duplicate lookup among accepted IDs | `find_duplicate()` in `backend/app/pipeline/validation.py` |
| Source evidence persistence | `persist_raw_orders()` in `backend/app/pipeline/service.py` |
| Accepted/rejected record persistence | `persist_order_outcomes()` in `backend/app/pipeline/service.py` |
| Actual record/count reconciliation | `reconcile_counts()` in `backend/app/pipeline/service.py` |
| Run lifecycle and transaction boundaries | `execute_pipeline()` in `backend/app/pipeline/service.py` |
| Configuration and lazy database sessions | `backend/app/config.py`, `backend/app/database.py` |
| Schema and constraints | `backend/app/models.py`, `backend/migrations/versions/0001_pipeline_foundation.py` |
| Isolated test databases and optional PostgreSQL schemas | `backend/tests/conftest.py` |

## Dependency recreation and deployment limit

Use Python 3.13 or newer to create `.venv` with `-m venv .venv`. The working interpreter found on this machine is `C:\Users\khale\AppData\Local\Python\pythoncore-3.13-64\python.exe`. Install the exact verified Python dependencies with `backend/requirements-lock.txt`; `backend/requirements.txt` lists direct dependencies. In `frontend`, use `npm.cmd ci` to reproduce its lock file.

The production frontend build is checked for compilation only. Vite's proxy is development-only; production hosting will require routing `/api` to the backend. No commit or push is performed by setup.

Implementation references: [SQLAlchemy sessions and transactions](https://docs.sqlalchemy.org/en/20/orm/session_basics.html), [Alembic migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html).


## Local credential files

- `C:\Users\khale\proofline-ai\.env`: development DATABASE_URL for database `proofline_dev`.
- `C:\Users\khale\proofline-ai\.env.test`: test TEST_DATABASE_URL for database `proofline_test`.
- `.env.example`: tracked placeholders for both settings; never put real credentials here.

Edit the ignored local files directly in your editor using your PostgreSQL username and password. Do not send credentials through chat or terminal commands. Percent-encode special characters in URL credentials (for example, `@` becomes `%40`, `#` becomes `%23`, and `%` becomes `%25`). Literal dotenv values are loaded without interpolation. Use a test role allowed to create schemas only in the dedicated test database where possible.

Git ignores `.env` and all `.env.*` files except `.env.example`. The real local files are not added to Git. Do not use Git force-add on them. Never paste their contents into reports. Restart the backend after filling development settings; tests automatically read `.env.test` on their next run. Nonblank incorrect test credentials fail tests with a safe setup message; they do not silently skip. Configuration does not grant database permissions or change Windows security settings.


## Phase 3A: evidence-based incidents

New pipeline runs now execute these stages inside the processing transaction:

1. Preserve raw CSV records (separately committed first, as before).
2. Validate rows, reject invalid/duplicate rows, and reconcile raw/clean/rejected counts.
3. Save a manifest of accepted `(raw_order_id, order_id)` identities in `reporting_expected_orders`, with its expected count in `reporting_stages`.
4. Publish identity-only output rows in `reported_orders`.
5. Detect incidents and store evidence snapshots in `incidents`.

The original four table schemas are unchanged. Additive migration `0002_incidents` creates only `reporting_stages`, `reporting_expected_orders`, `reported_orders`, and `incidents`; no historical run is backfilled or reprocessed. Old runs without a published reporting stage are not evidence of missing records.

`DUPLICATE_RECORDS` recomputes validation and duplicate grouping from the preserved raw fields using the same rules as processing: stripped IDs, case-sensitive comparison, first valid occurrence within a run wins. It checks the expected duplicates against persisted rejections and clean source identities before storing an incident. Invalid repeated IDs do not count as duplicates. Evidence includes the run/stage, rule version, normalized business IDs, first source record/row number, duplicate source record IDs/row numbers, occurrence counts, and raw/valid/invalid/clean/duplicate counts. Affected count means excess valid duplicate rows, not the number of distinct business IDs.

`MISSING_RECORDS` compares manifest identities to published downstream identities using a set difference. Rejected invalid and duplicate rows never enter the expected manifest. Evidence stores both identity sets, missing/unexpected identities, and expected/actual/matched/missing/unexpected counts. These reconcile as `expected = matched + missing` and `actual = matched + unexpected`. Equal total counts cannot hide a substituted identity. The reporting fault flag is audit metadata; detection examines stored records and does not rely on that flag.

Completed pipeline counts still reconcile independently of incident status. A missing reporting record does not remove or change a clean source row. For example, 3 accepted clean records with 2 reporting records produce a one-record missing incident while the raw processing counts remain reconciled.

There is at most one stored snapshot of each incident type per run. Repeated detection preserves existing evidence and IDs. API clients can only inspect incidents; there is no update, resolution, recovery, or replay endpoint. Database constraints enforce source/run references, known incident types/stages, positive affected counts, and unique incident types per run. Snapshots are preserved by service behavior; no database immutability trigger is claimed.

### Controlled synthetic scenarios

```powershell
$run = Invoke-RestMethod -Method Post http://127.0.0.1:8002/pipeline-runs/scenarios/both
Invoke-RestMethod "http://127.0.0.1:8002/incidents?run_id=$($run.id)"
```

`POST /pipeline-runs/scenarios/{scenario}` accepts `duplicate_records`, `missing_records`, `both`, `schema_drift`, `late_arrival`, or `normal_v2`. Each invocation creates a new synthetic run from three valid DEMO orders and one invalid-quantity row. Duplicate injection appends another original DEMO-001 row; missing injection intentionally omits DEMO-002 from downstream publication. No existing records are deleted or changed, and no arbitrary file path or existing run ID is accepted for injection.

| Scenario | Raw | Accepted | Rejected | Duplicate subset | Reporting expected / actual | Incidents |
| --- | --- | --- | --- | --- | --- | --- |
| duplicate_records | 5 | 3 | 2 | 1 | 3 / 3 | DUPLICATE_RECORDS |
| missing_records | 4 | 3 | 1 | 0 | 3 / 2 | MISSING_RECORDS |
| both | 5 | 3 | 2 | 1 | 3 / 2 | Both types |

The existing `POST /pipeline-runs/test` keeps its original eight-row sample and processing counts. It now publishes all three clean identities and records the two duplicate rows as one grouped duplicate incident; it produces no missing incident.

### Inspection API

- `GET /incidents`: returns an `items` list with evidence. Optional `run_id` and `incident_type` filters; `limit` is 1–100 (default 50), `offset` is nonnegative.
- `GET /incidents/{incident_id}`: one persisted evidence snapshot; unknown UUID returns 404, invalid UUID returns 422.
- Vite forwards the same endpoints under `/api`, with backend/frontend ports unchanged at 8002/5174.

Restart the backend to load the new routes if it is already running without reload.

### Functions to review

- `publish_reporting()` in `backend/app/pipeline/reporting.py`: saved upstream expectations and downstream publication.
- `run_fault_scenario()` in `backend/app/pipeline/scenarios.py`: fresh, deterministic synthetic injection.
- `duplicate_evidence()` in `backend/app/incidents/detection.py`: raw validation, duplicate grouping, outcome cross-checks.
- `missing_evidence()` in the same file: expected/actual identity comparison and reconciled evidence counts.
- `detect_incidents()` in the same file: completed-run guard, per-run locking, idempotent evidence persistence.
- `backend/tests/test_incidents.py`: detection, no false missing incidents, identity substitution, evidence, run isolation, transaction rollback, and API checks on temporary SQLite files and isolated `proofline_test` schemas.


## Phase 3B: schema drift and late-arriving data

### Versioned contracts

`backend/app/pipeline/contracts.py` defines immutable, explicitly named contracts:

- `orders.v1`: original five-column input. Its strict header behavior and existing validation/rejections remain unchanged. There is no inferred event time or new schema-drift incident for this compatibility path. Historical runs are not assigned invented schemas or timestamps.
- `orders.v2`: the same five columns plus required `event_timestamp`. Schema and lateness monitoring are enabled explicitly for this version. CSV column order may vary; field names and declared logical types remain fixed.

```csv
order_id,customer_id,quantity,unit_price,order_date,event_timestamp
```

The declared logical types are string, string, integer, finite decimal, calendar date, and ISO 8601 timestamp with an explicit timezone. CSV has text cells rather than physical database types: SCHEMA_DRIFT reports logical type incompatibilities against this declaration. Numeric range/quantity-zero/ID validity rules remain separate business validation; for example, zero is integer-compatible but is still rejected as INVALID_QUANTITY.

Every new run stores a `pipeline_contracts` row containing its schema version, expected-schema snapshot, actual header, extra-column policy, monitoring flag, ingestion timestamp, threshold, and clock source. Expected contracts are never inferred from faulty input, rewritten to make a run pass, or changed on repeat detection. The immutable registry returns fresh snapshots. Old runs without contract metadata retain their original behavior.

Use `execute_pipeline(..., schema_version="orders.v2", column_policy="reject")` from the Python service. The `column_policy` is saved for that run:

- `reject` (default): unexpected columns produce SCHEMA_DRIFT and rows with that header are rejected as INVALID_COLUMNS.
- `allow`: unexpected columns are retained in raw data without a drift incident solely for their presence; clean output uses the defined order fields. Required-column and type checks still apply.

The v2 parser retains faulty headers and every cell, including duplicate column names, uneven rows, extra columns, and exact record/source text. Duplicate headers are stored as `_columns` and `_cells` to prevent dictionary-key loss. Missing or duplicate headers are rejected without fabricating defaults. Empty files or syntactically malformed CSV still raise before a run is created, because their record boundaries cannot be reliably parsed; no source file is modified.

### Schema evidence

`SCHEMA_DRIFT` uses stage `source_to_raw` and stores:

- Versioned expected schema and actual header/observed logical types.
- Exact missing required columns, prohibited unexpected columns, duplicate headers, row-width mismatches, and incompatible values.
- Raw record IDs, record numbers, column names, expected/observed types, and original incompatible values.
- Difference count, source count, and affected-record count.

For this incident type, the shared `affected_count` field means **number of exact differences**, not number of records. This permits a header-only fault to have evidence even when there are zero data records. `counts.affected_record_count` gives the distinct affected-record count. One row can have several differences.

### Time contract and lateness

Each new raw record has a `record_arrivals` entry. For v2, the original event string stays in raw data while the parsed timestamp is normalized to UTC. Both event and arrival timestamps are timezone-aware in Python and PostgreSQL. SQLite tests use a UTC type adapter to preserve the same Python behavior.

A local batch captures its arrival timestamp once at ingestion. Service callers may inject an aware `ingested_at` for deterministic synthetic tests; that clock is explicitly marked `injected_clock`. Normal calls use `system_clock`. A date-only order_date is never treated as an event timestamp. Naive or invalid event timestamps are rejected with INVALID_EVENT_TIMESTAMP and recorded as schema incompatibilities, not late arrivals.

The non-secret setting `LATENESS_THRESHOLD_SECONDS` defaults to 300 and can be configured in `.env` or the environment. The service also accepts an explicit `lateness_seconds` value. Values must be integers from 0 through 31,536,000. Each run saves its effective threshold so later configuration changes cannot reinterpret historical arrivals. Scenario endpoints intentionally pin their threshold to 300 for reproducibility.

The rule is:

```text
expected_deadline = event_timestamp + threshold
late = actual_arrival_timestamp > expected_deadline
```

Equality is on time, including a zero threshold. A future event is not classified as late. Evidence distinguishes event age (arrival minus event) from lateness delay (arrival minus deadline), preserving microsecond precision in the stored timestamps. Only accepted clean identities are eligible: intentionally rejected invalid/duplicate records are excluded, and late accepted records remain in clean/reporting output. Late arrival does not imply a missing record.

`LATE_ARRIVING_DATA` uses stage `source_to_ingestion`. Evidence contains raw/order IDs, row numbers, schema version, clock source, threshold, event timestamp, expected deadline, actual arrival, delay seconds, event-age seconds, and eligible/late/on-time counts. Missing timing evidence for an accepted v2 record raises an integrity error rather than being silently skipped.

### Reproducible API examples

The existing incident GET endpoints and incident-type filter now support all four types. These new scenario names use v2 while the existing v1 scenarios keep their original behavior:

| Scenario | Raw / accepted / rejected | Expected incidents |
| --- | --- | --- |
| schema_drift | 1 / 0 / 1 | SCHEMA_DRIFT: missing customer_id, extra unexpected_note, quantity text two instead of integer (3 differences on 1 record) |
| late_arrival | 3 / 3 / 0 | LATE_ARRIVING_DATA: TIME-001 is 300 seconds beyond its deadline |
| normal_v2 | 2 / 2 / 0 | None |

The simulated timeline is January 1, 2026 in UTC: event 10:00, threshold 300 seconds, deadline 10:05, injected arrival 10:10, event age 600 seconds, late delay 300 seconds. These are labelled synthetic injected times, not live arrival measurements. The normal scenario contains events at 10:06 and 10:10 with arrival at 10:10.

```powershell
$run = Invoke-RestMethod -Method Post http://127.0.0.1:8002/pipeline-runs/scenarios/schema_drift
Invoke-RestMethod "http://127.0.0.1:8002/incidents?run_id=$($run.id)&incident_type=SCHEMA_DRIFT"
$late = Invoke-RestMethod -Method Post http://127.0.0.1:8002/pipeline-runs/scenarios/late_arrival
Invoke-RestMethod "http://127.0.0.1:8002/incidents?run_id=$($late.id)&incident_type=LATE_ARRIVING_DATA"
```

These POSTs create fresh synthetic runs; they never alter earlier runs. The frontend, ports, health endpoint, duplicate rules, reporting expectations, and missing-record detector remain unchanged. Restart a running backend to load the new code.

### Database and migration safety

Migration `0003_schema_lateness` adds only `pipeline_contracts` and `record_arrivals`, and widens existing incident/rejection check constraints for the new types, stages, and INVALID_EVENT_TIMESTAMP reason. It does not rewrite original input, backfill historical event times, or remove existing incidents. PostgreSQL uses timezone-aware columns. Online upgrades were verified on both project databases with pre-existing application-table fingerprints preserved.

Downgrade refuses to remove Phase 3B tables when contract/arrival or new incident evidence would be lost. Offline downgrade is deliberately refused because its live evidence guard cannot be verified. Empty isolated test databases still support migration round trips. Do not attempt destructive downgrades against development data.

The full suite retains the proofline_test-only connection guard, unique-schema PostgreSQL fixtures, and temporary SQLite files. New tests cover exact schema differences, policy behavior, old/new incident coexistence, false-positive exclusions, timezone offsets, persisted one-microsecond boundaries, threshold snapshots, source preservation, API filters, and legacy-row migration preservation. Database fingerprint checks outside pytest confirm that test runs leave development and public test data unchanged.

### Detection functions to review

- `get_contract()` / `validate_contract_order()` — `backend/app/pipeline/contracts.py`.
- `parse_orders_csv()` — `backend/app/pipeline/ingestion.py`.
- `compare_schema()` / `schema_drift_evidence()` — `backend/app/incidents/schema_drift.py`.
- `as_utc()` / `parse_event_timestamp()` / `UTCDateTime` — `backend/app/pipeline/timestamps.py`.
- `arrival_delay()` / `late_arrival_evidence()` — `backend/app/incidents/lateness.py`.
- `persist_raw_orders()` saves the contract and arrival evidence; `detect_incidents()` persists snapshots using the existing API structure.

Limitations: CSV logical types only; v2 must be selected explicitly; one ingestion timestamp per local batch; no streaming watermarks, clock-skew incident, or inference of times for legacy data. No LLM, recovery, replay, new infrastructure, or dashboard redesign is included.


## Phase 4: read-only record lineage and evidence tracing

Phase 4 adds two inspection endpoints without changing the pipeline, schema, or historical rows:

- `GET /pipeline-runs/{run_id}/lineage`
- `GET /incidents/{incident_id}/lineage`

Both use the existing session dependency, UUID validation, 404 behavior, and safe 503 database errors. They are GET-only and are forwarded by Vite under `/api`. The backend and frontend remain on ports 8002 and 5174. Restart the backend after loading the new code if it is running without reload.

### Storage and observations

No migration or new lineage table is needed. Existing run IDs and source foreign keys already connect raw, clean, rejected, manifest, reporting, arrival, contract, and incident records. Alembic remains at `0003_schema_lateness`. The read model joins these rows by run ID and raw record ID; it never re-runs validation, detection, ingestion, or reporting. It never backfills a stage event or changes stored evidence.

The logical path is Raw Orders -> Validation outcome -> Clean Orders -> Reporting. There is no separately persisted validation event or per-record clean/reporting write timestamp in the current model. Lineage therefore identifies validation **outcomes** from committed clean/rejected rows and returns execution timestamps as null. A reporting-stage published_at value is batch metadata, not a per-record arrival time. Existing event/arrival metadata and its system/injected clock label are returned only when recorded.

A failed transaction may have attempted a stage before rolling back. Without committed outcome evidence, lineage reports that stage as unknown; it does not assert the stage never ran.

### Response structure for a future visualization

Responses use `format_version: "1"` and contain:

| Field | Meaning |
| --- | --- |
| scope | Run or incident inspection, always with the authoritative run ID. |
| run | Stored status, counts, start/end timestamps, and safe error code. |
| stage_definitions | The four logical stages, not an execution event list. |
| records | Per-source validation outcome, clean identity, reporting expectation/observation, duplicate relation, timing, last confirmed stage, incident references, and evidence gaps. |
| nodes / edges | Stable IDs scoped by run, explicit node kinds/statuses and relationship evidence kinds. Unknown/absent stage placeholders are explicitly labelled and do not imply execution. |
| incidents | Original detection-time evidence snapshots, unchanged. |
| schema_contract / reporting_stage | Existing contract snapshot and published batch metadata, or null when not recorded. |
| summary | Full-run stored/observed count reconciliation, returned/total record counts, current missing-identity count within the returned records, completeness and integrity status. |
| evidence_gaps / integrity_issues | Missing metadata, unresolved snapshot references, conflicting outcomes, missing manifest entries, and count inconsistencies. |
| interpretations | Explicitly labelled identity-comparison inferences. Root cause remains null. |
| semantics | Limits on the observation, timestamp, validation, placeholder, and root-cause claims. |

Each evidence reference names its table and source key. Incident links include JSON paths into the stored evidence. An incident response returns the affected source records plus directly referenced duplicate source context, rather than every record in the run. Header-level schema drift is run-scoped; a header-only file can return an incident and run-context node with zero record nodes.

### Confirmed facts versus interpretation

- A raw, clean, rejection, manifest, arrival, or reporting row is a current durable observation.
- A validation result is backed by the persisted output/rejection, not a fabricated validation event or freshly re-evaluated rule.
- A rejected record is intentionally excluded with its recorded reason; it is not a missing downstream identity.
- A duplicate pair shows the rejection's actual duplicate_of_raw_id and the target's actual acceptance state. A missing target outcome is labelled unavailable rather than accepted.
- A manifest records an expected `(run_id, raw_order_id, order_id)` identity. It is an expectation, not proof that downstream received it.
- With a published manifest but no matching reporting row, lineage reports the last confirmed stage and absent observation. It cannot distinguish non-delivery, deletion, or another cause without further recorded evidence.
- If the source reached reporting with the wrong business ID, the current reporting observation is retained; the expected identity is separately marked absent.
- Old runs without a reporting marker/manifest are unknown downstream, not automatically missing. A clean row without a saved expectation does not cause lineage to invent that expectation.
- Incident evidence remains a detection-time snapshot. Current records can differ later, so `missing_at_detection` can coexist with a current observed reporting row. The reader neither rewrites nor resolves the incident.
- Cross-run or malformed snapshot references are reported as unresolved; the reader never fetches another run's records to satisfy them.

### Read-only consistency and test isolation

Fresh PostgreSQL API sessions use a REPEATABLE READ, READ ONLY transaction for a consistent inspection snapshot. The reader does not commit. Caller-owned transactions are respected; direct Python callers should supply a dedicated clean read session. Pending ORM writes are refused, and autoflush is disabled during inspection. SQLite uses the same pure read model without PostgreSQL transaction commands.

The existing test guard permits PostgreSQL connections only to `proofline_test`. Tests create unique temporary schemas and remove only those schemas. All lineage mutation scenarios (incorrect downstream identities, missing metadata, conflicting outcomes) occur only in fixture-owned synthetic data. No recovery operation is exposed. Full application-table fingerprints in development and the public test schema were unchanged before/after the full suite and operational lineage inspection.

Tests cover normal, rejected, duplicate, missing, schema-drift, and late records; incomplete/historical/failed runs; snapshot/current differences; cross-run references; deterministic graph IDs; API errors and GET-only behavior; pooled transaction reset; read-only data preservation; and unchanged migration/model metadata. No new migration is performed, and model parity remains checked against `0003_schema_lateness`.

### Verified development example

The existing synthetic run `4414eeb2-d86b-4da3-8cbb-d8da5999212e` has missing-record incident `677f1b54-ac14-426e-a652-e9e718f69e4c`. Its DEMO-002 record is raw ID 18, source row 2. Clean row 8 confirms its accepted validation outcome. The saved manifest expects `(raw_order_id=18, order_id=DEMO-002)`, but the published reporting stage has no matching downstream observation. The last confirmed stage is Clean Orders; no root cause or validation execution timestamp is claimed.

```powershell
Invoke-RestMethod http://127.0.0.1:8002/pipeline-runs/4414eeb2-d86b-4da3-8cbb-d8da5999212e/lineage
Invoke-RestMethod http://127.0.0.1:8002/incidents/677f1b54-ac14-426e-a652-e9e718f69e4c/lineage
```

These IDs are observations from this local development database, not seeded IDs guaranteed in a new installation.

### Functions to review

- `run_lineage()` / `incident_lineage()` / `_record_lineage()` in `backend/app/lineage/service.py`: load scoped observations and explain their availability without replay.
- `incident_references()` in `backend/app/lineage/evidence.py`: map stored snapshot references safely to source records within the run.
- `build_graph()` in `backend/app/lineage/graph.py`: stable nodes/edges with explicit evidence kinds and placeholder semantics.
- `backend/tests/test_lineage.py`: functional, safety, integrity, historical, and read-only regression coverage.

Remaining limits: this is a current-state evidence read model, not an event-sourced audit history; historical attempted stages and unrecorded event times remain unknown. Large-run pagination and visualization are not implemented. Phase 4 adds no LLM, root-cause generator, automated recovery, dashboard redesign, or infrastructure. Phase 5 below adds isolated replay simulation only.


## Phase 5: Safe Replay simulations

Safe Replay is a deterministic preview over an existing, completed run. It never updates the source run, raw rows, clean/rejected outcomes, reporting data, contract, timestamps, or stored incidents. There is no apply endpoint or automatic recovery path.

### Endpoint and allowed operations

`POST /pipeline-runs/{run_id}/replays` accepts exactly:

```json
{
  "operation": "recover_missing_reporting",
  "incident_id": "UUID-of-the-source-MISSING_RECORDS-incident"
}
```

The operation can also be `deduplicate_processing` with a DUPLICATE_RECORDS incident from that same run. UUIDs must be valid. Extra fields (including apply, code, SQL, database URLs, and output paths) and unsupported operation names are rejected with HTTP 422. The service also rejects unsupported direct Python operations explicitly. No arbitrary code, file path, schema selection, or SQL is accepted.

The endpoint returns HTTP 200 for a completed simulation report, whose status is one of:

- `verified`: proposed temporary output changes passed verification.
- `no_change_required`: output was already correct, and the replay verified that fact.
- `verification_failed`: temporary output did not satisfy the safety/correctness checks. No source correction was applied.
- `failed`: temporary setup/transformation/evaluation/cleanup failed. The report contains a safe stage/code, with no raw exception text or partial successful result.

Precondition failures return a safe structured `detail.code` and message, normally HTTP 409; missing runs or incidents outside the selected run return 404. Source database/configuration errors retain the existing safe 503 behavior. Reports never claim that a source incident was resolved.

### Isolation and reproducibility

1. A fresh source session reads one consistent snapshot through the existing lineage repository. PostgreSQL uses REPEATABLE READ, READ ONLY. Active caller transactions or pending writes are refused.
2. Copy the original run-scoped rows to detached Python data and compute their SHA-256 fingerprint. Original CSV text must exactly agree with preserved raw rows and the recorded header.
3. Create a private in-memory SQLite database using only the application's static models. The API cannot choose its URL or location. No source session/connection is passed to transformations.
4. Copy source rows with their original run/raw IDs and relationships. A second raw-only private database independently reuses `persist_order_outcomes()` to verify that recorded validation outcomes match the original input.
5. Perform one of the two allowlisted transformations in temporary data, then reuse existing count reconciliation and all four evidence detectors to evaluate before/after state.
6. Verify protected-input fingerprints, raw identities, validation results, expected identities, uniqueness, counts, other incident evidence, and operation-specific scope. Close all temporary sessions and dispose their engines, including failure paths.
7. Return the comparison report. Nothing is copied back. Cleanup failures are reported rather than asserted to have succeeded.

Stored schema version/definition, extra-column policy, ingestion/event timestamps, and lateness threshold are reused; environment changes and current wall-clock time cannot reinterpret replay input. The report contains no newly generated UUID or execution timestamp that would make identical inputs produce different report content. Internal temporary output surrogate IDs are not business identities and are excluded from comparisons.

### Scenario semantics

`recover_missing_reporting` requires a current MISSING_RECORDS snapshot with absent expected identities and no unexpected/duplicate downstream output. It proposes adding exactly those accepted manifest identities to temporary reporting output. It does not reclassify invalid/rejected records, change validation, remove existing reporting rows, or repair unrelated identity substitutions.

`deduplicate_processing` independently repeats the existing first-valid-occurrence rule against preserved raw input, rebuilds only temporary derived outputs, and corrects duplicated downstream business-ID projections to their validated manifest identities. It preserves which source raw IDs were absent downstream, so it does not silently repair unrelated physical omissions. Unrelated wrong identities are rejected. Source validation outcomes must already match deterministic processing; replay does not guess how to repair inconsistent upstream evidence.

A correctly processed run with duplicate raw records normally returns `no_change_required`: raw duplicate rows and their explicit rejections remain. Even when an incorrect duplicated reporting projection is corrected temporarily, DUPLICATE_RECORDS remains detected because it describes the preserved raw input. The report separately compares duplicate output excess, source duplicate counts, and each simulated incident's presence/affected count.

SCHEMA_DRIFT and LATE_ARRIVING_DATA are not supported target repair operations. Schema-drift input is refused; unrelated lateness evidence is preserved unchanged when replaying eligible missing/duplicate scenarios. Historical runs without saved contracts, timing, or reporting evidence are refused rather than assigned invented defaults. Stale incident snapshots must not be silently treated as current evidence.

### Auditable report

The JSON contains:

- `replay_id`: a deterministic content-derived ID for operation + algorithm version + source snapshot + run/incident references.
- `input_references`: original IDs, source snapshot fingerprint, UTF-8 CSV SHA-256, contract fingerprint, schema version, and lineage integrity summary.
- `stored_source_incidents`: original, unchanged detection-time snapshots.
- `proposed_correction`: bounded simulated changes, winning/duplicate policy, and preserved unrelated omissions.
- `before` / `after`: raw/clean/reporting identities, processing/reporting/duplicate counts, complete validation outcomes, and fresh simulated incident evidence/status.
- `comparison`: added/removed reporting identities, count deltas, validation changes, and simulated incident presence/affected-count changes.
- `verification`: individual checks and their actual aggregate result.
- `isolation`: no-apply policy, private memory storage, and actual temporary-engine creation/disposal counts.
- `report_sha256`: SHA-256 of canonical JSON excluding this digest field. Canonical JSON sorts keys, uses compact separators, UTF-8 characters, and ISO/decimal text for dates/decimals.

The report is returned as JSON, not stored in source database tables. Save it if you need a durable audit artifact. Hashes support integrity/repeatability; they are not signatures or an independently retained audit log. Comparisons refer to the captured snapshot and do not assert that another process could not change source data concurrently later.

Example (creates a new synthetic source run only when you explicitly invoke the first command):

```powershell
$run = Invoke-RestMethod -Method Post http://127.0.0.1:8002/pipeline-runs/scenarios/both
$incidents = Invoke-RestMethod "http://127.0.0.1:8002/incidents?run_id=$($run.id)"
$missing = $incidents.items | Where-Object incident_type -eq MISSING_RECORDS
$body = @{ operation = 'recover_missing_reporting'; incident_id = $missing.id } | ConvertTo-Json
$report = Invoke-RestMethod -Method Post -ContentType application/json -Body $body "http://127.0.0.1:8002/pipeline-runs/$($run.id)/replays"
$report.status
$report.comparison
```

### Verified demonstrations and tests

Demonstrations used two synthetic source runs in a uniquely owned temporary `proofline_test` schema. Replay then operated in private in-memory databases. No development source run was changed or corrected, and the demonstration schema was removed after verification.

| Demonstration | Before | Simulated after | Original source after replay |
| --- | --- | --- | --- |
| Missing downstream record | 5 raw, 3 accepted, 2 rejected; 2 reporting identities; DEMO-002 absent | Same raw/validation; 3 reporting identities; missing count 1 -> 0 | Still 2 reporting identities; original incident preserved |
| Duplicated reporting projection | 5 raw, 3 accepted, 2 rejected; 2 output rows with one excess duplicate business ID | Same raw/validation and output row count; duplicate output excess 1 -> 0; unrelated missing DEMO-002 retained | Original incorrect projection and incident snapshots unchanged |

Both demonstrations were executed twice and produced byte-equivalent canonical reports. Raw duplicate count remained 1, and the raw duplicate incident remained detectable in both before/after states.

Tests cover source/schema/incident consistency, run isolation, PostgreSQL read-only source transactions, no source DML, private database creation/disposal, successful and failing transformations, failed verification, failed cleanup, deterministic repetition/digests, unsupported code/apply/path requests, unchanged original tables, and unchanged Alembic revision. PostgreSQL tests remain restricted to isolated `proofline_test` schemas; SQLite fixture cases also run. Development/public test-table fingerprints are compared outside pytest.

No migration, server report table, new dependency, dashboard change, public deployment, or apply mechanism is added. Alembic remains at `0003_schema_lateness`. Local replay limits are 10,000 records and 5 MB of source CSV. The simulation backend is SQLite and does not certify a future PostgreSQL correction/commit. Phase 5 code is intentionally left uncommitted until separately approved.

### Functions to review

- `capture_input()` — `backend/app/replay/inputs.py`: read-only lineage-backed snapshot and safe input checks.
- `isolated_database()` — `backend/app/replay/sandbox.py`: private storage and guaranteed disposal attempts.
- `validate_replay()` / `transform()` — `backend/app/replay/operations.py`: allowlisted preconditions and temporary corrections.
- `evaluate_state()` — `backend/app/replay/state.py`: reuse pipeline reconciliation and incident detectors.
- `replay_run()` / `verify_replay()` — `backend/app/replay/service.py`: deterministic audit report and verification.
- `backend/tests/test_replay.py`: replay isolation, correctness, failure handling, auditability, and original-data preservation.


## Phase 6: persistent replay audits and evidence-grounded investigation

### Durable replay audit records

Migration `0004_replay_audit` adds `replay_audits` and a supporting `(run_id, id)` unique constraint on incidents for a composite foreign key. It does not change source row contents. Both project databases have been upgraded to this revision with pre-existing rows preserved.

The existing replay POST now saves a minimized audit record **after** simulation. Source reads remain in their original read-only snapshot; audit persistence opens a separate write session/transaction on the configured engine and inserts only into `replay_audits`. An audit write failure cannot apply the simulated correction. The response includes `audit.id`, `audit.created_at`, and `audit.audit_sha256` only after persistence succeeds.

`GET /pipeline-runs/{run_id}/replays/{audit_id}` retrieves the durable, integrity-checked audit representation. Unknown or cross-run audit IDs return 404; invalid ID syntax returns 422. Corrupt or inconsistent audit content is rejected with a safe integrity error. No update/delete API exists.

Stored fields include replay ID, audit ID, source run/incident references, supported operation, input fingerprint, algorithm version, source simulation digest, audit-content digest, UTC creation timestamp, outcome, and structured before/after/verification evidence. The durable projection deliberately omits raw CSV, customer IDs, business-ID values, original free-form incident text, and exception messages. It retains internal source IDs, identity hashes, counts, validation statuses/reasons, protected-input verification, proposal scope, and source-evidence hashes. The full transient simulation response remains available from POST; GET returns its minimized audit representation rather than duplicating raw data.

Logical replay ID identifies input snapshot + operation + algorithm version. Audit ID identifies the logical replay and the particular result digest. Repeated identical requests reuse the same audit ID and timestamp, including concurrent requests. Distinct failure/result outcomes can remain as separate immutable evidence, but a partial unique index permits only one verified/no-change result per logical replay. A conflicting successful result is rejected instead of overwriting history. Composite foreign keys prevent an audit from referencing an incident in another run; a database check forbids applied_to_source=true.

Durable audit creation is an intentional metadata write, not a source correction. Direct `replay_run()` still performs simulation only; the HTTP replay workflow calls `persist_audit()` separately. Audit records are not included in the replay source fingerprint, so writing an audit cannot change the next replay's logical input. Downgrade is refused when durable audit records would be lost. Hashes detect integrity changes; they are not digital signatures or protection against an administrator rewriting data and hashes together.

### Investigation endpoint and stable response

`POST /incidents/{incident_id}/investigate` returns:

- `summary` with supporting evidence IDs.
- `confirmed_observations` with database/lineage references and fixed server-rendered statements.
- `hypotheses` explicitly marked unconfirmed.
- `root_cause` with unknown status and null value unless the evidence model is extended in a later phase; this implementation never promotes a hypothesis to known cause.
- `unknowns` and sanitized `evidence_gaps`.
- `suggested_diagnostic_actions`, always marked not_executed.
- `relevant_replays` from integrity-checked audits for that exact run and incident, labelled as simulations rather than source fixes.
- `generation` identifying deterministic fallback versus validated LLM-assisted selection.
- `evidence_catalog` and a packet fingerprint for tracing every selected fact.

No body, or `{"use_llm": false}`, uses the deterministic local fallback. `{"use_llm": true}` permits a configured provider call; it does not bypass reference/claim validation. Extra fields, code, tools, SQL, or proposed fixes are not accepted by the request model. The endpoint is read-only; investigation output is not persisted and no suggested action is executed.

### Optional official provider adapter

The official `openai` Python SDK is pinned in requirements and lock files. Its Responses API structured-output adapter uses `responses.parse`, a strict Pydantic selection schema, the pinned official API endpoint, store=false, no tools, no automatic retries, and a bounded timeout (default 15 seconds, allowed 1–30). API failures, timeouts, refusals, SDK import/resource failures, and invalid outputs produce a clearly labelled deterministic fallback. Raw provider exceptions and rejected model content are never returned.

To opt in, fill only the ignored project-root `.env` file using the placeholder keys in `.env.example`:

```dotenv
AI_PROVIDER=openai
OPENAI_MODEL=YOUR_ACCOUNT_SUPPORTED_STRUCTURED_OUTPUT_MODEL
OPENAI_API_KEY=YOUR_PRIVATE_API_KEY
AI_TIMEOUT_SECONDS=15
```

Never put a real key in `.env.example` or chat. Choose a Responses/structured-output-capable model available to your account; no paid model is silently selected. Environment values take precedence over `.env`. Restart the backend after configuration changes. OPENAI_BASE_URL is not used; the adapter pins https://api.openai.com/v1. Credentials are never included in evidence packets.

Live provider inference was **not tested** during implementation. Automated requests are mocked and tests disable AI by default. In this execution environment, direct import of the SDK's response resources encountered a Windows Application Control block on the native jiter dependency. The app and public SDK client import successfully, and the provider adapter handles the blocked resource as provider_sdk_unavailable. Windows security settings were not changed. Resolve runtime/package approval compatibility separately before relying on real provider calls; deterministic fallback remains usable.

Official reference: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

### Grounding and privacy contract

The evidence catalog is built from the existing incident-lineage service, committed validation outcomes, recorded snapshot facts, and relevant checked audit records. Stored text is untrusted. Provider packets contain only generated fact IDs and typed numbers/statuses—not raw CSV, customer/business IDs, log contents, arbitrary schema values, or instruction-like strings from incident data. Context is bounded to 25 records and 20 relevant audits; truncated context is explicitly unknown.

The LLM's role is to select/prioritize existing facts and allowlisted hypothesis/action codes. It is not permitted to invent free-form confirmed prose. The server checks all fact references, complete critical observations and mandatory unknowns, allowed hypothesis/action predicates, and their specific supporting evidence. Unknown references, unsupported assertions, mismatched support, omitted unknowns, extra command fields, and attempts to label a hypothesis confirmed are rejected. The response falls back to deterministic output and does not echo the rejected model text.

All displayed statements are rendered from server templates. This constrained design provides enforceable claim grounding; it is deliberately more limited than a free-form AI analyst. `generation.mode=llm_assisted_validated` transparently means LLM selection/prioritization with server-rendered text. `generation.mode=deterministic_fallback` has llm_generated=false and a safe reason. A valid reference alone is not treated as permission for arbitrary prose.

A missing expected identity supports the observation that no matching current reporting row exists. It does not prove deletion, failed delivery, filtering, or any other cause. Those remain unconfirmed possibilities. Clean output supports a committed validation outcome, not an invented validation execution timestamp. A replay that proposes 2 -> 3 reporting identities remains hypothetical when the actual source still has 2.

### Concrete verified DEMO-002 investigation

A fresh, explicitly synthetic development run was added for the integrated demonstration:

- Run: `94aefef6-2c1d-49ec-a9c2-129b55489f6c`.
- Missing incident: `6c4cf732-da43-437c-80df-090e61fcd573`.
- DEMO-002: raw record 29, source row 2, accepted clean record 16.
- The manifest expects raw record 29 downstream; its matching reporting observation is absent. Last confirmed stage: Clean Orders.
- Durable replay audit: `c385007541a6adeee9895ef79addc6419d2b1b7c93643668adf28c04d6b1920f`.
- Replay verification proposed reporting count 2 -> 3. Actual source reporting count remained 2, and every pre-existing row was unchanged.
- Investigation used the deterministic fallback, with no real LLM call. It cited the incident, raw row, clean/validation outcome, manifest, lineage comparison, and audit record. Root cause remained unknown.

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8002/incidents/6c4cf732-da43-437c-80df-090e61fcd573/investigate
Invoke-RestMethod http://127.0.0.1:8002/pipeline-runs/94aefef6-2c1d-49ec-a9c2-129b55489f6c/replays/c385007541a6adeee9895ef79addc6419d2b1b7c93643668adf28c04d6b1920f
```

These are actual local development IDs, not seeded IDs for new installations. Restart an already-running backend to load routes. Ports remain 8002/5174; the React dashboard was not redesigned.

### Phase 6 verification and review map

Tests use mocked provider responses only and retain the proofline_test-only connection guard. Persistence tests cover cross-session retrieval, concurrent deduplication, failed/verified result history, successful-result conflicts, foreign-key/run isolation, no-apply checks, tamper detection, privacy minimization, and downgrade/upgrade safety. Investigation tests cover all four incident types, invented references, unsupported claims, malicious source text, incomplete evidence, provider errors/timeouts/refusals/SDK failures, privacy, and related audit results. Old tests now recognize the new schema head and permit only the intentional audit-table write in the replay HTTP workflow; source preservation checks remain intact.

Modules to review:

- `backend/app/replay/audit.py`: minimized durable projection, separate write transaction, content checks, idempotent retrieval.
- `backend/app/investigation/catalog.py`: evidence catalog, allowed hypotheses/actions, and privacy-minimized context.
- `backend/app/investigation/schemas.py`: strict structured provider-selection contract.
- `backend/app/investigation/provider.py`: optional official SDK adapter and safe failure handling.
- `backend/app/investigation/service.py`: grounding validation, fallback, and structured response rendering.
- `backend/tests/test_replay_audit.py` and `backend/tests/test_investigation.py`: regression and integration coverage.

Remaining limits: no free-form AI assertions, no independently retained investigation history, no signed audit trail, no automatic correction/recovery, and no live-provider success claim. Context truncation and absent historical evidence remain explicit unknowns. Phase 6 is left uncommitted; no push is performed.

## Phase 7: investigation dashboard

The React workspace reads real backend data: paginated pipeline runs, incident filters,
evidence, SVG record lineage, investigations, replay simulations, and saved audit reports.
Metric cards describe the loaded run page. Duplicate counts are a subset of rejected rows.

From `C:\Users\khale\proofline-ai`, start the backend in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8002
```

In a second terminal:

```powershell
Set-Location C:\Users\khale\proofline-ai\frontend
npm.cmd run dev
```

Open http://localhost:5174/. The existing `/api` proxy forwards to port 8002.
Keep database URLs and provider keys backend-only in the ignored local environment files.
No database migration is needed for Phase 7.

Read-only discovery additions:

- `GET /pipeline-runs?status=completed&limit=20&offset=0`: newest-first summaries and total.
- `GET /incidents?sort=newest`: optional ordering; oldest-first remains the default.
- `GET /pipeline-runs/{run_id}/replays?incident_id={incident_id}&limit=20&offset=0`:
  integrity-checked scoped audit metadata and total.

Changing scope clears previous results; obsolete responses are discarded. Loading, errors,
empty responses, incomplete evidence and timeouts have explicit UI states. The lineage
view separates recorded expectations, observations, intentional rejection, missing output,
and unknown stages. It does not invent execution timestamps or root causes.

Investigation requests default to local deterministic summaries, clearly labeled as not LLM
inference. The provider checkbox explicitly requests the optional external provider. Only
validated LLM-assisted responses receive that label. Phase 6 provider configuration and
runtime limitations still apply. Diagnostic suggestions are never executed.

Replay supports only existing missing-reporting and duplicate-processing operations. It
saves an audit but applies no source correction. Review before/simulated-after counts,
validation evidence, temporary incident checks, verification and saved reports. Old runs
without required source contracts may safely reject replay; the rejection is displayed.
Audit responses are checked against both the run and the selected incident.

There is no automatic replay, polling, new detector, or public deployment. Additional runs
are accessible through run-page navigation. The incident and audit lists are paginated.

Frontend checks (from `frontend`):

```powershell
npm.cmd test
npm.cmd run build
npm.cmd run lint
```

Frontend tests mock API transport, including provider responses; they do not make real LLM
calls. Backend PostgreSQL tests remain confined to fixture-owned schemas in `proofline_test`,
with separate SQLite coverage. Run the existing backend pytest suite from the project root.
If the default temporary location is unavailable, use a fresh ignored directory beneath
`work` with pytest's `--basetemp` option. That directory is owned by pytest and must never
contain project data.


## Arabic interface and real UCI retail evaluation

The dashboard now defaults to Arabic/RTL with an optional English language selector.
It separates local synthetic demonstrations, the real 1,000-line UCI evaluation, and
controlled faults injected into isolated copies of those real source lines.

See [the UCI integration and Arabic dashboard guide](docs/uci-retail.md) for official
attribution/license, pinned artifact checksums, line-identity mapping, actual results,
repeatable import commands, controlled ground truth, replay guarantees and limitations.
The UCI integration introduced migrations through `0006_reporting_collision_stage`; upgrades preserve existing data.

## Phase 8 — Data Health, Business Impact and Reliability Lab

Arabic/RTL health KPIs and recorded history now aggregate actual database rows. Incident impact
reports distinguish verified UCI source-line values from unknown financial loss. Reliability Lab
runs deterministic faults on private in-memory copies and saves only an integrity-checked audit
report. Source pipeline records, incidents and existing replay audits remain unchanged.

The current migration is `0007_reliability_lab`. See [the Phase 8 guide](docs/phase8-data-health.md)
for API contracts, metric formulas, isolation guarantees, evidence checks, local commands and limits.
