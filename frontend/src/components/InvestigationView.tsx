import { tr, translateStatement, getLanguage } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { assertScope, decodeInvestigation, message, request } from "../api";
import type { Finding, Incident, Investigation } from "../types";
import { Badge, Empty, Icon, Loading, Notice, References } from "../ui";
import { human } from "../format";
function Findings({
  title,
  items,
  tone,
}: {
  title: string;
  items: Finding[];
  tone: string;
}) {
  return (
    <section className="findings">
      <h3>
        <span className={`dot ${tone}`} />
        {tr(title)}
        <span className="count">{items.length}</span>
      </h3>
      {!items.length && (
        <p className="muted">{tr("None recorded in this response.")}</p>
      )}
      {items.map((item, i) => (
        <article className="finding" key={item.id ?? item.code ?? i}>
          <p>{translateStatement(item.statement)}</p>
          {item.status && <Badge tone="amber">{human(item.status)}</Badge>}
          {item.execution && (
            <span className="micro">
              {tr("Diagnostic suggestion \u00B7 not executed")}
            </span>
          )}
          <References items={item.evidence_refs} />
        </article>
      ))}
    </section>
  );
}
export function InvestigationView({
  incident,
  onAudit,
}: {
  incident: Incident;
  onAudit: () => void;
}) {
  const [llm, setLlm] = useState(false);
  const [result, setResult] = useState<Investigation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function generate() {
    const current = new AbortController();
    controller.current?.abort();
    controller.current = current;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const data = await request(
        `/incidents/${incident.id}/investigate`,
        decodeInvestigation,
        {
          method: "POST",
          body: JSON.stringify({ use_llm: llm }),
          signal: current.signal,
        },
      );
      assertScope(data, incident.run_id, incident.id);
      if (!current.signal.aborted) setResult(data);
    } catch (error) {
      if (!current.signal.aborted) setError(message(error));
    } finally {
      if (!current.signal.aborted) setBusy(false);
    }
  }
  const realLlm =
    result?.generation.mode === "llm_assisted_validated" &&
    result.generation.llm_generated === true;
  return (
    <>
      <div className="section-heading">
        <div>
          <h3>{tr("Evidence-grounded investigation")}</h3>
          <p>{tr("Facts first. Hypotheses remain unconfirmed.")}</p>
        </div>
        <Icon name="shield" size={24} />
      </div>
      <div className="investigate-controls">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={llm}
            onChange={(event) => setLlm(event.target.checked)}
            disabled={busy}
          />
          {tr("Request configured LLM provider")}
        </label>
        <p className="micro">
          {tr(
            "Optional external request with a minimized evidence packet. A labeled fallback may be returned.",
          )}
        </p>
        <button className="primary" onClick={generate} disabled={busy}>
          <Icon name="search" />
          {busy ? tr("Investigating\u2026") : tr("Generate investigation")}
        </button>
      </div>
      {error && <Notice error>{error}</Notice>}
      {busy && (
        <Loading
          text={tr("Checking evidence and generating the investigation\u2026")}
        />
      )}
      {!result && !busy && !error && (
        <Empty title={tr("Ready to investigate")}>
          {tr(
            "Generate a local, deterministic summary or explicitly request your configured LLM provider. No suggested action will be executed.",
          )}
        </Empty>
      )}
      {result && (
        <div className="investigation-result">
          {result.source_context?.controlled && (
            <Notice>
              {tr(
                "Controlled experiment: all injected incidents are synthetic.",
              )}
            </Notice>
          )}
          <div className={`generation ${realLlm ? "llm" : "fallback"}`}>
            <Badge tone={realLlm ? "blue" : "amber"}>
              {realLlm
                ? tr("LLM-assisted \u00B7 evidence validated")
                : tr("Deterministic fallback \u00B7 not LLM inference")}
            </Badge>
            <p>
              {realLlm
                ? getLanguage() === "ar"
                  ? "اختار النموذج مراجع من قائمة حقائق متحقق منها؛ جميع العبارات صاغها الخادم."
                  : result.generation.method
                : `${tr("Local server-rendered summary.")} ${result.generation.reason ? `${tr("Reason:")} ${human(result.generation.reason)}.` : ""}`}
            </p>
            {realLlm && (
              <span className="micro">
                {tr("Provider:")}
                {result.generation.provider}
                {tr("\u00B7 model:")} {result.generation.model}
              </span>
            )}
          </div>
          <p className="investigation-summary">
            {translateStatement(result.summary.statement)}
          </p>
          <Notice>
            {tr("Root cause:")}
            {human(result.root_cause.status)}
            {tr(". These observations do not establish the underlying cause.")}
          </Notice>
          <Findings
            title={tr("Confirmed observations")}
            items={result.confirmed_observations}
            tone="green"
          />
          <Findings
            title={tr("Unconfirmed hypotheses")}
            items={result.hypotheses}
            tone="amber"
          />
          <Findings
            title={tr("Remaining unknowns")}
            items={result.unknowns}
            tone="neutral"
          />
          <Findings
            title={tr("Suggested diagnostics")}
            items={result.suggested_diagnostic_actions}
            tone="blue"
          />
          {result.relevant_replays.length > 0 && (
            <section className="findings">
              <h3>{tr("Relevant saved simulations")}</h3>
              {result.relevant_replays.map((replay) => (
                <article className="finding" key={replay.audit_id}>
                  <p>
                    {human(replay.operation)} · {human(replay.outcome)}
                  </p>
                  <code>{replay.audit_id}</code>
                  <p className="micro">
                    {tr(
                      "Simulation evidence only. No source correction applied.",
                    )}
                  </p>
                </article>
              ))}
              <button onClick={onAudit}>
                {tr("View saved replay audits")}
                <Icon name="arrow" />
              </button>
            </section>
          )}
        </div>
      )}
    </>
  );
}
