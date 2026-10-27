import { tr } from "../i18n";
import { useState } from "react";
import type { Lineage } from "../types";
import { Badge, Empty, References } from "../ui";
import { human } from "../format";
const stages = [
  "raw",
  "validation",
  "clean",
  "reporting_expectation",
  "reporting",
];
const names = [
  "Raw orders",
  "Validation",
  "Clean orders",
  "Expected identity",
  "Reporting observation",
];
export function LineageView({ trace }: { trace: Lineage }) {
  const [page, setPage] = useState(0);
  const [focus, setFocus] = useState<string | null>(null);
  const rows = trace.records.slice(page * 8, page * 8 + 8);
  const ids = new Set(rows.map((row) => row.raw_order_id));
  const nodes = trace.nodes.filter(
    (node) =>
      node.raw_order_id !== undefined &&
      ids.has(node.raw_order_id) &&
      (stages.includes(node.stage ?? "") ||
        node.id.includes(":reporting_expectation:")),
  );
  const positioned = nodes.map((node) => {
    const column =
      node.kind === "recorded_expectation"
        ? 3
        : stages.indexOf(node.stage ?? "");
    const row = rows.findIndex(
      (record) => record.raw_order_id === node.raw_order_id,
    );
    return { ...node, x: 25 + column * 210, y: 58 + row * 128 };
  });
  const positions = new Map(positioned.map((node) => [node.id, node]));
  const edges = trace.edges.filter(
    (edge) => positions.has(edge.source) && positions.has(edge.target),
  );
  const focused = trace.nodes.find((node) => node.id === focus);
  if (!trace.records.length)
    return (
      <Empty title={tr("No record-level lineage recorded")}>
        {tr(
          "This incident may contain run-level schema evidence. No source records or stage events have been invented.",
        )}
      </Empty>
    );
  return (
    <>
      <div className="section-heading">
        <div>
          <h3>{tr("Recorded data lineage")}</h3>
          <p>
            {tr(
              "Persisted outcomes and expectations, not an execution-event timeline.",
            )}
          </p>
        </div>
        <Badge
          tone={
            trace.summary.evidence_completeness === "complete"
              ? "green"
              : "amber"
          }
        >
          {human(trace.summary.evidence_completeness)}
          {tr("evidence")}
        </Badge>
      </div>
      <div className="legend">
        <span className="confirmed">{tr("Confirmed")}</span>
        <span className="expectation">{tr("Recorded expectation")}</span>
        <span className="excluded">{tr("Intentionally excluded")}</span>
        <span className="missing">{tr("Missing observation")}</span>
        <span className="unknown">{tr("Unknown / not recorded")}</span>
      </div>
      <p className="micro">
        {tr("Pipeline direction: source → destination (left to right).")}
      </p>
      <div className="graph-scroll" dir="ltr">
        <svg
          className="lineage-graph"
          viewBox={`0 0 1090 ${80 + rows.length * 128}`}
          role="group"
          aria-label={tr("Recorded data lineage")}
        >
          <defs>
            <marker
              id="lineage-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="5"
              markerHeight="5"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#96a2b8" />
            </marker>
          </defs>
          {names.map((name, i) => (
            <text
              key={tr(name)}
              x={25 + i * 210}
              y={25}
              className="graph-label"
            >
              {tr(name)}
            </text>
          ))}
          {edges.map((edge) => {
            const start = positions.get(edge.source)!,
              end = positions.get(edge.target)!;
            const duplicate = edge.relation === "duplicate_of";
            const d = duplicate
              ? `M${start.x} ${start.y + 38} C0 ${start.y + 38},0 ${end.y + 38},${end.x} ${end.y + 38}`
              : `M${start.x + 182} ${start.y + 38} C${start.x + 198} ${start.y + 38},${end.x - 15} ${end.y + 38},${end.x} ${end.y + 38}`;
            return (
              <path
                key={edge.id}
                d={d}
                fill="none"
                stroke={
                  edge.relation === "expected_identity_not_observed"
                    ? "#dc7a60"
                    : "#96a2b8"
                }
                strokeDasharray={
                  ["identity_comparison", "recorded_expectation"].includes(
                    edge.evidence_kind,
                  ) || duplicate
                    ? "4 4"
                    : undefined
                }
                markerEnd="url(#lineage-arrow)"
              >
                <title>
                  {human(edge.relation)} · {human(edge.evidence_kind)}
                </title>
              </path>
            );
          })}
          {positioned.map((node) => {
            const record = rows.find(
              (row) => row.raw_order_id === node.raw_order_id,
            )!;
            const absent =
              node.status === "not_observed" &&
              node.kind === "absence_observation";
            const tone =
              node.kind === "recorded_expectation"
                ? "expectation"
                : absent
                  ? "missing"
                  : node.status === "rejected" ||
                      node.status === "intentionally_excluded"
                    ? "excluded"
                    : node.status === "unknown" ||
                        node.status === "not_observed"
                      ? "unknown"
                      : "confirmed";
            return (
              <g
                key={node.id}
                className={`graph-node ${tone} ${focus === node.id ? "focused" : ""}`}
                role="button"
                tabIndex={0}
                aria-label={`${human(node.stage ?? "raw")} ${node.raw_order_id}: ${human(node.status)}`}
                onClick={() => setFocus(node.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setFocus(node.id);
                  }
                }}
              >
                <rect x={node.x} y={node.y} width="182" height="82" rx="9" />
                <text x={node.x + 13} y={node.y + 22} className="graph-title">
                  {(record.source_order_id && record.source_order_id.length > 20
                    ? `${record.source_order_id.slice(0, 8)}…${record.source_order_id.slice(-9)}`
                    : record.source_order_id) ??
                    `${tr("Source record")} ${node.raw_order_id}`}
                </text>
                <text x={node.x + 13} y={node.y + 43} className="graph-status">
                  {absent ? tr("Expected, not observed") : human(node.status)}
                </text>
                <text x={node.x + 13} y={node.y + 65} className="graph-meta">
                  {node.stage === "raw"
                    ? `#${node.raw_order_id} · ${record.source_reference ? `XLSX ${record.source_reference.source_row}` : record.row_number}`
                    : node.stage === "validation" &&
                        record.validation.rejection_reason
                      ? human(record.validation.rejection_reason)
                      : human(node.kind)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      {trace.records.length > 8 && (
        <div className="pager">
          <button
            disabled={!page}
            onClick={() => {
              setPage(page - 1);
              setFocus(null);
            }}
          >
            {tr("Previous records")}
          </button>
          <span>
            {page * 8 + 1}–{Math.min((page + 1) * 8, trace.records.length)}
            {tr("of")} {trace.records.length}
          </span>
          <button
            disabled={(page + 1) * 8 >= trace.records.length}
            onClick={() => {
              setPage(page + 1);
              setFocus(null);
            }}
          >
            {tr("Next records")}
          </button>
        </div>
      )}
      {focused && (
        <div className="node-inspector">
          <h4>
            {human(focused.stage ?? "context")} · raw #{focused.raw_order_id}
          </h4>
          <p>
            {human(focused.status)} · {human(focused.kind)}
            {tr(". No per-record execution timestamp is claimed.")}
          </p>
          {rows.find((record) => record.raw_order_id === focused.raw_order_id)
            ?.source_reference && (
            <p className="micro">
              {tr("Original worksheet row")}:{" "}
              <bdi>
                {
                  rows.find(
                    (record) => record.raw_order_id === focused.raw_order_id,
                  )?.source_reference?.source_row
                }
              </bdi>{" "}
              · {tr("Mapped pipeline row")}:{" "}
              <bdi>
                {
                  rows.find(
                    (record) => record.raw_order_id === focused.raw_order_id,
                  )?.row_number
                }
              </bdi>
            </p>
          )}
          <References items={focused.evidence_refs} />
        </div>
      )}
      <details className="data-details">
        <summary>
          {tr("Accessible record observations (")}
          {trace.records.length})
        </summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{tr("Source record")}</th>
                <th>{tr("Validation")}</th>
                <th>{tr("Clean")}</th>
                <th>{tr("Reporting")}</th>
                <th>{tr("Last confirmed stage")}</th>
              </tr>
            </thead>
            <tbody>
              {trace.records.map((row) => (
                <tr key={row.raw_order_id}>
                  <td>{row.source_order_id ?? `Raw #${row.raw_order_id}`}</td>
                  <td>{human(row.validation.status)}</td>
                  <td>{human(row.clean.status)}</td>
                  <td>{human(row.reporting.status)}</td>
                  <td>{human(row.last_confirmed_stage)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      {(trace.evidence_gaps.length > 0 ||
        trace.integrity_issues.length > 0) && (
        <div className="gap-list">
          <h4>{tr("Evidence gaps & integrity checks")}</h4>
          {[...trace.evidence_gaps, ...trace.integrity_issues].map((gap, i) => (
            <p key={i}>
              {human(gap.code)}
              {gap.raw_order_id ? ` · raw #${gap.raw_order_id}` : ""}
            </p>
          ))}
        </div>
      )}
    </>
  );
}
