#!/usr/bin/env node
/** 用 @oai/artifact-tool 生成两份专家审核工作簿及逐 sheet 预览。 */

import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const argv = process.argv.slice(2);
const option = (name, fallback) => {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : fallback;
};
const root = path.resolve(option("--root", path.join(import.meta.dirname, "..")));
const candidatesPath = path.resolve(option("--candidates", path.join(root, "docs/oncology/authoring/oncology_authoring_candidates.json")));
const regimenPath = path.resolve(option("--regimens", path.join(root, "docs/oncology/authoring/regimen_authoring_payload.json")));
const outputDir = path.resolve(option("--output-dir", path.join(root, "outputs/add-oncology-kb-authoring")));
const previewDir = path.join(outputDir, "previews");
const generatedAt = option("--generated-at", "2026-07-21T00:00:00Z");
const templateVersion = "1.0.0";

const readJson = async (filename) => JSON.parse(await fs.readFile(filename, "utf8"));
const stableValue = (value) => {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]));
  }
  return value;
};
const stableJson = (value) => JSON.stringify(stableValue(value));
const digest = (value) => `sha256:${crypto.createHash("sha256").update(typeof value === "string" ? value : stableJson(value)).digest("hex")}`;
const batchId = (kind, snapshot) => `batch_${crypto.createHash("sha256").update(`${kind}\u001f${templateVersion}\u001f${snapshot}`).digest("hex").slice(0, 24)}`;
const asText = (value) => {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value) || typeof value === "object") return stableJson(value);
  return value;
};
const noneIfBlank = (value) => value === null || value === undefined || value === "" ? null : value;
const jsonText = (value) => {
  if (typeof value === "string") {
    try { return stableJson(JSON.parse(value)); } catch { return stableJson(value); }
  }
  return stableJson(value);
};
const typedChecksum = (values, excluded = []) => digest(Object.fromEntries(Object.entries(values).filter(([key]) => key !== "content_checksum" && !excluded.includes(key))));
const branchTypedChecksum = (item) => typedChecksum({
  branch_id: String(item.branch_id),
  rule_revision_id: String(item.rule_revision_id),
  source_fragment_id: String(item.source_fragment_id),
  ordinal_no: Number(item.ordinal),
  source_text: String(item.source_text),
  source_span_start: Number(item.source_span_start),
  source_span_end: Number(item.source_span_end),
  disposition: String(item.disposition),
}, ["disposition"]);
const nodeTypedChecksum = (item) => typedChecksum({
  node_id: String(item.node_id),
  branch_id: String(item.branch_id),
  parent_node_id: noneIfBlank(item.parent_node_id),
  sibling_order: Number(item.sibling_order),
  node_kind: String(item.node_kind),
  criterion_type: noneIfBlank(item.criterion_type),
  operator: noneIfBlank(item.operator),
  target_kind: noneIfBlank(item.target_kind),
  target_id: noneIfBlank(item.target_id),
  expected_value_json: item.expected_value === null || item.expected_value === undefined || item.expected_value === "" ? null : jsonText(item.expected_value),
  combination_requirement: noneIfBlank(item.combination_requirement),
  source_fragment_id: String(item.source_fragment_id),
  source_span_start: Number(item.source_span_start),
  source_span_end: Number(item.source_span_end),
  disposition: String(item.disposition),
}, ["disposition"]);
const aliasTypedChecksum = (item) => typedChecksum({
  alias_id: String(item.alias_id),
  regimen_revision_id: noneIfBlank(item.regimen_revision_id),
  original_alias: String(item.original_alias),
  normalized_alias: String(item.normalized_alias),
  alias_type: String(item.alias_type),
  language_code: String(item.language || "und"),
  aggregate_frequency: item.aggregate_frequency === null || item.aggregate_frequency === undefined || item.aggregate_frequency === "" ? null : Number(item.aggregate_frequency),
  source_corpus_checksum: noneIfBlank(item.source_corpus_checksum),
  review_status: String(item.review_status || "needs_review"),
  is_ambiguous: Boolean(item.ambiguous),
}, ["review_status"]);
const contextTypedChecksum = (item) => typedChecksum({
  context_id: String(item.context_id),
  regimen_revision_id: String(item.regimen_revision_id),
  cancer_context: String(item.cancer_context),
  histology: noneIfBlank(item.histology),
  clinical_setting: noneIfBlank(item.clinical_setting),
});
const componentTypedChecksum = (item) => typedChecksum({
  component_id: String(item.component_id),
  regimen_revision_id: String(item.regimen_revision_id),
  target_kind: String(item.target_kind),
  target_id: String(item.target_id),
  token: String(item.token),
  component_role: String(item.component_role),
  requirement: String(item.requirement),
  sibling_order: Number(item.sibling_order),
  source_fragment_id: noneIfBlank(item.source_reference),
});
const drugClassTypedChecksum = (item) => typedChecksum({
  drug_class_id: String(item.drug_class_id),
  canonical_name: String(item.canonical_name),
  match_terms_json: stableJson([...(item.match_terms ?? [])].map(String).sort()),
  lifecycle: String(item.lifecycle || "DRAFT"),
}, ["lifecycle"]);
const excelColumn = (index) => {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
};

