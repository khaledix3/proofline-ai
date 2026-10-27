export interface SourceInfo {
  kind: "local_demo" | "uci_real" | "uci_controlled";
  controlled: boolean;
  dataset?: string;
  workbook_sha256?: string;
  subset_sha256?: string;
  provenance?: {
    citation: string;
    source_url: string;
    license: string;
    selection: string;
    experiment: string;
    source_row_count: number;
    injected: boolean;
    arrival_semantics: string;
    source_timezone: string;
  };
  evaluation?: {
    status?: string;
    counts: Counts;
    categories: Record<
      string,
      { source: number; accepted: number; rejected: number }
    >;
    rejection_reasons: Counts;
    source_observations: Record<string, unknown>;
    incident_counts: Counts;
    controlled_ground_truth: {
      expected_incident_counts: Counts;
      target_raw_ids: number[];
      detector_matches_expected: boolean;
    } | null;
  };
}
export type IncidentType =
  | "MISSING_RECORDS"
  | "DUPLICATE_RECORDS"
  | "SCHEMA_DRIFT"
  | "LATE_ARRIVING_DATA";
export type Operation = "recover_missing_reporting" | "deduplicate_processing";
export type Counts = Record<string, number>;
export interface Run {
  source?: SourceInfo;
  id: string;
  status: string;
  source_name: string;
  started_at: string;
  finished_at: string | null;
  error_code: string | null;
  counts: Counts;
}
export interface Incident {
  source_kind?: string;
  id: string;
  run_id: string;
  incident_type: IncidentType;
  stage: string;
  affected_count: number;
  detected_at: string;
  evidence: Record<string, unknown>;
}
export interface Page<T> {
  items: T[];
  total?: number;
  limit: number;
  offset: number;
}
export interface Reference {
  table?: string;
  key?: Record<string, unknown>;
  kind?: string;
  path?: string;
  run_id?: string;
  raw_order_id?: number;
}
export interface Observation {
  status: string;
  evidence_kind: string;
  evidence_refs: Reference[];
  rejection_reason?: string | null;
  clean_order_id?: number | null;
  expected_identity?: {
    raw_order_id: number;
    order_id: string;
    run_id: string;
  } | null;
  observed_identity?: {
    raw_order_id: number;
    order_id: string;
    run_id: string;
  } | null;
  missing_expected_identity?: boolean | null;
}
export interface LineageRecord {
  source_reference?: {
    source_row: number;
    source_identity: string;
    sheet: string;
    workbook_sha256: string;
    evidence_ref: Reference;
  } | null;
  raw_order_id: number;
  row_number: number;
  source_order_id: string | null;
  raw: Observation;
  validation: Observation;
  clean: Observation;
  reporting: Observation;
  last_confirmed_stage: string;
  evidence_gaps: string[];
  integrity_issues: string[];
  timing: {
    event_timestamp: string | null;
    arrival_timestamp: string;
    time_source: string;
  } | null;
}
export interface GraphNode {
  id: string;
  raw_order_id?: number;
  stage: string | null;
  kind: string;
  status: string;
  evidence_refs: Reference[];
}
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  evidence_kind: string;
}
export interface Lineage {
  scope: { run_id: string; incident_id: string | null };
  run: Run;
  records: LineageRecord[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  summary: {
    evidence_completeness: string;
    integrity_status: string;
    returned_record_count: number;
    total_run_record_count: number;
  };
  evidence_gaps: { code: string; raw_order_id?: number }[];
  integrity_issues: { code: string; raw_order_id?: number }[];
  interpretations: { statement: string; root_cause: null }[];
}
export interface Finding {
  id?: string;
  code?: string;
  statement: string;
  evidence_refs?: Reference[];
  status?: string;
  execution?: string;
}
export interface Investigation {
  source_context?: {
    source_kind: string;
    controlled: boolean;
    experiment: string | null;
    clock_semantics: string;
  };
  run_id: string;
  incident_id: string;
  incident_type: IncidentType;
  generation: {
    mode: string;
    llm_generated: boolean;
    reason: string | null;
    provider: string | null;
    model: string | null;
    method: string;
  };
  summary: { statement: string; evidence_ids: string[] };
  confirmed_observations: Finding[];
  hypotheses: Finding[];
  root_cause: { status: string; value: null };
  unknowns: Finding[];
  suggested_diagnostic_actions: Finding[];
  records: {
    raw_order_id: number;
    display_order_id: string | null;
    last_confirmed_stage: string;
  }[];
  relevant_replays: {
    audit_id: string;
    outcome: string;
    operation: Operation;
    applied_to_source: false;
  }[];
  evidence_gaps: { code: string; raw_order_id?: number }[];
}
export interface Verification {
  passed: boolean;
  checks: Record<string, boolean>;
}
export interface SimulationState {
  incident_status?: Record<
    string,
    {
      detected_in_simulation: boolean;
      affected_count?: number;
      evidence?: unknown;
    }
  >;
  validation?: unknown[];
  counts: Counts;
  raw_order_ids?: number[];
  reported_identities?: { raw_order_id: number; order_id: string }[];
  validation_results?: unknown[];
}
export interface Replay {
  replay_id: string;
  operation: Operation;
  status: string;
  applied_to_source: boolean;
  input_references: { run_id: string; incident_id: string };
  before: SimulationState | null;
  after: SimulationState | null;
  comparison: {
    added_reporting_identities?: { raw_order_id: number; order_id: string }[];
    removed_reporting_identities?: { raw_order_id: number; order_id: string }[];
    count_deltas: Counts;
  } | null;
  verification: Verification;
  audit: { id: string; created_at: string; audit_sha256: string };
  failure?: { code: string; stage: string };
}
export interface AuditSummary {
  id: string;
  replay_id: string;
  run_id: string;
  incident_id: string;
  operation: Operation;
  outcome: string;
  created_at: string;
  verification_passed: boolean;
}
export interface Audit {
  id: string;
  replay_id: string;
  run_id: string;
  incident_id: string;
  created_at: string;
  audit_sha256: string;
  report: {
    outcome: string;
    operation: Operation;
    applied_to_source: boolean;
    before: SimulationState | null;
    after: SimulationState | null;
    verification: Verification;
    comparison: { count_deltas: Counts; validation_results_changed: boolean };
  };
}
