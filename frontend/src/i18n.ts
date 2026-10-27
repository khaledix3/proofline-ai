import arabic from "./ar.json";
export type Language = "ar" | "en";
const dictionary: Record<string, string> = arabic;
let language: Language = "ar";
try {
  language = localStorage.getItem("proofline-language") === "en" ? "en" : "ar";
} catch {
  /* Storage may be unavailable. */
}
export const getLanguage = () => language;
export function setLanguage(value: Language) {
  language = value;
  document.documentElement.lang = value;
  document.documentElement.dir = value === "ar" ? "rtl" : "ltr";
  try {
    localStorage.setItem("proofline-language", value);
  } catch {
    /* No persistence required. */
  }
}
export const tr = (text: string): string => {
  const value = language === "ar" ? (dictionary[text] ?? text) : text;
  const suffix = [
    "Run",
    "Page",
    "Loaded run page ·",
    "Evidence completeness:",
    "Root cause:",
    "Provider:",
    "Saved audit ·",
    "Saved simulation outcome:",
  ].includes(text)
    ? " "
    : "";
  const prefix = ["affected", "runs", "evidence", "of"].includes(text)
    ? " "
    : "";
  return prefix + value + suffix;
};
setLanguage(language);

const codeArabic: Record<string, string> = {
"run_count":"عمليات التشغيل",
"processed_record_count":"السجلات المعالجة",
"validation_failure_count":"مرفوضات التحقق بلا التكرارات",
"acceptance_percentage":"نسبة القبول",
"rejection_percentage":"نسبة الرفض",
"missing_downstream_record_count":"هويات التقرير المفقودة",
"schema_drift_incident_count":"حوادث تغير المخطط",
"schema_monitoring_run_count":"عمليات بمراقبة المخطط",
"late_arriving_record_count":"السجلات المتأخرة المراقبة",
"lateness_monitoring_run_count":"عمليات بمراقبة التأخر",
"true_positives":"إيجابيات صحيحة",
"false_positives":"إيجابيات كاذبة",
"false_negatives":"سلبيات كاذبة",
"precision":"دقة الإيجابيات",
"recall":"الاسترجاع",
"f1":"درجة F1",
"all":"جميع السيناريوهات",
"normal":"خط أساس بلا عطل",
"missing_records":"غياب مخرج",
"duplicate_outputs":"تكرار مخرج",
"NO_POSITIVE_PREDICTIONS":"لا توجد تنبؤات إيجابية",
"NO_POSITIVE_GROUND_TRUTH":"لا توجد حالات إيجابية مرجعية",
"NO_POSITIVE_LABELS_OR_PREDICTIONS":"لا توجد حالات أو تنبؤات إيجابية",
"INCOMPLETE_EVALUATION":"التقييم غير مكتمل",
"FINANCIAL_LOSS_NOT_ESTABLISHED":"الخسارة المالية غير مثبتة",
"ROOT_CAUSE_UNKNOWN":"السبب الجذري مجهول",
"REPORTING_OUTPUT_HAS_NO_AMOUNT_COLUMNS":"مخرج التقرير لا يخزن قيمًا مالية",
"verified_associated_value":"قيمة مصدر مرتبطة متحققة",
"not_applicable":"لا ينطبق",
"UNSAFE_DOWNSTREAM_STATE":"رفض إعادة التشغيل: توجد هوية مخرج غير متوقعة",
"unsupported_incident_type":"نوع غير مدعوم لإعادة التشغيل",
"UNVERIFIED_CURRENCY_AND_MAPPING":"العملة وربط المصدر غير متحققين",

  DATASET_SOURCE_REFERENCE_NOT_RECORDED: "مرجع صف ورقة المصدر غير مسجل",
  running: "قيد التنفيذ",
  completed: "مكتمل",
  failed: "فشل التنفيذ",
  verified: "متحقق منه",
  no_change_required: "لا يلزم تغيير",
  verification_failed: "فشل التحقق",
  accepted: "مقبول",
  rejected: "مستبعد",
  observed: "مرصود",
  confirmed: "مثبت",
  expected: "متوقع",
  unknown: "غير معلوم",
  unconfirmed: "غير مؤكد",
  complete: "مكتملة",
  incomplete: "غير مكتملة",
  consistent: "متسقة",
  inconsistent: "غير متسقة",
  intentionally_excluded: "مستبعد وفق السياسة",
  not_observed: "غير مرصود",
  identity_mismatch: "اختلاف المعرّف",
  raw: "السجلات الأصلية",
  clean: "السجلات المقبولة",
  validation: "التحقق",
  reporting: "التقارير",
  raw_to_clean: "من الأصل إلى المقبول",
  clean_to_reporting: "من المقبول إلى التقارير",
  source_to_raw: "من المصدر إلى الأصل",
  source_to_ingestion: "من المصدر إلى الاستقبال",
  source_count: "عدد السجلات المصدرية",
  accepted_count: "عدد المقبول",
  rejected_count: "عدد المستبعد",
  duplicate_count: "عدد التكرارات المصدرية",
  expected_reporting_count: "عدد التقارير المتوقع",
  reported_count: "عدد التقارير المرصود",
  duplicate_output_excess_count: "التكرارات الزائدة في المخرجات",
  expected_count: "العدد المتوقع",
  actual_count: "العدد الفعلي",
  matched_count: "العدد المطابق",
  missing_count: "العدد المفقود",
  unexpected_count: "العدد غير المتوقع",
  raw_count: "عدد الأصل",
  clean_count: "عدد المقبول",
  valid_record_count: "السجلات الصالحة",
  invalid_record_count: "السجلات غير الصالحة",
  duplicate_record_count: "التكرارات المصدرية الزائدة",
  affected_order_id_count: "عدد المعرّفات المتأثرة",
  persisted_record: "سجل محفوظ",
  persisted_outcome: "نتيجة محفوظة",
  recorded_expectation: "توقع مسجل",
  absence_observation: "ملاحظة غياب",
  stage_placeholder: "مرحلة غير موثقة",
  policy_outcome: "نتيجة سياسة الاستبعاد",
  identity_comparison: "مقارنة المعرّفات",
  persisted_relationship: "علاقة محفوظة",
  persisted_rejection: "استبعاد محفوظ",
  validation_outcome: "نتيجة التحقق",
  accepted_source: "مصدر مقبول",
  expected_downstream_identity: "معرّف وجهة متوقع",
  expected_identity_not_observed: "المعرّف المتوقع غير مرصود",
  duplicate_of: "تكرار للسجل",
  matches_expectation: "مطابق للتوقع",
  recover_missing_reporting: "محاكاة استعادة التقارير المفقودة",
  deduplicate_processing: "محاكاة تصحيح المعالجة المكررة",
  llm_not_requested: "لم يُطلب النموذج اللغوي",
  provider_unconfigured: "لم يُهيّأ المزوّد",
  provider_sdk_unavailable: "مكتبة المزوّد غير متاحة",
  provider_timeout: "انتهت مهلة المزوّد",
  provider_error: "تعذّر طلب المزوّد",
  provider_response_rejected: "رُفضت استجابة المزوّد لعدم توافقها مع الأدلة",
  provider_configuration_invalid: "إعدادات المزوّد غير صالحة",
  provider_response_unavailable: "لا تتوفر استجابة صالحة من المزوّد",
  local_demo: "أمثلة محلية اصطناعية",
  uci_real: "بيانات UCI الحقيقية",
  uci_controlled: "أعطال مضبوطة على بيانات UCI",
  real: "تقييم المصدر الحقيقي",
  missing_reporting: "غياب تقارير محقون",
  duplicate_processing: "تكرار معالجة محقون",
  schema_drift: "انحراف مخطط محقون",
  late_arrival: "توقيت وصول اصطناعي",
  cancellation: "إلغاء وفق رمز الفاتورة",
  negative_quantity_return_or_adjustment_candidate:
    "كمية سالبة: مرشح إرجاع أو تسوية",
  zero_quantity: "كمية صفرية",
  missing_customer_reference: "مرجع عميل مفقود",
  zero_price: "سعر صفري",
  other_transaction_line: "أسطر معاملات أخرى",
  missing_description: "وصف مفقود",
  repeated_invoice_excess_lines: "أسطر إضافية تشترك في رقم الفاتورة",
  exact_content_repeat_candidates: "أسطر متطابقة المحتوى مرشحة للمراجعة",
  source: "المصدر",
  affected_record_count: "عدد السجلات المتأثرة",
  difference_count: "عدد الاختلافات",
  source_record_count: "عدد سجلات المصدر",
  late_record_count: "عدد السجلات المتأخرة",
  eligible_record_count: "السجلات المؤهلة",
  on_time_record_count: "السجلات ضمن المهلة",
  INVALID_COLUMNS: "أعمدة غير متوافقة",
  INVALID_ORDER_ID: "معرّف سجل غير صالح",
  INVALID_CUSTOMER_ID: "مرجع عميل غير صالح أو مفقود",
  INVALID_QUANTITY: "كمية خارج عقد القيم الموجبة",
  INVALID_UNIT_PRICE: "سعر غير متوافق مع العقد",
  INVALID_ORDER_DATE: "تاريخ غير صالح",
  INVALID_EVENT_TIMESTAMP: "وقت حدث غير صالح أو بلا منطقة زمنية",
  DUPLICATE_ORDER_ID: "تكرار المعرّف داخل التشغيل",
  MISSING_RECORDS: "سجلات مفقودة",
  DUPLICATE_RECORDS: "سجلات مكررة",
  SCHEMA_DRIFT: "انحراف المخطط",
  LATE_ARRIVING_DATA: "بيانات متأخرة الوصول",
  raw_ids_unchanged: "المعرّفات الأصلية لم تتغير",
  protected_input_evidence_unchanged: "الأدلة المحمية لم تتغير",
  validation_results_unchanged: "نتائج التحقق لم تتغير",
  clean_identities_unchanged: "معرّفات المقبول لم تتغير",
  expected_identities_unchanged: "المعرّفات المتوقعة لم تتغير",
  raw_counts_reconcile: "الأعداد الأصلية متطابقة",
  outputs_are_unique: "المخرجات خالية من تصادم المعرّفات",
  other_incident_evidence_preserved: "أدلة الحوادث الأخرى محفوظة",
  missing_identities_recovered_in_simulation:
    "استُعيدت المعرّفات المفقودة داخل المحاكاة",
  no_existing_reporting_identity_removed: "لم يُحذف معرّف تقارير موجود",
  captured_input_unchanged: "المدخل الملتقط لم يتغير",
  raw_duplicate_evidence_preserved: "أدلة التكرار الأصلي محفوظة",
  unrelated_missing_sources_preserved: "المصادر المفقودة غير المعنية محفوظة",
  reporting_source_ids_unchanged: "معرّفات مصادر التقارير لم تتغير",
  VALIDATION_OUTCOME_NOT_RECORDED: "نتيجة التحقق غير مسجلة",
  REPORTING_STAGE_NOT_RECORDED: "مرحلة التقارير غير مسجلة",
  REPORTING_EXPECTATION_NOT_RECORDED: "توقع التقارير غير مسجل",
  SCHEMA_CONTRACT_NOT_RECORDED: "عقد المخطط غير مسجل",
  ARRIVAL_METADATA_NOT_RECORDED: "بيانات الوصول غير مسجلة",
  INCIDENT_SOURCE_REFERENCES_NOT_RECORDED: "مراجع مصدر الحادث غير مسجلة",
  UNRESOLVED_INCIDENT_SOURCE: "تعذّر ربط مصدر الحادث",
  STORED_COUNTS_DIFFER_FROM_CURRENT_RECORDS:
    "الأعداد المحفوظة تختلف عن السجلات الحالية",
  CONFLICTING_VALIDATION_OUTCOMES: "نتائج تحقق متعارضة",
  REPORTING_MANIFEST_COUNT_MISMATCH: "اختلاف عدد قائمة توقعات التقارير",
  INVALID_INCIDENT_EVIDENCE_SHAPE: "بنية أدلة الحادث غير صالحة",
  INCIDENT_EVIDENCE_RUN_MISMATCH: "أدلة الحادث تخص تشغيلًا مختلفًا",
  DUPLICATE_TARGET_OUTCOME_UNAVAILABLE: "نتيجة السجل المرجعي للتكرار غير متاحة",
  COMPLETED_RUN_WITHOUT_VALIDATION_OUTCOME: "تشغيل مكتمل دون نتيجة تحقق مسجلة",
};
export function translateCode(value: string): string {
  return language === "ar"
    ? (codeArabic[value] ?? value)
    : value.replaceAll("_", " ").toLowerCase();
}