const COLORS = {
  title: "#17365D",
  header: "#1F4E78",
  machine: "#D9EAF7",
  expert: "#FFF2CC",
  qa: "#FCE4D6",
  white: "#FFFFFF",
  border: "#B4C6E7",
};

function rowChecksum(row, expertColumns = []) {
  const machine = Object.fromEntries(Object.entries(row).filter(([key]) => !expertColumns.includes(key) && key !== "row_checksum").map(([key, value]) => [key, asText(value)]));
  return digest(stableJson(machine));
}

function withRowChecksums(rows, expertColumns = []) {
  return rows.map((source) => {
    const row = { ...source };
    if (!row.row_checksum) row.row_checksum = rowChecksum(row, expertColumns);
    return row;
  });
}

function requireSha256(value, label) {
  if (!/^sha256:[0-9a-f]{64}$/.test(String(value ?? ""))) {
    throw new Error(`${label} 缺少 canonical typed content checksum`);
  }
  return value;
}

function addStructuredSheet(workbook, name, columns, sourceRows, options = {}) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const expertColumns = options.expertColumns ?? [];
  const rows = sourceRows.map((source) => {
    const row = { ...source };
    if (columns.includes("row_checksum") && !row.row_checksum) row.row_checksum = rowChecksum(row, expertColumns);
    return columns.map((column) => asText(row[column]));
  });
  sheet.getRangeByIndexes(0, 0, 1, columns.length).values = [columns];
  if (rows.length) sheet.getRangeByIndexes(1, 0, rows.length, columns.length).values = rows;
  const header = sheet.getRangeByIndexes(0, 0, 1, columns.length);
  header.format = {
    fill: COLORS.header,
    font: { bold: true, color: COLORS.white },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: COLORS.border },
  };
  header.format.rowHeight = 34;
  if (rows.length) {
    const body = sheet.getRangeByIndexes(1, 0, rows.length, columns.length);
    body.format = { verticalAlignment: "top", wrapText: true };
    for (let index = 0; index < columns.length; index += 1) {
      const fill = expertColumns.includes(columns[index]) ? COLORS.expert : COLORS.machine;
      sheet.getRangeByIndexes(1, index, rows.length, 1).format.fill = fill;
    }
    const table = sheet.tables.add(`A1:${excelColumn(columns.length - 1)}${rows.length + 1}`, true, options.tableName);
    table.style = "TableStyleMedium2";
    table.showFilterButton = true;
  }
  sheet.freezePanes.freezeRows(1);
  if (options.freezeColumns) sheet.freezePanes.freezeColumns(options.freezeColumns);
  const widths = options.widths ?? {};
  for (let index = 0; index < columns.length; index += 1) {
    const columnName = columns[index];
    const width = widths[columnName] ?? (expertColumns.includes(columnName) ? 22 : 18);
    sheet.getRange(`${excelColumn(index)}:${excelColumn(index)}`).format.columnWidth = Math.min(width, 48);
  }
  const decisionIndex = columns.indexOf("review_decision");
  if (decisionIndex >= 0) {
    sheet.getRangeByIndexes(1, decisionIndex, Math.max(rows.length, 1), 1).dataValidation = {
      rule: { type: "list", values: ["APPROVE", "APPROVE_WITH_EDIT", "REJECT", "UNABLE_TO_DETERMINE"] },
    };
  }
  return sheet;
}

function addInstructions(workbook, title, lines) {
  const sheet = workbook.worksheets.add("00_使用说明");
  sheet.showGridLines = false;
  sheet.getRange("A1:F1").merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange("A1:F1").format = { fill: COLORS.title, font: { bold: true, color: COLORS.white, size: 16 }, verticalAlignment: "center" };
  sheet.getRange("A1:F1").format.rowHeight = 34;
  const rows = lines.map((line, index) => [index + 1, line]);
  sheet.getRangeByIndexes(2, 0, rows.length, 2).values = rows;
  sheet.getRangeByIndexes(2, 0, rows.length, 1).format = { fill: COLORS.machine, font: { bold: true }, horizontalAlignment: "center" };
  sheet.getRangeByIndexes(2, 1, rows.length, 1).format = { wrapText: true, verticalAlignment: "top" };
  sheet.getRange("A:A").format.columnWidth = 8;
  sheet.getRange("B:B").format.columnWidth = 90;
  sheet.freezePanes.freezeRows(2);
  return sheet;
}

