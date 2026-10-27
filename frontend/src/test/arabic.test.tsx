import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import App from "../App";
import { setLanguage, translateStatement, translateCode } from "../i18n";
import { date } from "../format";
import { SourceView } from "../components/SourceView";
import { InvestigationView } from "../components/InvestigationView";
import { ReplayView } from "../components/ReplayView";
import { LineageView } from "../components/LineageView";
import type { Incident, Lineage, SourceInfo } from "../types";

const incident: Incident = {
  id: "case-one",
  run_id: "run-one",
  incident_type: "MISSING_RECORDS",
  stage: "clean_to_reporting",
  affected_count: 1,
  detected_at: "2026-01-01T10:00:00Z",
  evidence: {
    missing_records: [{ raw_order_id: 29, order_id: "UCI352_ROW2" }],
  },
};
const source: SourceInfo = {
  kind: "uci_real",
  controlled: false,
  provenance: {
    citation: "Chen, D. (2015). UCI Online Retail",
    source_url: "https://archive.ics.uci.edu/dataset/352/online+retail",
    license: "CC BY 4.0",
    selection: "fixture",
    experiment: "real",
    source_row_count: 2,
    injected: false,
    arrival_semantics: "actual import; no real event delay",
    source_timezone: "unspecified",
  },
  evaluation: {
    counts: { source_count: 2, accepted_count: 1, rejected_count: 1 },
    categories: {
      missing_customer_reference: { source: 1, accepted: 0, rejected: 1 },
      other_transaction_line: { source: 1, accepted: 1, rejected: 0 },
    },
    rejection_reasons: { INVALID_CUSTOMER_ID: 1 },
    source_observations: { missing_customer_reference: 1 },
    incident_counts: {},
    controlled_ground_truth: null,
  },
};
function response(value: unknown) {
  return { ok: true, status: 200, json: async () => value } as Response;
}
function api() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.includes("/health"))
      return response({ status: "ok", service: "proofline-ai" });
    if (path.includes("/pipeline-runs?"))
      return response({ items: [], total: 0, limit: 20, offset: 0 });
    if (path.includes("/incidents?"))
      return response({ items: [], limit: 20, offset: 0 });
    if (path.includes("/replays?"))
      return response({ items: [], total: 0, limit: 20, offset: 0 });
    throw new Error("Unexpected fixture request");
  });
}
beforeEach(() => {
  vi.restoreAllMocks();
  setLanguage("ar");
});
afterEach(() => {
  setLanguage("en");
  vi.restoreAllMocks();
});

