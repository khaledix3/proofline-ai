import { tr } from "../i18n";
import { useEffect, useRef, useState } from "react";
import {
  assertScope,
  decodeAudit,
  decodeAudits,
  decodeReplay,
  message,
  request,
} from "../api";
import { useResource } from "../hooks";
import type {
  Audit,
  Incident,
  Operation,
  Replay,
  SimulationState,
  Verification,
} from "../types";
import { Badge, Empty, Icon, Loading, Notice } from "../ui";
import { date, human, short } from "../format";
export function Comparison({
  before,
  after,
  verification,
}: {
  before: SimulationState | null;
  after: SimulationState | null;
  verification: Verification;
}) {
  const keys = [
    "source_count",
    "accepted_count",
    "rejected_count",
    "duplicate_count",
    "expected_reporting_count",
    "reported_count",
    "duplicate_output_excess_count",
  ];
  return (
    <>
      <div className="section-heading">
        <h3>{tr("Before / simulated after")}</h3>
        <Badge tone={verification.passed ? "green" : "amber"}>
          {verification.passed ? tr("Verification passed") : tr("Not verified")}
        </Badge>
      </div>
      <div className="table-scroll">
        <table className="comparison">
          <thead>
            <tr>
              <th>{tr("Recorded metric")}</th>
              <th>{tr("Before")}</th>
              <th>{tr("Simulated after")}</th>
              <th>{tr("Delta")}</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => {
              const b = before?.counts[key],
                a = after?.counts[key];
              return (
                <tr key={key}>
                  <td>{human(key)}</td>
                  <td>{b ?? "—"}</td>
                  <td>{a ?? "—"}</td>
                  <td>
                    {a !== undefined && b !== undefined
                      ? `${a - b > 0 ? "+" : ""}${a - b}`
                      : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {(before?.incident_status || after?.incident_status) && (
        <section className="simulation-status">
          <h3>{tr("Incident checks in temporary data")}</h3>
          <p className="micro">
            {tr(
              "These comparisons do not resolve or alter stored source incidents.",
            )}
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{tr("Incident type")}</th>
                  <th>{tr("Before")}</th>
                  <th>{tr("Simulated after")}</th>
                </tr>
              </thead>
              <tbody>
                {Array.from(
                  new Set([
                    ...Object.keys(before?.incident_status ?? {}),
                    ...Object.keys(after?.incident_status ?? {}),
                  ]),
                ).map((key) => (
                  <tr key={key}>
                    <td>{human(key)}</td>
                    {[before, after].map((state, index) => (
                      <td key={index}>
                        {state?.incident_status?.[key]
                          ? state.incident_status[key].detected_in_simulation
                            ? tr("Detected in simulation")
                            : tr("Not detected in simulation")
                          : tr("Not recorded")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <details className="data-details">
        <summary>{tr("Before / after validation evidence")}</summary>
        <pre>
          {JSON.stringify(
            {
              before:
                before?.validation_results ??
                before?.validation ??
                "Not recorded",
              simulated_after:
                after?.validation_results ??
                after?.validation ??
                "Not recorded",
            },
            null,
            2,
          )}
        </pre>
      </details>
      {!after && (
        <Notice>
          {tr(
            "No after-state was produced. This result is not a verified correction.",
          )}
        </Notice>
      )}
      <details className="data-details">
        <summary>{tr("Individual verification checks")}</summary>
        {Object.entries(verification.checks).map(([key, passed]) => (
          <div className="check-row" key={key}>
            <span>{human(key)}</span>
            <Badge tone={passed ? "green" : "red"}>
              {passed ? tr("Passed") : tr("Failed")}
            </Badge>
          </div>
        ))}
      </details>
    </>
  );
}
export function ReplayView({ incident }: { incident: Incident }) {
  const operation: Operation | null =
    incident.incident_type === "MISSING_RECORDS"
      ? "recover_missing_reporting"
      : incident.incident_type === "DUPLICATE_RECORDS"
        ? "deduplicate_processing"
        : null;
  const [replay, setReplay] = useState<Replay | null>(null);
  const [audit, setAudit] = useState<Audit | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [offset, setOffset] = useState(0);
  const [manual, setManual] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const saved = useResource(
    `/pipeline-runs/${incident.run_id}/replays?incident_id=${incident.id}&limit=20&offset=${offset}`,
    decodeAudits,
    revision,
  );
  async function execute(auditId?: string) {
    if (!auditId && !operation) return;
    const current = new AbortController();
    controller.current?.abort();
    controller.current = current;
    setBusy(true);
    setError(null);
    setReplay(null);
    setAudit(null);
    try {
      if (auditId) {
        const data = await request(
          `/pipeline-runs/${incident.run_id}/replays/${encodeURIComponent(auditId)}`,
          decodeAudit,
          { signal: current.signal },
        );
        assertScope(data, incident.run_id, incident.id);
        if (data.report.applied_to_source !== false)
          throw new Error(
            "Unexpected replay safety state. This response will not be displayed.",
          );
        if (!current.signal.aborted) setAudit(data);
      } else {
        const data = await request(
          `/pipeline-runs/${incident.run_id}/replays`,
          decodeReplay,
          {
            method: "POST",
            body: JSON.stringify({ operation, incident_id: incident.id }),
            signal: current.signal,
          },
        );
        assertScope(data.input_references, incident.run_id, incident.id);
        if (data.applied_to_source !== false)
          throw new Error(
            "Unexpected replay safety state. This response will not be displayed.",
          );
        if (!current.signal.aborted) {
          setReplay(data);
          setRevision((value) => value + 1);
        }
      }
    } catch (error) {
      if (!current.signal.aborted) setError(message(error));
    } finally {
      if (!current.signal.aborted) setBusy(false);
    }
  }
  return (
    <>
      <div className="section-heading">
        <div>
          <h3>{tr("Safe Replay")}</h3>
          <p>{tr("Test a proposed correction in isolated temporary data.")}</p>
        </div>
        <Badge tone="blue">{tr("Simulation only")}</Badge>
      </div>
      <div className="safety-banner">
        <Icon name="shield" size={23} />
        <div>
          <strong>{tr("No source correction is applied")}</strong>
          <p>
            {tr(
              "Original raw, clean, reporting, and incident data remain untouched. Only an audit record is saved.",
            )}
          </p>
        </div>
      </div>
      {operation ? (
        <div className="replay-operation">
          <div>
            <span className="eyebrow">{tr("SUPPORTED OPERATION")}</span>
            <h4>
              {operation === "recover_missing_reporting"
                ? tr("Recover missing reporting identities")
                : tr("Verify and correct duplicate processing")}
            </h4>
            <p>
              {operation === "recover_missing_reporting"
                ? tr("Propose only the missing, validated manifest identities.")
                : tr(
                    "Preserve duplicate raw rows and revalidate the first accepted occurrence. Existing correct output may require no change.",
                  )}
            </p>
          </div>
          <button className="primary" disabled={busy} onClick={() => execute()}>
            <Icon name="replay" />
            {busy ? tr("Working\u2026") : tr("Run isolated simulation")}
          </button>
        </div>
      ) : (
        <Notice>
          {tr("Replay is not supported for")}
          {human(incident.incident_type)}
          {tr(
            ". Only missing-record and duplicate-processing simulations are available.",
          )}
        </Notice>
      )}
      {error && <Notice error>{error}</Notice>}
      {busy && (
        <Loading
          text={tr("Reading or simulating isolated replay evidence\u2026")}
        />
      )}
      {replay && (
        <section className="replay-result">
          <div className="result-heading">
            <Badge tone={replay.verification.passed ? "green" : "amber"}>
              {human(replay.status)}
            </Badge>
            <span className="micro">{tr("Source correction applied: No")}</span>
          </div>
          <Comparison
            before={replay.before}
            after={replay.after}
            verification={replay.verification}
          />
          {replay.comparison && (
            <details className="data-details">
              <summary>
                {tr("Changed reporting identities (temporary data)")}
              </summary>
              <pre>
                {JSON.stringify(
                  {
                    added: replay.comparison.added_reporting_identities,
                    removed: replay.comparison.removed_reporting_identities,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          )}
          {replay.failure && (
            <Notice error>
              {human(replay.failure.code)} · {human(replay.failure.stage)}
            </Notice>
          )}
          <div className="audit-receipt">
            <Icon name="check" />
            <div>
              <strong>{tr("Durable audit saved")}</strong>
              <code>{replay.audit.id}</code>
              <span className="micro">{date(replay.audit.created_at)}</span>
            </div>
            <button onClick={() => execute(replay.audit.id)} disabled={busy}>
              {tr("Open saved audit")}
            </button>
          </div>
        </section>
      )}
      <section className="saved-audits">
        <div className="section-heading">
          <h3>{tr("Saved audit reports")}</h3>
          <span className="count">{saved.data?.total ?? "—"}</span>
        </div>
        {saved.loading && <Loading text={tr("Loading saved audits\u2026")} />}
        {saved.error && <Notice error>{saved.error}</Notice>}
        {saved.data?.items.length === 0 && (
          <Empty title={tr("No saved audits for this incident")}>
            {tr(
              "A completed replay request records an audit. No simulation is run automatically.",
            )}
          </Empty>
        )}
        {saved.data && saved.data.items.length > 0 && (
          <>
            <label className="field">
              {tr("Saved replay audit")}
              <select
                value={audit?.id ?? ""}
                onChange={(event) => {
                  if (event.target.value) void execute(event.target.value);
                }}
                disabled={busy}
              >
                <option value="">{tr("Select a saved audit")}</option>
                {saved.data.items.map((row) => (
                  <option key={row.id} value={row.id}>
                    {short(row.id)} · {human(row.outcome)} ·{" "}
                    {date(row.created_at)}
                  </option>
                ))}
              </select>
            </label>
            <div className="pager">
              <button
                disabled={!offset || busy}
                onClick={() => setOffset(offset - 20)}
              >
                {tr("Previous audits")}
              </button>
              <span>
                {offset + 1}–{offset + saved.data.items.length}
                {tr("of")} {saved.data.total}
              </span>
              <button
                disabled={offset + 20 >= (saved.data.total ?? 0) || busy}
                onClick={() => setOffset(offset + 20)}
              >
                {tr("Next audits")}
              </button>
            </div>
          </>
        )}
        <form
          className="audit-search"
          onSubmit={(event) => {
            event.preventDefault();
            if (/^[0-9a-f]{64}$/.test(manual)) void execute(manual);
          }}
        >
          <label className="field">
            {tr("Retrieve audit by ID")}
            <input
              value={manual}
              onChange={(event) => setManual(event.target.value.trim())}
              placeholder={tr("64-character audit ID")}
              autoComplete="off"
            />
          </label>
          <button disabled={busy || !/^[0-9a-f]{64}$/.test(manual)}>
            {tr("Retrieve audit")}
          </button>
        </form>
      </section>
      {audit && (
        <section className="audit-detail">
          <div className="section-heading">
            <h3>
              {tr("Saved audit \u00B7")}
              {short(audit.id)}
            </h3>
            <Badge tone="neutral">{tr("Persisted evidence")}</Badge>
          </div>
          <Notice>
            {tr("Saved simulation outcome:")}
            {human(audit.report.outcome)}
            {tr(". No source correction applied.")}
          </Notice>
          <p className="micro">
            {date(audit.created_at)} · {human(audit.report.operation)}
          </p>
          <Comparison
            before={audit.report.before}
            after={audit.report.after}
            verification={audit.report.verification}
          />
          <details className="data-details">
            <summary>
              {tr("Minimized audit evidence & integrity digest")}
            </summary>
            <pre>{JSON.stringify(audit, null, 2)}</pre>
          </details>
        </section>
      )}
    </>
  );
}
