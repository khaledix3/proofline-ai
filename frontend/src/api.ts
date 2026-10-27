import { translateError } from "./i18n";
import type {
  Audit,
  AuditSummary,
  Incident,
  Investigation,
  Lineage,
  Page,
  Replay,
  Run,
} from "./types";

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const fail = (): never => {
  throw new Error(
    "The backend returned an unsupported response. Refresh and check the API version.",
  );
};
function shape<T>(value: unknown, valid: boolean): T {
  if (!valid) fail();
  return value as T;
}
export const decodeRuns = (value: unknown) =>
  shape<Page<Run>>(
    value,
    object(value) &&
      Array.isArray(value.items) &&
      value.items.every(
        (v) =>
          object(v) &&
          typeof v.id === "string" &&
          typeof v.status === "string" &&
          object(v.counts),
      ),
  );
export const decodeIncidents = (value: unknown) =>
  shape<Page<Incident>>(
    value,
    object(value) &&
      Array.isArray(value.items) &&
      value.items.every(
        (v) =>
          object(v) &&
          typeof v.id === "string" &&
          typeof v.run_id === "string" &&
          object(v.evidence),
      ),
  );
export const decodeIncident = (value: unknown) =>
  shape<Incident>(
    value,
    object(value) &&
      typeof value.id === "string" &&
      typeof value.run_id === "string" &&
      object(value.evidence),
  );
export const decodeLineage = (value: unknown) =>
  shape<Lineage>(
    value,
    object(value) &&
      object(value.scope) &&
      object(value.run) &&
      Array.isArray(value.records) &&
      Array.isArray(value.nodes) &&
      Array.isArray(value.edges) &&
      object(value.summary) &&
      Array.isArray(value.evidence_gaps) &&
      Array.isArray(value.integrity_issues),
  );
export const decodeInvestigation = (value: unknown) =>
  shape<Investigation>(
    value,
    object(value) &&
      typeof value.run_id === "string" &&
      typeof value.incident_id === "string" &&
      object(value.generation) &&
      object(value.summary) &&
      Array.isArray(value.confirmed_observations) &&
      Array.isArray(value.unknowns) &&
      Array.isArray(value.hypotheses) &&
      Array.isArray(value.suggested_diagnostic_actions) &&
      Array.isArray(value.relevant_replays) &&
      object(value.root_cause),
  );
export const decodeReplay = (value: unknown) =>
  shape<Replay>(
    value,
    object(value) &&
      typeof value.replay_id === "string" &&
      object(value.input_references) &&
      object(value.audit) &&
      object(value.verification) &&
      typeof value.applied_to_source === "boolean",
  );
export const decodeAudits = (value: unknown) =>
  shape<Page<AuditSummary>>(
    value,
    object(value) &&
      Array.isArray(value.items) &&
      value.items.every(
        (v) =>
          object(v) &&
          typeof v.id === "string" &&
          typeof v.run_id === "string" &&
          typeof v.incident_id === "string",
      ),
  );
export const decodeAudit = (value: unknown) =>
  shape<Audit>(
    value,
    object(value) &&
      typeof value.id === "string" &&
      typeof value.run_id === "string" &&
      typeof value.incident_id === "string" &&
      object(value.report) &&
      object(value.report.verification),
  );
export const decodeHealth = (value: unknown) =>
  shape<{ status: string; service: string }>(
    value,
    object(value) && value.status === "ok" && value.service === "proofline-ai",
  );

export async function request<T>(
  path: string,
  decode: (value: unknown) => T,
  options: RequestInit = {},
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timeout = setTimeout(abort, options.method === "POST" ? 60000 : 15000);
  try {
    const response = await fetch(`/api${path}`, {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = object(body) ? body.detail : null;
      const message =
        typeof detail === "string"
          ? detail
          : object(detail) && typeof detail.message === "string"
            ? detail.message
            : `Request failed (HTTP ${response.status}).`;
      throw new Error(message);
    }
    return decode(body);
  } catch (error) {
    if (controller.signal.aborted && !options.signal?.aborted)
      throw new Error(
        "The backend request timed out. No source correction was requested. Refresh or retry.",
      );
    throw error;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abort);
  }
}
export function assertScope(
  value: { run_id: string; incident_id: string },
  runId: string,
  incidentId: string,
) {
  if (value.run_id !== runId || value.incident_id !== incidentId)
    throw new Error(
      "The response belongs to a different run or incident. Nothing has been displayed.",
    );
}
export const message = (error: unknown) =>
  error instanceof Error
    ? translateError(error.message)
    : translateError("The request could not be completed.");