it("uses Arabic and RTL, retains technical route names, and toggles to English", async () => {
  const fetch = api();
  render(<App />);
  expect(document.documentElement).toHaveAttribute("dir", "rtl");
  expect(document.documentElement).toHaveAttribute("lang", "ar");
  expect(await screen.findByText("الخادم متصل")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "مساحة التحقيق" }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("اللغة"), { target: { value: "en" } });
  expect(document.documentElement).toHaveAttribute("dir", "ltr");
  expect(
    await screen.findByRole("heading", { name: "Investigation workspace" }),
  ).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => String(url) === "/api/health")).toBe(
    true,
  );
});
it("filters real and controlled data using unchanged source-kind identifiers", async () => {
  const fetch = api();
  render(<App />);
  fireEvent.change(screen.getByLabelText("فئة المصدر"), {
    target: { value: "uci_controlled" },
  });
  await screen.findByText("لا توجد حوادث ضمن النطاق المحدد");
  expect(
    fetch.mock.calls.some(([url]) =>
      String(url).includes("source_kind=uci_controlled"),
    ),
  ).toBe(true);
});
it("renders source categories and attribution without claiming fraud or real lateness", () => {
  render(<SourceView source={source} />);
  expect(screen.getByText("مرجع عميل مفقود")).toBeInTheDocument();
  expect(
    screen.getByText("الاستبعاد وفق العقد لا يمثل اكتشاف احتيال."),
  ).toBeInTheDocument();
  expect(screen.getByText(/تأخر الوصول الفعلي غير معلوم/)).toBeInTheDocument();
  expect(screen.getByRole("link")).toHaveAttribute(
    "href",
    source.provenance!.source_url,
  );
});
it("clearly labels injected ground truth and does not alter reference IDs", () => {
  render(
    <SourceView
      source={{
        ...source,
        kind: "uci_controlled",
        controlled: true,
        evaluation: {
          ...source.evaluation!,
          incident_counts: { MISSING_RECORDS: 1 },
          controlled_ground_truth: {
            expected_incident_counts: { MISSING_RECORDS: 1 },
            target_raw_ids: [29],
            detector_matches_expected: true,
          },
        },
      }}
    />,
  );
  expect(
    screen.getByText("تجربة مضبوطة: جميع الحوادث المحقونة اصطناعية."),
  ).toBeInTheDocument();
  expect(
    screen.getByText("المقارنة بالحقيقة المعروفة للتجربة"),
  ).toBeInTheDocument();
});
it("localizes grounded statements without changing IDs, counts or certainty", () => {
  expect(
    translateStatement(
      "No current reporting row matches the recorded expected identity for raw record 29; the cause is unknown.",
    ),
  ).toContain("29؛ السبب غير معلوم");
  expect(
    translateStatement(
      "Reporting delivery, filtering, or identity handling could explain the gap; the cause is unconfirmed.",
    ),
  ).toContain("غير مؤكد");
  expect(
    translateStatement(
      "Durable replay audit abc123 records outcome verified for a simulation; no source correction was applied.",
    ),
  ).toContain("abc123");
  expect(
    translateStatement(
      "Mapped pipeline row 1 is stored as raw record 1033; original worksheet row is 2.",
    ),
  ).toContain("صف ورقة المصدر الأصلي هو 2");
  expect(translateCode("INVALID_QUANTITY")).toBe("كمية خارج عقد القيم الموجبة");
});
it("uses explicit UTC and Latin digits for Arabic dates", () => {
  expect(date("2026-01-01T10:05:00Z")).toContain("2026");
  expect(date("2026-01-01T10:05:00Z")).toContain("10:05");
  expect(date("2026-01-01T10:05:00Z")).toContain("UTC");
  expect(date(null)).toBe("غير مسجل");
});
it("displays deterministic fallback as non-LLM and preserves evidence references", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    response({
      run_id: incident.run_id,
      incident_id: incident.id,
      incident_type: incident.incident_type,
      generation: {
        mode: "deterministic_fallback",
        llm_generated: false,
        reason: "llm_not_requested",
        provider: null,
        model: null,
        method: "server",
      },
      summary: {
        statement: "Stored incident MISSING_RECORDS affects 1 evidence items.",
        evidence_ids: ["I"],
      },
      confirmed_observations: [
        {
          statement: "Clean record 16 references raw record 29.",
          evidence_refs: [{ table: "clean_orders", key: { id: 16 } }],
        },
      ],
      hypotheses: [],
      unknowns: [
        { statement: "The recorded evidence does not establish a root cause." },
      ],
      suggested_diagnostic_actions: [],
      root_cause: { status: "unknown", value: null },
      relevant_replays: [],
      records: [],
      evidence_gaps: [],
    }),
  );
  render(<InvestigationView incident={incident} onAudit={() => {}} />);
  fireEvent.click(screen.getByText("إنشاء تقرير التحقيق"));
  expect(
    await screen.findByText("ملخص حتمي بديل · ليس استنتاجًا من نموذج لغوي"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("السجل المقبول 16 مرتبط بالسجل الأصلي 29."),
  ).toBeInTheDocument();
  expect(screen.getByText("clean_orders #16")).toBeInTheDocument();
});
it("labels replay as isolated simulation and does not send requests automatically", async () => {
  const fetch = api();
  render(<ReplayView incident={incident} />);
  expect(
    screen.getByText("لا يُطبّق أي تصحيح على البيانات الأصلية"),
  ).toBeInTheDocument();
  expect(screen.getByText("تشغيل محاكاة معزولة")).toBeInTheDocument();
  await screen.findByText("لا توجد محاكاة محفوظة لهذا الحادث");
  expect(
    fetch.mock.calls.every(([, options]) => options?.method !== "POST"),
  ).toBe(true);
});
it("keeps source-to-destination graph geometry LTR inside an Arabic document", () => {
  const observation = {
    status: "observed",
    evidence_kind: "persisted_record",
    evidence_refs: [],
  };
  const trace = {
    scope: { run_id: "run-one", incident_id: "case-one" },
    run: {},
    records: [
      {
        raw_order_id: 29,
        row_number: 2,
        source_order_id: "UCI352_ROW2",
        raw: observation,
        validation: observation,
        clean: observation,
        reporting: observation,
        last_confirmed_stage: "clean",
        evidence_gaps: [],
        integrity_issues: [],
        timing: null,
      },
    ],
    nodes: [
      {
        id: "raw",
        raw_order_id: 29,
        stage: "raw",
        kind: "persisted_record",
        status: "observed",
        evidence_refs: [],
      },
    ],
    edges: [],
    summary: { evidence_completeness: "complete" },
    evidence_gaps: [],
    integrity_issues: [],
  } as unknown as Lineage;
  render(<LineageView trace={trace} />);
  expect(
    screen.getByRole("group", { name: "تتبّع البيانات المثبت" }),
  ).toHaveAccessibleName("تتبّع البيانات المثبت");
  expect(
    screen.getByRole("group", { name: "تتبّع البيانات المثبت" }).parentElement,
  ).toHaveAttribute("dir", "ltr");
  expect(screen.getAllByText("UCI352_ROW2")[0]).toBeInTheDocument();
});