const statements: Record<string, string> = {
  "This run is a controlled UCI experiment; injected incidents are not findings about historical source failures.":
    "هذه تجربة UCI مضبوطة؛ الحوادث المحقونة لا تمثل اكتشافًا لأعطال تاريخية في المصدر.",
  "This run evaluates preserved UCI worksheet lines; invoice-number repetition alone is not duplicate evidence.":
    "يقيّم هذا التشغيل أسطر ورقة UCI المحفوظة؛ تكرار رقم الفاتورة وحده ليس دليلًا على التكرار.",
  "Event and arrival clocks for this run were explicitly simulated; they do not measure historical delivery.":
    "حُوكيت أوقات الحدث والوصول في هذا التشغيل صراحةً؛ وهي لا تقيس التسليم التاريخي.",
  "The recorded evidence does not establish a root cause.":
    "الأدلة المسجلة لا تثبت السبب الجذري.",
  "A separate per-record validation execution timestamp is not recorded.":
    "لا يوجد وقت تنفيذ مستقل مسجل للتحقق من كل سجل.",
  "Some source references or observations are unavailable or inconsistent.":
    "بعض مراجع المصدر أو الملاحظات غير متاحة أو غير متسقة.",
  "Replay audit results describe a simulation, not a correction applied to source data.":
    "نتائج تدقيق إعادة التشغيل تصف محاكاة، وليست تصحيحًا مطبقًا على المصدر.",
  "The incident exceeds the bounded record context; not all records are included.":
    "يتجاوز الحادث حد سياق السجلات؛ لم تُضمّن جميع السجلات.",
  "Some stored timing values could not be verified safely.":
    "تعذّر التحقق الآمن من بعض قيم التوقيت المحفوظة.",
  "Reporting delivery, filtering, or identity handling could explain the gap; the cause is unconfirmed.":
    "قد يرتبط الغياب بتسليم التقارير أو التصفية أو معالجة المعرّفات؛ السبب غير مؤكد.",
  "Repeated source submission or business-ID reuse could explain the duplicate input; neither is established.":
    "قد يرتبط تكرار المدخل بإعادة إرسال المصدر أو إعادة استخدام المعرّف؛ لم يُثبت أي منهما.",
  "A producer schema or serialization change could explain the contract differences; this is unconfirmed.":
    "قد تفسر تغييرات مخطط المنتج أو تسلسل البيانات اختلاف العقد؛ هذا غير مؤكد.",
  "Delayed delivery or a clock discrepancy could explain lateness; the evidence does not distinguish them.":
    "قد يفسر تأخر التسليم أو اختلاف الساعة التأخر؛ الأدلة لا تميّز بينهما.",
  "Collect the missing contract, manifest, stage, or timing observations before drawing conclusions; do not invent absent events.":
    "اجمع ملاحظات العقد أو قائمة التوقعات أو المرحلة أو التوقيت الناقصة قبل الاستنتاج؛ لا تفترض أحداثًا غير مسجلة.",
  "Inspect reporting delivery/filtering logs and identity mapping for the referenced source, without changing data.":
    "راجع سجلات تسليم التقارير والتصفية وربط المعرّفات للمصدر المرجعي دون تعديل البيانات.",
  "Compare source submission identifiers and ingestion records for the duplicate group.":
    "قارن معرّفات إرسال المصدر وسجلات الاستقبال لمجموعة التكرار.",
  "Compare the recorded producer schema with the versioned expected contract.":
    "قارن مخطط المنتج المسجل بعقد المخطط المتوقع ذي الإصدار المحدد.",
  "Check event-time provenance, arrival capture, and clock synchronization.":
    "تحقق من مصدر وقت الحدث وتسجيل الوصول وتزامن الساعات.",
  "Review the referenced simulation verification; do not apply a correction automatically.":
    "راجع تحقق المحاكاة المرجعية؛ لا تطبّق تصحيحًا تلقائيًا.",
  "Lineage reports incomplete or inconsistent recorded evidence.":
    "يشير التتبّع إلى أدلة مسجلة غير مكتملة أو غير متسقة.",
};
export function translateStatement(value: string): string {
  if (language === "en") return value;
  let result = statements[value] ?? value;
  result = result
    .replace(
      /Stored incident (MISSING_RECORDS|DUPLICATE_RECORDS|SCHEMA_DRIFT|LATE_ARRIVING_DATA) affects (\d+) evidence items\./g,
      (_, kind: string, count: string) =>
        `الحادث المسجل من نوع «${translateCode(kind)}» يتضمن ${count} عنصر دليل متأثر.`,
    )
    .replace(
      /Source row (\d+) is stored as raw record (\d+)\./g,
      "الصف المصدري $1 محفوظ بالسجل الأصلي $2.",
    )
    .replace(
      /Record (\d+) has a committed (accepted|rejected) validation outcome; no separate execution time is recorded\./g,
      (_, id: string, outcome: string) =>
        `للسجل ${id} نتيجة تحقق محفوظة «${translateCode(outcome)}»؛ لا يوجد وقت تنفيذ مستقل مسجل.`,
    )
    .replace(
      /Clean record (\d+) references raw record (\d+)\./g,
      "السجل المقبول $1 مرتبط بالسجل الأصلي $2.",
    )
    .replace(
      /A reporting manifest records an expected downstream identity for raw record (\d+)\./g,
      "تسجل قائمة التقارير معرّف وجهة متوقعًا للسجل الأصلي $1.",
    )
    .replace(
      /Reporting currently contains a row referencing raw record (\d+)\./g,
      "تحتوي التقارير حاليًا على صف مرتبط بالسجل الأصلي $1.",
    )
    .replace(
      /No current reporting row matches the recorded expected identity for raw record (\d+); the cause is unknown\./g,
      "لا يوجد صف تقارير حالي يطابق المعرّف المتوقع للسجل الأصلي $1؛ السبب غير معلوم.",
    )
    .replace(
      /The stored snapshot identifies (\d+) excess valid raw duplicate records; input preservation does not imply duplicated clean output\./g,
      "تحدد اللقطة المحفوظة $1 سجلًا أصليًا صالحًا مكررًا زائدًا؛ حفظ المدخل لا يعني تكرار المخرجات المقبولة.",
    )
    .replace(
      /The stored snapshot records (\d+) excess reporting identity collisions; invoice-number repetition alone is not duplicate evidence\./g,
      "تسجل اللقطة المحفوظة $1 تصادمًا زائدًا في معرّفات التقارير؛ تكرار رقم الفاتورة وحده ليس دليل تكرار.",
    )
    .replace(
      /The stored schema snapshot records (\d+) exact differences against its expected versioned contract\./g,
      "تسجل لقطة المخطط $1 اختلافًا محددًا عن العقد المتوقع ذي الإصدار المحدد.",
    )
    .replace(
      /Stored timing evidence records raw record (\d+) arriving ([0-9.]+) seconds beyond its deadline\./g,
      "تسجل أدلة التوقيت وصول السجل الأصلي $1 بعد المهلة بمقدار $2 ثانية.",
    )
    .replace(
      /Durable replay audit ([a-f0-9]+) records outcome ([a-z_]+) for a simulation; no source correction was applied\./g,
      (_, id: string, outcome: string) =>
        `يسجل تدقيق المحاكاة ${id} النتيجة «${translateCode(outcome)}»؛ لم يُطبّق تصحيح على الأصل.`,
    );
  result = result.replace(
    /Mapped pipeline row (\d+) is stored as raw record (\d+); original worksheet row is (\d+)\./g,
    "صف المعالجة $1 محفوظ بالسجل الأصلي $2؛ صف ورقة المصدر الأصلي هو $3.",
  );
  for (const [english, arabicText] of Object.entries(statements))
    result = result.replaceAll(english, arabicText);
  return result;
}
export function translateError(value: string): string {
  if (language === "en") return value;
  const known: Record<string, string> = {
    "Database unavailable or migrations missing; see README.":
      "قاعدة البيانات غير متاحة أو ترحيلاتها ناقصة؛ راجع دليل المشروع.",
    "The response belongs to a different run or incident. Nothing has been displayed.":
      "الاستجابة تخص تشغيلًا أو حادثًا مختلفًا. لم تُعرض بياناتها.",
    "The backend returned an unsupported response. Refresh and check the API version.":
      "أعاد الخادم استجابة غير مدعومة. حدّث البيانات وتحقق من إصدار API.",
    "The backend request timed out. No source correction was requested. Refresh or retry.":
      "انتهت مهلة طلب الخادم. لم يُطلب تعديل المصدر؛ حدّث البيانات أو أعد المحاولة.",
    "Stored incident evidence does not match the current source observation.":
      "أدلة الحادث المحفوظة لا تطابق الملاحظة الحالية للمصدر.",
    "A recorded schema contract and reporting stage are required.":
      "يلزم وجود عقد مخطط ومرحلة تقارير مسجلين.",
    "Complete, consistent source lineage is required.":
      "يلزم تتبّع مصدري كامل ومتسق.",
    "Replay audit not found": "لم يُعثر على سجل التدقيق",
    "Incident not found": "لم يُعثر على الحادث",
    "Pipeline run not found": "لم يُعثر على التشغيل",
  };
  return (
    known[value] ??
    dictionary[value] ??
    (/Request failed \(HTTP (\d+)\)/.test(value)
      ? value.replace(
          /Request failed \(HTTP (\d+)\)\./,
          "تعذّر الطلب (HTTP $1).",
        )
      : `تعذّر إتمام الطلب. التفاصيل التقنية الأصلية: ${value}`)
  );
}

export function translateSelection(value: string): string {
  const match =
    /^(\d+) evenly spaced worksheet data rows, including first\/last; original row order$/.exec(
      value,
    );
  return language === "ar" && match
    ? `${match[1]} صف موزع بالتساوي على ورقة المصدر، يشمل أول صف وآخر صف مع حفظ ترتيب الصفوف.`
    : value;
}
