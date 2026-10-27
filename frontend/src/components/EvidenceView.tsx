import { tr } from "../i18n";
import type { Incident } from "../types";
import { Badge, Notice } from "../ui";
import { human } from "../format";
// Evidence remains untrusted display data; only render text, never HTML or commands.
export function EvidenceView({ incident }: { incident: Incident }) {
  const evidence = incident.evidence;
  const counts =
    evidence.counts &&
    typeof evidence.counts === "object" &&
    !Array.isArray(evidence.counts)
      ? Object.entries(evidence.counts).filter(
          (item): item is [string, number] => typeof item[1] === "number",
        )
      : [];
  const ids = Array.isArray(evidence.affected_order_ids)
    ? evidence.affected_order_ids.filter(
        (id): id is string => typeof id === "string",
      )
    : [];
  return (
    <>
      <div className="section-heading">
        <h3>{tr("Confirmed incident evidence")}</h3>
        <Badge>
          {human(
            typeof evidence.output_stage === "string"
              ? evidence.output_stage
              : incident.stage,
          )}
        </Badge>
      </div>
      <Notice>
        {tr(
          "This incident is a recorded observation. Its presence does not establish a root cause or a current resolution.",
        )}
      </Notice>
      <div className="facts">
        <div>
          <span>
            {tr(
              ["SCHEMA_DRIFT", "DUPLICATE_RECORDS"].includes(
                incident.incident_type,
              )
                ? "Affected evidence items"
                : "Affected records",
            )}
          </span>
          <strong>{incident.affected_count}</strong>
        </div>
        <div>
          <span>{tr("Observed stage")}</span>
          <strong>
            {human(
              typeof evidence.output_stage === "string"
                ? evidence.output_stage
                : incident.stage,
            )}
          </strong>
        </div>
      </div>
      {counts.length > 0 && (
        <div className="evidence-counts">
          {counts.map(([key, value]) => (
            <div key={key}>
              <span>{human(key)}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
      )}
      {ids.length > 0 && (
        <section className="affected-identities">
          <h3>{tr("Affected source identities")}</h3>
          {ids.map((id, i) => (
            <code key={i}>{id}</code>
          ))}
        </section>
      )}
      <h3>{tr("Recorded evidence packet")}</h3>
      <details className="data-details">
        <summary>
          {tr("Structured evidence \u00B7 original API response")}
        </summary>
        <pre>{JSON.stringify(evidence, null, 2)}</pre>
      </details>
      <h3>{tr("Known limits")}</h3>
      <p className="muted">
        {tr(
          "Root cause is unconfirmed. Missing observations do not prove that a processing stage executed.",
        )}
      </p>
    </>
  );
}
