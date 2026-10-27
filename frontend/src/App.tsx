import {HealthCenter} from "./components/HealthCenter";
import {ReliabilityLab} from "./components/ReliabilityLab";
import {BusinessImpactView} from "./components/BusinessImpactView";
import { SourceView } from "./components/SourceView";
import { tr, getLanguage, setLanguage, type Language } from "./i18n";
import { useState } from "react";
import {
  decodeHealth,
  decodeIncidents,
  decodeLineage,
  decodeRuns,
} from "./api";
import { useResource } from "./hooks";
import type { Incident, IncidentType, Run } from "./types";
import { Badge, Empty, Icon, Loading, Notice } from "./ui";
import { date, human, labels, short } from "./format";
import { LineageView } from "./components/LineageView";
import { InvestigationView } from "./components/InvestigationView";
import { ReplayView } from "./components/ReplayView";
import { EvidenceView } from "./components/EvidenceView";
import "./App.css";
type Tab = "Evidence" | "Lineage" | "Investigation" | "Safe Replay" | "Business impact";
function Workspace({
  incident,
  startTab,
}: {
  incident: Incident;
  startTab: Tab;
}) {
  const [tab, setTab] = useState<Tab>(startTab);
  const [wholeRun, setWholeRun] = useState(false);
  const trace = useResource(`/incidents/${incident.id}/lineage`, decodeLineage);
  const runTrace = useResource(
    wholeRun ? `/pipeline-runs/${incident.run_id}/lineage` : null,
    decodeLineage,
  );
  const selected = wholeRun ? runTrace : trace;
  const scoped =
    selected.data &&
    selected.data.scope.run_id === incident.run_id &&
    (wholeRun || selected.data.scope.incident_id === incident.id);
  return (
    <section className="workspace">
      <header className="case-header">
        <div>
          <span className="eyebrow">
            {tr("RECORDED INCIDENT \u00B7")}
            {short(incident.id)}
          </span>
          <h2>{labels[incident.incident_type]}</h2>
          <p>
            {tr("Run")}
            <code>{short(incident.run_id)}</code> · {date(incident.detected_at)}
          </p>
        </div>
        <Badge tone="amber">
          {incident.affected_count}
          {tr("affected")}
        </Badge>
      </header>
      <div
        className="tabs"
        role="tablist"
        aria-label={tr("Incident inspection")}
      >
        {(["Evidence", "Lineage", "Investigation", "Safe Replay", "Business impact"] as Tab[]).map(
          (item) => (
            <button
              key={tr(item)}
              role="tab"
              aria-selected={tab === item}
              onClick={() => setTab(item)}
            >
              {tr(item)}
            </button>
          ),
        )}
      </div>
      <div className="tab-content" role="tabpanel">
{tab === "Business impact" && <BusinessImpactView incident={incident} lineage={()=>setTab("Lineage")}/>}
        {trace.data && trace.data.scope.run_id === incident.run_id && (
          <SourceView source={trace.data.run.source} />
        )}
        {tab === "Evidence" && (
          <>
            <EvidenceView incident={incident} />
            {trace.loading && <Loading />}
            {trace.error && <Notice error>{trace.error}</Notice>}
            {trace.data &&
              trace.data.scope.run_id === incident.run_id &&
              trace.data.scope.incident_id === incident.id && (
                <>
                  <p>
                    {tr("Evidence completeness:")}{" "}
                    <strong>
                      {human(trace.data.summary.evidence_completeness)}
                    </strong>
                  </p>
                  {trace.data.evidence_gaps.map((gap, index) => (
                    <Notice key={index}>
                      {human(gap.code)}
                      {gap.raw_order_id !== undefined
                        ? ` · raw #${gap.raw_order_id}`
                        : ""}
                    </Notice>
                  ))}
                  <button onClick={() => setTab("Lineage")}>
                    {tr("Trace recorded stages")}
                    <Icon name="arrow" />
                  </button>
                </>
              )}
          </>
        )}
        {tab === "Lineage" && (
          <>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={wholeRun}
                onChange={(event) => setWholeRun(event.target.checked)}
              />
              {tr("Include all records in this run")}
            </label>
            {selected.loading && <Loading />}
            {selected.error && <Notice error>{selected.error}</Notice>}
            {selected.data && !scoped && (
              <Notice error>
                {tr("Lineage response belongs to a different scope.")}
              </Notice>
            )}
            {scoped && selected.data && (
              <LineageView
                key={
                  wholeRun
                    ? `run:${incident.run_id}`
                    : `incident:${incident.id}`
                }
                trace={selected.data}
              />
            )}
          </>
        )}
        {tab === "Investigation" && (
          <InvestigationView
            incident={incident}
            onAudit={() => setTab("Safe Replay")}
          />
        )}
        {tab === "Safe Replay" && <ReplayView incident={incident} />}
      </div>
    </section>
  );
}
function IncidentBrowser({
  runId,
  type,
  sourceKind,
  revision,
  startTab,
}: {
  runId: string;
  sourceKind: string;
  type: string;
  revision: number;
  startTab: Tab;
}) {
  const [offset, setOffset] = useState(0);
  const [selection, setSelection] = useState<string | null>(null);
  const query = new URLSearchParams({
    limit: "20",
    offset: String(offset),
    sort: "newest",
  });
  if (sourceKind) query.set("source_kind", sourceKind);
  if (runId) query.set("run_id", runId);
  if (type) query.set("incident_type", type);
  const rows = useResource(`/incidents?${query}`, decodeIncidents, revision);
  const validRows = rows.data?.items.filter(
    (item) =>
      (!runId || item.run_id === runId) &&
      (!type || item.incident_type === type),
  );
  const selected =
    validRows?.find((item) => item.id === selection) ?? validRows?.[0];
  return (
    <div className="incident-layout">
      <section className="incident-list">
        <div className="list-title">
          <h2>{tr("Recorded incidents")}</h2>
          <span className="count">{validRows?.length ?? "—"}</span>
        </div>
        {rows.loading && <Loading text={tr("Loading incidents\u2026")} />}
        {rows.error && <Notice error>{rows.error}</Notice>}
        {validRows?.length === 0 && (
          <Empty title={tr("No incidents in this scope")}>
            {tr("Choose another run or incident type.")}
          </Empty>
        )}
        {validRows?.map((item) => (
          <button
            className={`incident-card ${selected?.id === item.id ? "selected" : ""}`}
            key={item.id}
            onClick={() => setSelection(item.id)}
            aria-pressed={selected?.id === item.id}
          >
            <div>
              <span className={`incident-symbol ${item.incident_type}`}>
                <Icon name="incident" />
              </span>
              <Badge>
                {item.affected_count}
                {tr("affected")}
              </Badge>
            </div>
            <strong>{labels[item.incident_type]}</strong>
            {item.source_kind && <Badge>{human(item.source_kind)}</Badge>}
            <span>
              {tr("Run")}
              {short(item.run_id)} ·{" "}
              {human(
                typeof item.evidence.output_stage === "string"
                  ? item.evidence.output_stage
                  : item.stage,
              )}
            </span>
            <small>{date(item.detected_at)}</small>
          </button>
        ))}
        {rows.data && (
          <div className="pager">
            <button
              disabled={!offset}
              onClick={() => {
                setOffset(offset - 20);
                setSelection(null);
              }}
            >
              {tr("Previous")}
            </button>
            <span>
              {tr("Page")}
              {offset / 20 + 1}
            </span>
            <button
              disabled={rows.data.items.length < 20}
              onClick={() => {
                setOffset(offset + 20);
                setSelection(null);
              }}
            >
              {tr("Next")}
            </button>
          </div>
        )}
      </section>
      {selected ? (
        <Workspace
          key={`${selected.id}:${revision}:${startTab}`}
          incident={selected}
          startTab={startTab}
        />
      ) : (
        <section className="workspace">
          <Empty title={tr("Select a recorded incident")}>
            {tr(
              "Evidence, lineage, investigation, and replay stay scoped to your selected incident.",
            )}
          </Empty>
        </section>
      )}
    </div>
  );
}
function Runs({
  rows,
  total,
  inspect,
}: {
  rows: Run[];
  total?: number;
  inspect: (id: string) => void;
}) {
  const [sourceRun, setSourceRun] = useState<string | null>(null);
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>{tr("Pipeline runs")}</h2>
          <p>
            {tr("Execution status and recorded counts \u00B7 newest first")}
          </p>
        </div>
        <Badge>
          {total ?? rows.length}
          {tr("runs")}
        </Badge>
      </div>
      {!rows.length ? (
        <Empty title={tr("No pipeline runs yet")}>
          {tr("Run your existing local pipeline to begin collecting evidence.")}
        </Empty>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{tr("Run / source")}</th>
                <th>{tr("Execution")}</th>
                <th>{tr("Raw")}</th>
                <th>{tr("Accepted")}</th>
                <th>{tr("Rejected")}</th>
                <th>{tr("Raw duplicates")}</th>
                <th>{tr("Reconciliation")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((run) => (
                <tr key={run.id}>
                  <td>
                    <strong>
                      <bdi dir="ltr">{run.source_name}</bdi>
                    </strong>
                    {run.source && (
                      <span className="source-badge">
                        <Badge>{human(run.source.kind)}</Badge>
                      </span>
                    )}
                    <small>
                      {short(run.id)} · {date(run.started_at)}
                    </small>
                  </td>
                  <td>
                    <Badge
                      tone={
                        run.status === "completed"
                          ? "green"
                          : run.status === "failed"
                            ? "red"
                            : "blue"
                      }
                    >
                      {human(run.status)}
                    </Badge>
                    {run.error_code && <small>{human(run.error_code)}</small>}
                  </td>
                  {[
                    "source_count",
                    "accepted_count",
                    "rejected_count",
                    "duplicate_count",
                  ].map((key) => (
                    <td key={key}>{run.counts[key]}</td>
                  ))}
                  <td>
                    {run.status === "completed" ? (
                      <Badge
                        tone={
                          run.counts.source_count ===
                          run.counts.accepted_count + run.counts.rejected_count
                            ? "green"
                            : "red"
                        }
                      >
                        {run.counts.source_count ===
                        run.counts.accepted_count + run.counts.rejected_count
                          ? tr("Reconciled")
                          : tr("Mismatch")}
                      </Badge>
                    ) : (
                      "Pending"
                    )}
                  </td>
                  <td>
                    <button
                      onClick={() =>
                        setSourceRun(sourceRun === run.id ? null : run.id)
                      }
                    >
                      {tr("Source details")}
                    </button>
                    <button onClick={() => inspect(run.id)}>
                      {tr("Inspect")}
                      <Icon name="arrow" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {sourceRun && (
        <SourceView source={rows.find((run) => run.id === sourceRun)?.source} />
      )}
      <p className="micro">
        {tr(
          "Duplicate count is a subset of rejected records. Completed indicates execution completion, not absence of incidents.",
        )}
      </p>
    </section>
  );
}
export default function App() {
  const [language, updateLanguage] = useState<Language>(getLanguage());
  const [view, setView] = useState("Incidents");
  const [runId, setRunId] = useState("");
  const [type, setType] = useState("");
  const [sourceKind, setSourceKind] = useState("");
  const [revision, setRevision] = useState(0);
  const [offset, setOffset] = useState(0);
  const health = useResource("/health", decodeHealth, revision);
  const runs = useResource(
    `/pipeline-runs?limit=20&offset=${offset}${sourceKind ? `&source_kind=${sourceKind}` : ""}`,
    decodeRuns,
    revision,
  );
  const inspect = (id: string) => {
    setRunId(id);
    setSourceKind("");
    setType("");
    setView("Incidents");
  };
  return (
    <div className="app-shell" dir={language === "ar" ? "rtl" : "ltr"}>
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            setView("Incidents");
          }}
        >
          <span className="brand-mark">
            <Icon name="trace" size={24} />
          </span>
          <span>
            ProofLine <b>AI</b>
            <small>{tr("DATA RELIABILITY WORKSPACE")}</small>
          </span>
        </a>
        <span className="nav-label">{tr("WORKSPACE")}</span>
        <nav aria-label={tr("Main navigation")}>
          {[
            ["Runs", "grid"],
            ["Incidents", "incident"],
            ["Saved audits", "replay"],
["Data Health", "grid"],
["Reliability Lab", "shield"],
          ].map(([label, icon]) => (
            <button
              className={view === label ? "active" : ""}
              key={tr(label)}
              onClick={() => setView(label)}
            >
              <Icon name={icon} />
              {tr(label)}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <Icon name="shield" />
          <strong>{tr("Evidence before action")}</strong>
          <p>{tr("Inspect real observations. Simulate corrections safely.")}</p>
          <span className="local-dot" />
          {tr("Local workspace")}
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            {tr("Workspace")}
            <span className="muted">/ {tr(view)}</span>
          </span>
          <div className="top-actions">
            <label className="language-switch">
              {tr("Language")}
              <select
                aria-label={tr("Language")}
                value={language}
                onChange={(event) => {
                  const value = event.target.value as Language;
                  setLanguage(value);
                  updateLanguage(value);
                  setRevision((current) => current + 1);
                }}
              >
                <option value="ar">العربية</option>
                <option value="en">English</option>
              </select>
            </label>
            <Badge
              tone={health.data ? "green" : health.error ? "red" : "neutral"}
            >
              {health.data
                ? tr("Backend connected")
                : health.error
                  ? tr("Backend unavailable")
                  : tr("Connecting\u2026")}
            </Badge>
            <button
              aria-label={tr("Refresh backend data")}
              onClick={() => setRevision(revision + 1)}
            >
              <Icon name="refresh" />
              {tr("Refresh")}
            </button>
          </div>
        </header>
        <div className="page">
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                {tr("PROOFLINE AI / OBSERVE \u00B7 TRACE \u00B7 VERIFY")}
              </span>
              <h1>
                {view === "Data Health" || view === "Reliability Lab" ? tr(view) : view === "Runs"
                  ? tr("Pipeline overview")
                  : tr("Investigation workspace")}
              </h1>
              <p>
                {tr(
                  "Follow the evidence from source records to verified simulations.",
                )}
              </p>
            </div>
            <span className="environment">{tr("LOCAL / READ & SIMULATE")}</span>
          </div>
          {health.error && (
            <Notice error>
              {tr(
                "Backend connection failed. Start FastAPI on 127.0.0.1:8002 and refresh.",
              )}
            </Notice>
          )}
          {view !== "Reliability Lab" && <div className="filters source-filters">
            <label className="field">
              {tr("Source category")}
              <select
                value={sourceKind}
                onChange={(event) => {
                  setSourceKind(event.target.value);
                  setRunId("");
                  setOffset(0);
                }}
              >
                <option value="">{tr("All sources")}</option>
                <option value="local_demo">
                  {tr("Local synthetic demonstrations")}
                </option>
                <option value="uci_real">{tr("Real UCI data")}</option>
                <option value="uci_controlled">
                  {tr("Controlled faults on real data")}
                </option>
              </select>
            </label>
          </div>
          }
          {view !== "Data Health" && view !== "Reliability Lab" && <div className="metrics">
            {[
              ["Source records", "source_count"],
              ["Accepted", "accepted_count"],
              ["Rejected", "rejected_count"],
              ["Raw duplicate rejections", "duplicate_count"],
            ].map(([label, key]) => (
              <div className="metric" key={key}>
                <span>{tr(label)}</span>
                <strong>
                  {runs.data
                    ? runs.data.items
                        .reduce((sum, run) => sum + run.counts[key], 0)
                        .toLocaleString()
                    : "—"}
                </strong>
                <small>
                  {tr("Loaded run page \u00B7")}
                  {runs.data?.items.length ?? "—"}
                  {tr("runs")}
                </small>
              </div>
            ))}
          </div>
          }
          {runs.loading && <Loading text={tr("Loading pipeline runs\u2026")} />}
          {runs.error && <Notice error>{runs.error}</Notice>}
          {view === "Data Health" ? <HealthCenter key={sourceKind} sourceKind={sourceKind} revision={revision} inspect={inspect}/> : view === "Reliability Lab" ? <ReliabilityLab revision={revision} inspect={inspect}/> : view === "Runs" ? (
            <>
              {runs.data && (
                <Runs
                  rows={runs.data.items}
                  total={runs.data.total}
                  inspect={inspect}
                />
              )}
              <div className="pager">
                <button
                  disabled={!offset || runs.loading}
                  onClick={() => setOffset(offset - 20)}
                >
                  {tr("Previous runs")}
                </button>
                <span>
                  {tr("Page")}
                  {offset / 20 + 1}
                </span>
                <button
                  disabled={
                    runs.loading ||
                    !runs.data ||
                    offset + 20 >= (runs.data.total ?? 0)
                  }
                  onClick={() => setOffset(offset + 20)}
                >
                  {tr("Next runs")}
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="filters">
                <label className="field">
                  {tr("Pipeline run")}
                  <select
                    value={runId}
                    onChange={(event) => setRunId(event.target.value)}
                  >
                    <option value="">{tr("All runs")}</option>
                    {runId &&
                      !runs.data?.items.some((run) => run.id === runId) && (
                        <option value={runId}>{short(runId)}</option>
                      )}
                    {runs.data?.items.map((run) => (
                      <option value={run.id} key={run.id}>
                        {short(run.id)} · {run.source_name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  {tr("Incident type")}
                  <select
                    value={type}
                    onChange={(event) => setType(event.target.value)}
                  >
                    <option value="">{tr("All incident types")}</option>
                    {(Object.keys(labels) as IncidentType[]).map((key) => (
                      <option key={key} value={key}>
                        {labels[key]}
                      </option>
                    ))}
                  </select>
                </label>
                <p>
                  {tr("Each inspection stays scoped to one incident and run.")}
                </p>
              </div>
              <IncidentBrowser
                key={`${runId}:${type}:${view}:${sourceKind}`}
                sourceKind={sourceKind}
                runId={runId}
                type={type}
                revision={revision}
                startTab={view === "Saved audits" ? "Safe Replay" : "Evidence"}
              />
            </>
          )}
          <footer className="page-footer">
            {tr(
              "ProofLine AI \u00B7 Evidence-grounded investigation \u00B7 No automatic recovery",
            )}
          </footer>
        </div>
      </main>
    </div>
  );
}
