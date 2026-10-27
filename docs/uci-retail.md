# Arabic dashboard and UCI Online Retail evaluation

## Source and attribution

- Official record: https://archive.ics.uci.edu/dataset/352/online+retail (UCI ID 352).
- Exact acquired download: https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip
- Citation: Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5BW33.
- License: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/. Attribution and this mapping/change description must accompany redistribution.
- Original workbook SHA-256: `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d`.
- Downloaded ZIP SHA-256: `f5385cbb54bbebf7196389109c6b0621faab0c304e3702548165e71c84aede8b`.
- Selected subset SHA-256: `a9f04f2e1f06958317154b447edf7286e425942ca8715c457058e6307cd96bae`.
- Acquired artifacts stay in ignored `work/uci352/`; they are not bundled into React or Git.

## Scope and source identity

Exactly 1,000 distinct worksheet data rows were selected deterministically, evenly spaced across the workbook, including first/last. This offers broad coverage, not a random or statistically representative sample. The whole source workbook must be downloaded/read to select across it; only the subset is imported. Four clearly marked controlled runs each use a separate copy of these same 1,000 source lines. These are not 5,000 distinct original transactions.

InvoiceNo is not the unique pipeline key. Multiple products may legitimately share an invoice number. Exact repeated row content is only an observation for review, not sufficient evidence of a repeated pipeline record. The adapter preserves each physical worksheet line with a stable source identity.

## Mapping version `uci352.invoice-line.v1`

| Original field | Pipeline use | Preserved source meaning |
|---|---|---|
| Workbook hash + worksheet row | `order_id = UCI352_<hash-prefix>_<row>` | Technical source-line identity, not an invented business order number |
| InvoiceNo, StockCode | Retained in original source values | Invoice/product identifiers; InvoiceNo is not used for deduplication |
| CustomerID | Stable derived SHA-256 reference in `customer_id`, or blank | No substitute customer is invented when missing |
| Quantity | Exact numeric value rendered as text for validation | No absolute value, clipping or rounding |
| UnitPrice | Exact parsed numeric value rendered as decimal text | No rounding to make a row pass; source unit is sterling |
| InvoiceDate | Calendar date in `order_date` | Full original naive datetime retained; no timezone inferred |
| Description, Country | Retained in original source values | Not fabricated or used as validation substitutes |

The untouched workbook bytes are retained. `dataset_source_records.original_values` preserves typed cell values, with Excel datetimes serialized as naive ISO strings. It is separate from mapped `raw_orders`. `pipeline_runs.source_csv` retains the mapped/injected pipeline input, not a claim to be the original workbook. Original customer references stay in the database/source artifact; there is no new HTTP endpoint exposing them. Derived hashes reduce casual disclosure but are not encryption or a guarantee of anonymity.

Original InvoiceDate is not an ingestion timestamp. The real evaluation uses `orders.v1`, records the actual import capture time, and has no event-time lateness monitoring. The source adapter verifies its eight header fields, but no claim is made that legacy v1 incident monitoring independently monitors the original workbook schema.

## Experiment A: actual source evaluation

Observed counts: 1,000 raw = 743 accepted + 257 rejected; zero raw duplicate rejections; zero pipeline incidents.

| Mutually exclusive primary source category | Source | Accepted | Rejected |
|---|---:|---:|---:|
| other_transaction_line | 742 | 742 | 0 |
| missing_customer_reference | 234 | 0 | 234 |
| cancellation | 22 | 0 | 22 |
| zero_price | 1 | 1 | 0 |
| negative_quantity_return_or_adjustment_candidate | 1 | 0 | 1 |

Overlapping source observations: 236 missing CustomerID values, 2 missing Description values, 5 excess sampled lines sharing an invoice number, and 0 exact-content repeat candidates. The primary missing-customer category is 234 because two other missing-customer lines are classified first as cancellations. Validation reports the first error: 236 `INVALID_CUSTOMER_ID` and 21 `INVALID_QUANTITY` rejections.

C-prefix invoices are cancellation-coded according to UCI documentation. Negative non-C quantities are return/adjustment candidates; their precise business explanation is not established. The existing positive-quantity contract excludes them rather than reinterpreting them as fraud/corruption. The one zero-price line passes the existing nonnegative-price rule. Missing descriptions are source completeness observations, not automatically pipeline incidents.

## Experiment B: controlled ground truth

| Fault | Accepted / rejected | Expected and observed incidents |
|---|---|---|
| Missing reporting row | 743 / 257 | 1 missing identity |
| Duplicate reporting identity | 743 / 257 | 1 excess output collision and 1 missing expected identity |
| Schema drift | 0 / 1000 | 3 differences: missing customer_id, unexpected controlled_extra, one incompatible quantity |
| Late arrival | 743 / 257 | 1 late record, 60 seconds beyond a 300-second deadline |

The output-collision experiment substitutes one reporting identity with another accepted source identity. It does not create a repeated InvoiceNo or modify source records. The new duplicate detector preserves old raw-duplicate rules while reporting collisions under `output_groups` and `output_stage`. New output-only incidents use the reporting stage. Earlier snapshots using the legacy category stage plus explicit output_stage remain unchanged and are handled compatibly.

