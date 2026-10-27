import type { SourceInfo } from "../types";
import { tr, translateSelection } from "../i18n";
import { human } from "../format";
import { Badge, Notice } from "../ui";
export function SourceView({ source }: { source?: SourceInfo }) {
  if (!source || source.kind === "local_demo") return null;
  const provenance = source.provenance,
    evaluation = source.evaluation;
  return (
    <details className="source-detail" open>
      <summary>{tr("Source attribution and evaluation")}</summary>
      <Badge tone={source.controlled ? "amber" : "blue"}>
        {human(source.kind)}
      </Badge>
      <p>{tr("Invoice-line identities, not unique invoice/order IDs.")}</p>
      {source.controlled &&
        provenance &&
        ["late_arrival", "schema_drift"].includes(provenance.experiment) && (
          <Notice>
            {tr(
              "These event and arrival timestamps are entirely simulated in UTC, independent of the historical invoice time.",
            )}
          </Notice>
        )}
      {source.controlled && (
        <Notice>
          {tr("Controlled experiment: all injected incidents are synthetic.")}
        </Notice>
      )}
      <p>
        {tr(
          "Historical source timestamps have no documented timezone. Real arrival delay is unknown.",
        )}
      </p>
      {provenance && (
        <>
          <p>
            <strong>{tr("Attribution")}: </strong>
            <a
              href="https://archive.ics.uci.edu/dataset/352/online+retail"
              target="_blank"
              rel="noreferrer"
            >
              {provenance.citation}
            </a>{" "}
            · {provenance.license}
          </p>
          <p>
            {tr("Experiment")}: {human(provenance.experiment)} ·{" "}
            {provenance.source_row_count}
          </p>
          <p>
            {tr("Selection")}:{" "}
            <bdi>{translateSelection(provenance.selection)}</bdi>
          </p>
        </>
      )}
      {evaluation && evaluation.status && evaluation.status !== "completed" && (
        <Notice error>{human(evaluation.status)}</Notice>
      )}
      {evaluation && (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{tr("Primary category")}</th>
                  <th>{tr("Source")}</th>
                  <th>{tr("Accepted")}</th>
                  <th>{tr("Rejected")}</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(evaluation.categories).map(([key, counts]) => (
                  <tr key={key}>
                    <td>{human(key)}</td>
                    <td>{counts.source}</td>
                    <td>{counts.accepted}</td>
                    <td>{counts.rejected}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Notice>
            {tr(
              "Contract rejections are policy exclusions, not fraud findings.",
            )}
          </Notice>
          <details className="data-details">
            <summary>{tr("Source observations")}</summary>
            {Object.entries(evaluation.source_observations)
              .filter(([, value]) => typeof value === "number")
              .map(([key, value]) => (
                <p key={key}>
                  {human(key)}: <bdi>{String(value)}</bdi>
                </p>
              ))}
          </details>
          {evaluation.controlled_ground_truth && (
            <div className="ground-truth">
              <h3>{tr("Ground truth comparison")}</h3>
              <Badge
                tone={
                  evaluation.controlled_ground_truth.detector_matches_expected
                    ? "green"
                    : "red"
                }
              >
                {tr(
                  evaluation.controlled_ground_truth.detector_matches_expected
                    ? "Passed"
                    : "Failed",
                )}
              </Badge>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>{tr("Incident type")}</th>
                      <th>{tr("Expected evidence count")}</th>
                      <th>{tr("Detected evidence count")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from(
                      new Set([
                        ...Object.keys(
                          evaluation.controlled_ground_truth
                            .expected_incident_counts,
                        ),
                        ...Object.keys(evaluation.incident_counts),
                      ]),
                    ).map((key) => (
                      <tr key={key}>
                        <td>{human(key)}</td>
                        <td>
                          {evaluation.controlled_ground_truth!
                            .expected_incident_counts[key] ?? 0}
                        </td>
                        <td>{evaluation.incident_counts[key] ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
      {source.workbook_sha256 && (
        <details>
          <summary>SHA-256</summary>
          <code>{source.workbook_sha256}</code>
        </details>
      )}
    </details>
  );
}