function addMetadata(workbook, rows) {
  const sheet = workbook.worksheets.add("01_批次元数据");
  sheet.showGridLines = false;
  sheet.getRange("A1:B1").values = [["字段", "值"]];
  sheet.getRangeByIndexes(1, 0, rows.length, 2).values = rows;
  sheet.getRange("A1:B1").format = { fill: COLORS.header, font: { bold: true, color: COLORS.white } };
  sheet.getRangeByIndexes(1, 0, rows.length, 1).format = { fill: COLORS.machine, font: { bold: true } };
  sheet.getRangeByIndexes(1, 1, rows.length, 1).format = { fill: COLORS.expert, wrapText: true };
  sheet.getRange("A:A").format.columnWidth = 34;
  sheet.getRange("B:B").format.columnWidth = 72;
  sheet.freezePanes.freezeRows(1);
  return sheet;
}

async function renderWorkbook(workbook, prefix) {
  const overview = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 10000 });
  console.log(overview.ndjson);
  for (const sheet of workbook.worksheets.items) {
    const used = sheet.getUsedRange(true);
    const maxRows = Math.min(30, Math.max(1, used?.rowCount ?? 30));
    const maxCols = Math.min(12, Math.max(2, used?.columnCount ?? 8));
    const preview = await workbook.render({
      sheetName: sheet.name,
      range: `A1:${excelColumn(maxCols - 1)}${maxRows}`,
      scale: 1,
      format: "png",
    });
    const safe = sheet.name.replace(/[^\p{L}\p{N}_-]+/gu, "_");
    await fs.writeFile(path.join(previewDir, `${prefix}_${safe}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: `${prefix} formula error scan`,
  });
  console.log(errors.ndjson);
}

async function buildEligibility(data) {
  const workbook = Workbook.create();
  const authoringSnapshotChecksum = data.authoring_snapshot_checksum ?? data.snapshot_checksum;
  addInstructions(workbook, "肿瘤药指南适应证与医保限定条件树 KB", [
    "本工作簿是专家审核投影，不是知识真相源；禁止删除、改名或覆盖机器列。",
    "GUIDELINE_INDICATION 表示临床应用指导原则，不是法定药品说明书；当前不得填写 NMPA_LABEL。",
    "黄色列允许专家填写；选择 APPROVE_WITH_EDIT 时同时填写 expert_value、expert_comment、evidence_reference、reviewer_id 和 reviewed_at。",
    "默认有效期为 2026-01-01 至 2027-12-31。修改日期必须选择 EXPERT_OVERRIDE，并填写覆盖原因与日期审核意见。",
    "完成后先运行 oncology-kb validate；任何错误都会阻断整批 materialize。",
    "工作簿不得包含患者号、原始病历、run ID、ownership ID、数据库凭据或客户端绝对路径。",
  ]);
  const sourceCounts = Object.fromEntries(data.source_documents.map((item) => [item.source_type, data.source_rules.filter((rule) => rule.policy_scope === item.source_type).length]));
  addMetadata(workbook, [
    ["template_schema_version", templateVersion],
    ["export_batch_id", batchId("eligibility", authoringSnapshotChecksum)],
    ["source_snapshot_checksum", authoringSnapshotChecksum],
    ["candidate_snapshot_checksum", data.snapshot_checksum],
    ["curated_manifest_checksum", data.curated_manifest_checksum ?? ""],
    ["generated_at", generatedAt],
    ["default_effective_from", "2026-01-01"],
    ["default_effective_to", "2027-12-31"],
    ["effective_date_basis", "CURRENT_FILE_ASSUMPTION"],
    ["historical_application_policy", "APPLY_CURRENT_RELEASE_WITH_WARNING"],
    ["source_rule_counts", JSON.stringify(sourceCounts)],
    ["branch_count", data.branches.length],
    ["condition_node_count", data.condition_nodes.length],
    ["product_count", data.drug_products.length],
    ["row_checksum_policy", "sha256(canonical machine columns)"],
  ]);
  const expert = ["review_decision", "expert_value", "expert_comment", "evidence_reference", "reviewer_id", "reviewed_at"];
  const effectiveExpert = ["expert_effective_from", "expert_effective_to", "expert_effective_date_basis", "expert_date_override_reason", "expert_date_review_comment"];
  const curatedExpert = ["expert_migration_status", "expert_target_kind", "expert_target_id", "expert_verification_evidence"];
  const concepts = Object.fromEntries(data.drug_concepts.map((item) => [item.drug_concept_id, item]));
  addStructuredSheet(workbook, "02_药品产品", ["drug_product_id", "drug_concept_id", "canonical_name", "normalized_name", "concept_lifecycle", "product_name", "dosage_form", "manufacturer", "insurance_code", "hospital_code", "source_kind", "row_checksum", ...expert], data.drug_products.map((item) => ({ ...item, canonical_name: concepts[item.drug_concept_id]?.canonical_name ?? "", normalized_name: concepts[item.drug_concept_id]?.normalized_name ?? "", concept_lifecycle: "DRAFT" })), { tableName: "EligibilityProducts", expertColumns: expert, widths: { canonical_name: 28, product_name: 34, manufacturer: 26, row_checksum: 28 }, freezeColumns: 2 });
  const documents = Object.fromEntries(data.source_documents.map((item) => [item.source_document_id, item]));
  addStructuredSheet(workbook, "03_来源原文", ["source_fragment_id", "source_document_id", "source_type", "title", "document_version", "document_year", "retrieval_date", "document_checksum", "anchor", "page_numbers", "original_text", "content_checksum", "row_checksum", ...expert], data.source_fragments.map((item) => ({ ...item, source_type: documents[item.source_document_id]?.source_type ?? "", title: documents[item.source_document_id]?.title ?? "", document_version: documents[item.source_document_id]?.document_version ?? "", document_year: documents[item.source_document_id]?.document_year ?? "", retrieval_date: documents[item.source_document_id]?.retrieval_date ?? "", document_checksum: documents[item.source_document_id]?.content_checksum ?? "" })), { tableName: "EligibilitySources", expertColumns: expert, widths: { title: 34, anchor: 34, original_text: 48, content_checksum: 28, row_checksum: 28 }, freezeColumns: 2 });
  const rules = Object.fromEntries(data.source_rules.map((item) => [item.revision_id, item]));
  const branchExpert = ["date_override_reason", "date_review_comment", ...expert];
  const branchRows = withRowChecksums(data.branches.map((item) => { const rule = rules[item.rule_revision_id] ?? {}; return ({ ...item, logical_rule_id: rule.logical_rule_id ?? "", drug_concept_id: rule.drug_concept_id ?? "", policy_scope: rule.policy_scope ?? "", effective_from: rule.effective_window?.effective_from ?? "2026-01-01", effective_to: rule.effective_window?.effective_to ?? "2027-12-31", effective_date_basis: rule.effective_window?.effective_date_basis ?? "CURRENT_FILE_ASSUMPTION", date_override_reason: rule.effective_window?.date_override_reason ?? "", date_review_comment: rule.effective_window?.date_review_comment ?? "", historical_application_policy: rule.effective_window?.historical_application_policy ?? "APPLY_CURRENT_RELEASE_WITH_WARNING", lifecycle: rule.lifecycle ?? "DRAFT", revision_content_checksum: digest(rule) }); }), branchExpert);
  addStructuredSheet(workbook, "04_适应证分支", ["branch_id", "rule_revision_id", "logical_rule_id", "drug_concept_id", "policy_scope", "source_fragment_id", "ordinal", "source_text", "source_span_start", "source_span_end", "disposition", "effective_from", "effective_to", "effective_date_basis", "date_override_reason", "date_review_comment", "historical_application_policy", "lifecycle", "revision_content_checksum", "row_checksum", ...expert], branchRows, { tableName: "EligibilityBranches", expertColumns: branchExpert, widths: { source_text: 48, row_checksum: 28 }, freezeColumns: 2 });
  const nodeRows = withRowChecksums(data.condition_nodes, expert);
  addStructuredSheet(workbook, "05_条件节点", ["node_id", "branch_id", "parent_node_id", "sibling_order", "node_kind", "criterion_type", "operator", "target_kind", "target_id", "expected_value", "combination_requirement", "source_fragment_id", "source_span_start", "source_span_end", "disposition", "row_checksum", ...expert], nodeRows, { tableName: "EligibilityNodes", expertColumns: expert, widths: { expected_value: 34, row_checksum: 28 }, freezeColumns: 2 });
  const drugClassAuthority = new Map((data.drug_classes ?? []).map((item) => [item.drug_class_id, { drug_class_id: item.drug_class_id, canonical_name: item.canonical_name, match_terms: [...(item.match_terms ?? [])], lifecycle: item.lifecycle ?? "DRAFT" }]));
  for (const node of nodeRows.filter((item) => item.target_kind === "CLASS" && item.target_id)) {
    const term = String(node.expected_value?.display_name ?? "").trim();
    if (!term) throw new Error(`condition CLASS target ${node.target_id} 缺少可审核 match term`);
    const existing = drugClassAuthority.get(node.target_id);
    if (existing) existing.match_terms.push(term);
    else drugClassAuthority.set(node.target_id, { drug_class_id: node.target_id, canonical_name: term, match_terms: [term], lifecycle: "DRAFT" });
  }
  const drugClassRows = [...drugClassAuthority.values()].sort((left, right) => left.drug_class_id.localeCompare(right.drug_class_id)).map((item) => ({ dictionary: "drug_class", value: item.drug_class_id, description: item.canonical_name, canonical_name: item.canonical_name, match_terms: [...new Set(item.match_terms.map(String).filter(Boolean))].sort(), lifecycle: item.lifecycle ?? "DRAFT" }));
  const reviewRows = [
    ...[...new Map(branchRows.map((item) => [item.rule_revision_id, item])).values()].map((item) => ({ entity_type: "eligibility_revision", entity_id: item.rule_revision_id, field_name: "effective_window", source_checksum: item.revision_content_checksum, review_decision: "", expert_value: "", expert_comment: "", evidence_reference: "", reviewer_id: "", reviewed_at: "" })),
    ...branchRows.map((item) => ({ entity_type: "branch", entity_id: item.branch_id, field_name: "branch", source_checksum: branchTypedChecksum(item), review_decision: "", expert_value: "", expert_comment: "", evidence_reference: "", reviewer_id: "", reviewed_at: "" })),
    ...nodeRows.map((item) => ({ entity_type: "condition_node", entity_id: item.node_id, field_name: "condition", source_checksum: nodeTypedChecksum(item), review_decision: "", expert_value: "", expert_comment: "", evidence_reference: "", reviewer_id: "", reviewed_at: "" })),
    ...drugClassRows.map((item) => ({ entity_type: "drug_class", entity_id: item.value, field_name: "drug_class", source_checksum: drugClassTypedChecksum({ drug_class_id: item.value, canonical_name: item.canonical_name, match_terms: item.match_terms, lifecycle: item.lifecycle }), review_decision: "", expert_value: "", expert_comment: "", evidence_reference: "", reviewer_id: "", reviewed_at: "" })),
  ];
  addStructuredSheet(workbook, "06_专家审核", ["entity_type", "entity_id", "field_name", "source_checksum", ...effectiveExpert, ...expert], reviewRows, { tableName: "EligibilityReviews", expertColumns: [...effectiveExpert, ...expert], widths: { entity_id: 28, source_checksum: 28, expert_value: 34, expert_comment: 40 }, freezeColumns: 2 });
  const dictionaryRows = [
    ...["INSURANCE_PAYMENT", "GUIDELINE_INDICATION", "NMPA_LABEL"].map((value) => ({ dictionary: "source_type", value, description: value === "NMPA_LABEL" ? "预留，当前不得使用" : "当前允许来源" })),
    ...["APPROVE", "APPROVE_WITH_EDIT", "REJECT", "UNABLE_TO_DETERMINE"].map((value) => ({ dictionary: "review_decision", value, description: "结构化专家决定" })),
    ...["ALL", "ANY", "LEAF"].map((value) => ({ dictionary: "node_kind", value, description: "条件树节点" })),
    ...["diagnosis", "histology", "stage", "disease_status", "resectability", "biomarker", "age", "sex", "menopausal_status", "prior_therapy", "therapy_count", "line_of_therapy", "treatment_status", "combination_requirement", "surgery_status", "radiotherapy_status", "transplant_eligibility", "time_window", "clinician_assessment", "unsupported"].map((value) => ({ dictionary: "criterion_type", value, description: value === "unsupported" ? "仅可待审，不得发布" : "一期类型化条件" })),
    ...["EQUALS", "NOT_EQUALS", "IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS", "EXISTS", "NOT_EXISTS", "GTE", "LTE", "WITHIN_DAYS"].map((value) => ({ dictionary: "operator", value, description: "条件操作符" })),
    ...["CONCEPT", "CLASS", "REGIMEN", "VALUE"].map((value) => ({ dictionary: "target_kind", value, description: "条件目标类型" })),
    ...["DRAFT", "IN_REVIEW", "CHANGES_REQUESTED", "APPROVED", "RELEASED", "REJECTED", "RETIRED"].map((value) => ({ dictionary: "revision_lifecycle", value, description: "revision 生命周期" })),
    ...["approved", "in_review", "rejected", "unsupported"].map((value) => ({ dictionary: "candidate_disposition", value, description: "候选覆盖分区" })),
    ...["REQUIRED", "OPTIONAL", "WITH_OR_WITHOUT"].map((value) => ({ dictionary: "combination_requirement", value, description: "联合用药必选性" })),
    ...data.drug_concepts.map((item) => ({ dictionary: "drug_concept", value: item.drug_concept_id, description: item.canonical_name })),
    ...drugClassRows,
  ];
  const normalizedDictionaryRows = dictionaryRows.map((item) => ({
    dictionary: item.dictionary ?? "",
    value: item.value ?? "",
    description: item.description ?? "",
    canonical_name: item.canonical_name ?? "",
    match_terms: item.match_terms ?? "",
    lifecycle: item.lifecycle ?? "",
  }));
  addStructuredSheet(workbook, "07_术语字典", ["dictionary", "value", "description", "canonical_name", "match_terms", "lifecycle", "row_checksum", ...expert], normalizedDictionaryRows, { tableName: "EligibilityDictionary", expertColumns: expert, widths: { value: 32, description: 48, match_terms: 40, row_checksum: 28 } });
  const pathologyByNode = new Map();
  for (const link of data.pathology_links) {
    const current = pathologyByNode.get(link.condition_node_id) ?? { marker: link.marker, links: 0, approvedLinks: 0, knownThresholdLinks: 0 };
    current.links += 1;
    current.approvedLinks += link.approved_assertion ? 1 : 0;
    current.knownThresholdLinks += link.threshold_known ? 1 : 0;
    pathologyByNode.set(link.condition_node_id, current);
  }
  const unresolvedCombinationNodes = nodeRows.filter((item) => item.criterion_type === "combination_requirement" && item.target_kind === "CONCEPT" && !concepts[item.target_id]);
  const classTargetNodes = nodeRows.filter((item) => item.criterion_type === "combination_requirement" && item.target_kind === "CLASS");
  const qaRows = [
    ...Object.entries(data.qa.source_rule_coverage.counts).map(([key, value]) => ({ qa_code: `SOURCE_PARTITION_${key.toUpperCase()}`, entity_id: "", severity: key === "approved" ? "info" : "review", detail: String(value) })),
    ...data.qa.tree_errors.map((item) => ({ qa_code: "TREE_ERROR", entity_id: item.branch_id, severity: "error", detail: JSON.stringify(item.errors) })),
    { qa_code: "DRAFT_RULE_REVISIONS", entity_id: "", severity: "review", detail: String(data.qa.release_readiness?.draft_rule_revisions ?? data.source_rules.length) },
    { qa_code: "IN_REVIEW_BRANCHES", entity_id: "", severity: "review", detail: String(data.qa.release_readiness?.in_review_branches ?? data.branches.length) },
    ...unresolvedCombinationNodes.map((item) => ({ qa_code: "COMBINATION_CONCEPT_TARGET_UNRESOLVED", entity_id: item.node_id, severity: "review", detail: String(item.expected_value?.display_name ?? item.target_id) })),
    ...[...new Map(classTargetNodes.map((item) => [item.target_id, item])).values()].map((item) => ({ qa_code: "DRUG_CLASS_REQUIRES_EXPERT_APPROVAL", entity_id: item.target_id, severity: "review", detail: String(item.expected_value?.display_name ?? item.target_id) })),
    ...[...pathologyByNode.entries()].map(([nodeId, item]) => ({ qa_code: "PATHOLOGY_CONTEXT_REQUIRES_EXPERT_CONFIRMATION", entity_id: nodeId, severity: "review", detail: `${item.marker}|links=${item.links}|approved_links=${item.approvedLinks}|known_threshold_links=${item.knownThresholdLinks}` })),
    ...(data.curated_atoms ?? []).filter((item) => item.oncology && item.migration_status !== "VERIFIED").map((item) => ({ qa_code: "ONCOLOGY_CURATED_ATOM_NOT_VERIFIED", entity_id: item.atom_id, severity: "review", detail: `${item.source_rule_id}|${item.atom_type}|${item.migration_status}` })),
  ];
  addStructuredSheet(workbook, "08_QA问题", ["qa_code", "entity_id", "severity", "detail"], qaRows, { tableName: "EligibilityQA", widths: { qa_code: 32, entity_id: 30, detail: 48 } });
  const curatedRows = (data.curated_atoms ?? []).map((item) => ({ ...item, expert_migration_status: "", expert_target_kind: "", expert_target_id: "", expert_verification_evidence: "" }));
  addStructuredSheet(workbook, "09_肿瘤知识保全", ["atom_id", "source_rule_id", "oncology", "rule_status", "source_field_or_test", "atom_type", "canonical_payload", "source_checksum", "migration_status", "target_kind", "target_id", "verification_evidence", "row_checksum", ...curatedExpert, ...expert], curatedRows, { tableName: "EligibilityPreservation", expertColumns: [...curatedExpert, ...expert], widths: { oncology: 12, rule_status: 14, canonical_payload: 48, source_checksum: 28, verification_evidence: 36 }, freezeColumns: 2 });
  return workbook;
}

async function buildRegimens(data) {
  const workbook = Workbook.create();
  addInstructions(workbook, "肿瘤治疗方案组成 KB", [
    "一期只维护方案规范名、别名、癌种上下文和组成药品/药物类别；剂量与给药日程字段不参与发布或推理。",
    "黄色列允许专家填写；机器 ID、来源 checksum、聚合频次和 row checksum 不得修改。",
    "同一归一别名映射多个方案且上下文不能消歧时必须保持 ambiguous，不得自动选择方案或组分。",
    "挖掘候选仅保留归一别名、聚合频次、允许的上下文摘要和 corpus checksum；禁止填入患者号或原始病历。",
    "完成后先运行 oncology-kb validate；上传、批准、发布和部署是彼此独立的操作。",
  ]);
  addMetadata(workbook, [
    ["template_schema_version", templateVersion],
    ["export_batch_id", batchId("regimen", data.checksum)],
    ["source_snapshot_checksum", data.checksum],
    ["generated_at", generatedAt],
    ["approved_regimen_count", data.revisions.filter((item) => item.lifecycle === "APPROVED").length],
    ["alias_candidate_count", data.aliases.filter((item) => item.review_status === "needs_review").length],
    ["publication_scope", "identity+alias+context+composition only"],
    ["schedule_fields_enabled", false],
  ]);
  const expert = ["review_decision", "expert_value", "expert_comment", "evidence_reference", "reviewer_id", "reviewed_at"];
  const effectiveExpert = ["expert_effective_from", "expert_effective_to", "expert_effective_date_basis", "expert_date_override_reason", "expert_date_review_comment"];
  const revisionContentChecksums = new Map(data.revisions.map((item) => [
    item.regimen_revision_id,
    requireSha256(item.content_checksum, `regimen ${item.regimen_revision_id}`),
  ]));
  const revisionRows = withRowChecksums(data.revisions.map(({ content_checksum: _contentChecksum, ...item }) => item), expert);
  const aliasRows = withRowChecksums(data.aliases, expert);
  const contextRows = withRowChecksums(data.contexts, expert);
  const componentRows = withRowChecksums(data.components, expert);
  addStructuredSheet(workbook, "02_方案主表", ["logical_regimen_id", "regimen_revision_id", "canonical_name", "lifecycle", "source_refs", "effective_window", "supersedes_revision_id", "row_checksum", ...expert], revisionRows, { tableName: "RegimenMaster", expertColumns: expert, widths: { canonical_name: 30, source_refs: 42, effective_window: 40, row_checksum: 28 }, freezeColumns: 2 });
  addStructuredSheet(workbook, "03_方案别名", ["alias_id", "regimen_revision_id", "original_alias", "normalized_alias", "alias_type", "language", "aggregate_frequency", "source_corpus_checksum", "review_status", "ambiguous", "row_checksum", ...expert], aliasRows, { tableName: "RegimenAliases", expertColumns: expert, widths: { original_alias: 30, normalized_alias: 30, source_corpus_checksum: 28, row_checksum: 28 }, freezeColumns: 2 });
  addStructuredSheet(workbook, "04_方案上下文", ["context_id", "regimen_revision_id", "cancer_context", "histology", "clinical_setting", "row_checksum", ...expert], contextRows, { tableName: "RegimenContexts", expertColumns: expert, widths: { cancer_context: 34, row_checksum: 28 }, freezeColumns: 2 });
  addStructuredSheet(workbook, "05_方案组分", ["component_id", "regimen_revision_id", "target_kind", "target_id", "token", "component_role", "requirement", "sibling_order", "source_reference", "row_checksum", ...expert], componentRows, { tableName: "RegimenComponents", expertColumns: expert, widths: { target_id: 30, source_reference: 36, row_checksum: 28 }, freezeColumns: 2 });
  addStructuredSheet(workbook, "06_预留给药字段", ["schedule_component_id", "regimen_revision_id", "component_id", "dose_value", "dose_unit", "dose_basis", "route", "administration_days", "cycle_length_days", "max_cycles", "treatment_phase", "sequence_no", "publishing_enabled", "inference_enabled", "row_checksum", ...expert], data.schedules, { tableName: "RegimenSchedules", expertColumns: ["dose_value", "dose_unit", "dose_basis", "route", "administration_days", "cycle_length_days", "max_cycles", "treatment_phase", "sequence_no", ...expert], widths: { row_checksum: 28 }, freezeColumns: 3 });
  const reviewRows = [
    ...revisionRows.map((item) => ({ entity_type: "regimen", entity_id: item.regimen_revision_id, field_name: "revision", source_checksum: revisionContentChecksums.get(item.regimen_revision_id) })),
    ...aliasRows.map((item) => ({ entity_type: "alias", entity_id: item.alias_id, field_name: "alias", source_checksum: aliasTypedChecksum(item) })),
    ...contextRows.map((item) => ({ entity_type: "context", entity_id: item.context_id, field_name: "context", source_checksum: contextTypedChecksum(item) })),
    ...componentRows.map((item) => ({ entity_type: "component", entity_id: item.component_id, field_name: "component", source_checksum: componentTypedChecksum(item) })),
  ].map((item) => ({ ...item, review_decision: "", expert_value: "", expert_comment: "", evidence_reference: "", reviewer_id: "", reviewed_at: "" }));
  addStructuredSheet(workbook, "07_专家审核", ["entity_type", "entity_id", "field_name", "source_checksum", ...effectiveExpert, ...expert], reviewRows, { tableName: "RegimenReviews", expertColumns: [...effectiveExpert, ...expert], widths: { entity_id: 30, source_checksum: 28, expert_value: 34, expert_comment: 40 }, freezeColumns: 2 });
  const aliasGroups = new Map();
  for (const alias of data.aliases) {
    if (!alias.regimen_revision_id) continue;
    const values = aliasGroups.get(alias.normalized_alias) ?? new Set();
    values.add(alias.regimen_revision_id);
    aliasGroups.set(alias.normalized_alias, values);
  }
  const qaRows = [...aliasGroups.entries()].filter(([, revisions]) => revisions.size > 1).map(([alias, revisions]) => ({ qa_code: "AMBIGUOUS_ALIAS", entity_id: alias, severity: "error", detail: JSON.stringify([...revisions].sort()) }));
  addStructuredSheet(workbook, "08_QA问题", ["qa_code", "entity_id", "severity", "detail"], qaRows.length ? qaRows : [{ qa_code: "NO_BLOCKING_ALIAS_AMBIGUITY", entity_id: "", severity: "info", detail: "当前 approved 基线无不可消歧别名" }], { tableName: "RegimenQA", widths: { entity_id: 32, detail: 48 } });
  return workbook;
}

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const candidates = await readJson(candidatesPath);
const preservationPath = path.join(root, "docs/oncology/authoring/curated_knowledge_manifest.json");
const preservation = await readJson(preservationPath);
candidates.curated_atoms = preservation.atoms;
candidates.curated_manifest_checksum = preservation.manifest_checksum;
candidates.authoring_snapshot_checksum = digest({
  candidate_snapshot_checksum: candidates.snapshot_checksum,
  curated_manifest_checksum: preservation.manifest_checksum,
});
const regimens = await readJson(regimenPath);
candidates.drug_classes = regimens.drug_classes ?? [];

const eligibilityWorkbook = await buildEligibility(candidates);
await renderWorkbook(eligibilityWorkbook, "eligibility");
const eligibilityOutput = await SpreadsheetFile.exportXlsx(eligibilityWorkbook);
await eligibilityOutput.save(path.join(outputDir, "肿瘤药指南适应证与医保限定条件树KB.xlsx"));

const regimenWorkbook = await buildRegimens(regimens);
await renderWorkbook(regimenWorkbook, "regimen");
const regimenOutput = await SpreadsheetFile.exportXlsx(regimenWorkbook);
await regimenOutput.save(path.join(outputDir, "肿瘤治疗方案组成KB.xlsx"));

console.log(`eligibility=${path.join(outputDir, "肿瘤药指南适应证与医保限定条件树KB.xlsx")}`);
console.log(`regimen=${path.join(outputDir, "肿瘤治疗方案组成KB.xlsx")}`);