For schema/late experiments, event and arrival clocks are entirely simulated UTC values unrelated to the historical InvoiceDate. Ordinary eligible rows arrive exactly at the 300-second threshold (on time); one controlled row arrives at age 360 seconds, 60 seconds beyond its deadline. No real historical delay is inferred.

All four cases matched expected incident types and counts, with no extra incident types. This is four deterministic checks, not a statistical accuracy claim about arbitrary production data.

## Lineage, investigation and replay

Missing-source example: run `0879cf00-ba8c-41e7-a1f7-9d490d868f8e`, source identity `UCI352_43465a06f2cc_2`, raw record 1033. Its last confirmed stage is clean; the reporting expectation is present and the observation is absent.

Missing replay verifies reporting 742 -> 743 only in private in-memory SQLite. Duplicate replay verifies output excess collisions 1 -> 0, restoring the overwritten expected identity. Raw source values, clean outcomes, manifests, provenance, and original incident snapshots stay unchanged. Audits alone are added through a separate writer. Source snapshots were compared before/after each replay.

`safe-replay.v2` versions the extended reporting-collision verification and provenance fingerprinting. Existing v1 audit reports remain retrievable. A new algorithm version may produce a distinct audit; identical requests within the same algorithm/input/result reuse an audit. Hashes are integrity fingerprints, not digital signatures.

Investigations used the live deterministic fallback (`use_llm=false`), with evidence references and unknown root cause. No real LLM call was made. The controlled experiment metadata supplies known injection ground truth; the generic investigation does not claim independent proof of the causal mechanism.

## Arabic interface

Arabic is the default on first use; the language selector remembers Arabic/English locally. Navigation, controls, status/rejection labels, empty/loading/error states, evidence presentation, bounded investigation statements and replay descriptions are localized. Technical field names, identifiers, raw JSON and integrity digests remain unchanged. Dates explicitly use UTC and Latin digits. The RTL layout mirrors navigation/tables/cards; the graph geometry deliberately stays left-to-right source-to-destination, with Arabic labels and an accessible observations table.

The UI includes source filters for local synthetic demonstrations, real UCI data, and controlled faults on UCI records. Inspect source attribution/categories in the run overview using Source details. Investigations and replay remain manual actions; no source correction is applied.

## Database and isolation

`0005_dataset_provenance` adds `dataset_imports` and `dataset_source_records`. `0006_reporting_collision_stage` extends only the incident stage constraint. All pre-existing application row hashes were preserved during upgrades in proofline_dev and proofline_test. Downgrades fail closed when provenance, audits or incompatible reporting-stage incidents would be endangered.

Import keys include the pinned workbook, selected row content, mapping version and experiment. A per-key PostgreSQL advisory lock and database uniqueness constraint prevent unintended repeated imports. Original source values are committed before processing; failures retain raw/provenance data and a failed run. An existing unfinished/failed import is explicitly refused rather than implicitly duplicated.

Tests keep their global PostgreSQL guard and fixture-owned proofline_test schemas; SQLite unit/integration copies remain isolated. Existing development rows are never reset. Real experiments were new, separately classified development runs. Source and database credentials remain backend-only.

## Run and demonstrate

Backend from project root:
```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8002
```
Frontend in a second terminal:
```powershell
Set-Location C:\Users\khale\proofline-ai\frontend
npm.cmd run dev
```
Open http://localhost:5174/. Select the real UCI source category and Runs to review the 1,000-row evaluation. Select controlled data and inspect the missing-reporting run for lineage, investigation and saved replay audits.

Reproduce an import with the official pinned workbook (reuse is safe):
```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m app.datasets.cli --download --experiment real
.\.venv\Scripts\python.exe -m app.datasets.cli --experiment missing_reporting
```
The CLI is restricted to proofline_dev; automated tests call the service with fixture-owned engines. Downloading is only performed with --download if the pinned workbook is absent. A changed local/workbook download is refused rather than overwritten or silently trusted.

Verification commands:
```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=work/pytest-uci-review-fresh
.\.venv\Scripts\python.exe -m compileall -q backend/app backend/tests
npm.cmd test --prefix frontend
npm.cmd run build --prefix frontend
npm.cmd run lint --prefix frontend
```
Choose a fresh ignored --basetemp directory; pytest owns that temporary directory. Never target a project-data folder.

## Limitations

- Only the selected 1,000 physical lines were evaluated. No dataset-wide quality/fraud claim is supported.
- The existing positive-quantity/customer-required contract is not a complete retail returns/cancellation/accounting model.
- Original timezone and historical ingestion times are unavailable. Real lateness cannot be measured.
- Customer reference hashing is not secure anonymization; original values are kept privately for provenance.
- Raw evidence JSON, source field names, filenames and API identifiers intentionally remain in their original form. Unknown future backend messages retain clearly labeled original technical details rather than an invented translation.
- No real provider call was tested; the deterministic fallback and mocked provider tests remain the verified paths.
- Existing Starlette/httpx deprecation warning remains; no Windows security settings were changed.
- No commit or push was performed.
