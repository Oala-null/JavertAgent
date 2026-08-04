"""专家工作簿保护、解析、本地校验与安全逐行诊断。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook
from openpyxl.styles import Protection

from .ids import canonical_json_bytes
from .lifecycle import validate_effective_window_overlaps


ELIGIBILITY_SHEETS = (
    "00_使用说明",
    "01_批次元数据",
    "02_药品产品",
    "03_来源原文",
    "04_适应证分支",
    "05_条件节点",
    "06_专家审核",
    "07_术语字典",
    "08_QA问题",
    "09_肿瘤知识保全",
)
REGIMEN_SHEETS = (
    "00_使用说明",
    "01_批次元数据",
    "02_方案主表",
    "03_方案别名",
    "04_方案上下文",
    "05_方案组分",
    "06_预留给药字段",
    "07_专家审核",
    "08_QA问题",
)
REQUIRED_COLUMNS = {
    "eligibility": {
        "02_药品产品": {"drug_product_id", "drug_concept_id", "canonical_name", "normalized_name", "concept_lifecycle", "row_checksum"},
        "03_来源原文": {"source_fragment_id", "source_document_id", "source_type", "document_version", "retrieval_date", "document_checksum", "original_text", "content_checksum", "row_checksum"},
        "04_适应证分支": {"branch_id", "rule_revision_id", "logical_rule_id", "drug_concept_id", "source_fragment_id", "effective_from", "effective_to", "effective_date_basis", "historical_application_policy", "lifecycle", "revision_content_checksum", "row_checksum"},
        "05_条件节点": {"node_id", "branch_id", "parent_node_id", "node_kind", "criterion_type", "operator", "source_fragment_id", "row_checksum"},
        "06_专家审核": {"entity_type", "entity_id", "field_name", "source_checksum", "review_decision", "reviewer_id", "reviewed_at"},
        "07_术语字典": {"dictionary", "value", "description", "canonical_name", "match_terms", "lifecycle", "row_checksum"},
        "09_肿瘤知识保全": {"atom_id", "source_rule_id", "oncology", "rule_status", "atom_type", "migration_status", "source_checksum", "row_checksum"},
    },
    "regimen": {
        "02_方案主表": {"logical_regimen_id", "regimen_revision_id", "canonical_name", "lifecycle", "row_checksum"},
        "03_方案别名": {"alias_id", "regimen_revision_id", "original_alias", "normalized_alias", "review_status", "row_checksum"},
        "04_方案上下文": {"context_id", "regimen_revision_id", "cancer_context", "row_checksum"},
        "05_方案组分": {"component_id", "regimen_revision_id", "target_kind", "target_id", "requirement", "row_checksum"},
        "06_预留给药字段": {"schedule_component_id", "regimen_revision_id", "component_id", "publishing_enabled", "inference_enabled", "row_checksum"},
        "07_专家审核": {"entity_type", "entity_id", "field_name", "source_checksum", "review_decision", "reviewer_id", "reviewed_at"},
    },
}
EXPERT_COLUMNS = {
    "review_decision",
    "expert_value",
    "expert_comment",
    "evidence_reference",
    "reviewer_id",
    "reviewed_at",
    "date_override_reason",
    "date_review_comment",
    "dose_value",
    "dose_unit",
    "dose_basis",
    "route",
    "administration_days",
    "cycle_length_days",
    "max_cycles",
    "treatment_phase",
    "sequence_no",
    "expert_effective_from",
    "expert_effective_to",
    "expert_effective_date_basis",
    "expert_date_override_reason",
    "expert_date_review_comment",
    "expert_migration_status",
    "expert_target_kind",
    "expert_target_id",
    "expert_verification_evidence",
}
REVIEW_DECISIONS = {
    "APPROVE",
    "APPROVE_WITH_EDIT",
    "REJECT",
    "UNABLE_TO_DETERMINE",
}
CURATED_RULE_STATUSES = {"ready", "drafting", "abandoned"}
_EXPERT_COLUMN_PATCHES = {
    "expert_effective_from": "effective_from",
    "expert_effective_to": "effective_to",
    "expert_effective_date_basis": "effective_date_basis",
    "expert_date_override_reason": "date_override_reason",
    "expert_date_review_comment": "date_review_comment",
    "expert_migration_status": "migration_status",
    "expert_target_kind": "target_kind",
    "expert_target_id": "target_id",
    "expert_verification_evidence": "verification_evidence",
}
_EDITABLE_FIELDS = {
    "eligibility_revision": frozenset(
        {
            "effective_from",
            "effective_to",
            "effective_date_basis",
            "date_override_reason",
            "date_review_comment",
        }
    ),
    "condition_node": frozenset(
        {
            "criterion_type",
            "operator",
            "target_kind",
            "target_id",
            "expected_value",
            "combination_requirement",
        }
    ),
    "regimen": frozenset(
        {
            "canonical_name",
            "effective_from",
            "effective_to",
            "effective_date_basis",
            "date_override_reason",
            "date_review_comment",
        }
    ),
    "alias": frozenset(
        {
            "original_alias",
            "normalized_alias",
            "alias_type",
            "language_code",
            "is_ambiguous",
        }
    ),
    "context": frozenset({"cancer_context", "histology", "clinical_setting"}),
    "component": frozenset(
        {
            "target_kind",
            "target_id",
            "token",
            "component_role",
            "requirement",
            "sibling_order",
        }
    ),
    "curated_knowledge_atom": frozenset(
        {"migration_status", "target_kind", "target_id", "verification_evidence"}
    ),
}
_SENSITIVE_RE = re.compile(
    r"(?i)(patient[_ -]?id|ownership[_ -]?id|run[_ -]?id|(?:PWD|PASSWORD)\s*=|"
    r"(?:jdbc|odbc|mssql)://|/Users/[^/\s]+/|\\Users\\[^\\\s]+\\|病历原文|住院号\s*[:：])"
)


@dataclass(frozen=True, order=True)
class ValidationIssue:
    safe_filename: str
    sheet: str
    excel_row: int
    stable_id: str
    error_code: str
    safe_detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "safe_filename": self.safe_filename,
            "sheet": self.sheet,
            "excel_row": self.excel_row,
            "stable_id": self.stable_id,
            "error_code": self.error_code,
            "safe_detail": self.safe_detail,
        }


def _safe_basename(path: Path) -> str:
    return re.sub(r"[^\w.()\-\u4e00-\u9fff]+", "_", path.name)


def _cell_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return "" if value is None else value


def _sheet_rows(sheet) -> list[dict[str, Any]]:
    headers = [str(cell.value or "").strip() for cell in sheet[1]]
    rows = []
    for row_number, cells in enumerate(sheet.iter_rows(min_row=2), start=2):
        values = [_cell_value(cell.value) for cell in cells[: len(headers)]]
        if not any(value != "" for value in values):
            continue
        rows.append({"_excel_row": row_number, **dict(zip(headers, values))})
    return rows


def parse_workbook(path: Path, *, kind: str) -> dict[str, Any]:
    """只按固定 sheet/列和稳定 ID 解析，不依赖行序、样式或公式缓存。"""
    workbook = load_workbook(path, data_only=False, read_only=False)
    expected = ELIGIBILITY_SHEETS if kind == "eligibility" else REGIMEN_SHEETS
    missing = [name for name in expected if name not in workbook.sheetnames]
    if missing:
        raise ValueError(f"缺少必需 sheets: {missing}")
    metadata = {
        str(row[0].value): _cell_value(row[1].value)
        for row in workbook["01_批次元数据"].iter_rows(min_row=2, max_col=2)
        if row[0].value
    }
    return {
        "kind": kind,
        "metadata": metadata,
        "sheets": {
            name: _sheet_rows(workbook[name])
            for name in expected
            if name not in {"00_使用说明", "01_批次元数据"}
        },
    }


def _row_checksum(row: dict[str, Any]) -> str:
    machine = {
        key: _cell_value(value)
        for key, value in row.items()
        if key not in EXPERT_COLUMNS and key not in {"row_checksum", "_excel_row"}
    }
    raw = json.dumps(machine, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _stable_id(row: dict[str, Any]) -> str:
    for key in (
        "node_id",
        "branch_id",
        "drug_product_id",
        "source_fragment_id",
        "atom_id",
        "alias_id",
        "context_id",
        "component_id",
        "schedule_component_id",
        "entity_id",
        "regimen_revision_id",
        "rule_revision_id",
    ):
        if row.get(key):
            return str(row[key])
    return ""


def _issue(path: Path, sheet: str, row: dict[str, Any], code: str, detail: str) -> ValidationIssue:
    return ValidationIssue(
        safe_filename=_safe_basename(path),
        sheet=sheet,
        excel_row=int(row.get("_excel_row") or 0),
        stable_id=_stable_id(row),
        error_code=code,
        safe_detail=detail,
    )


def _duplicate_issues(path: Path, sheet: str, rows: list[dict[str, Any]], key: str) -> list[ValidationIssue]:
    seen: dict[str, dict[str, Any]] = {}
    issues = []
    for row in rows:
        value = str(row.get(key) or "")
        if not value:
            issues.append(_issue(path, sheet, row, "STABLE_ID_MISSING", f"缺少 {key}"))
        elif value in seen:
            issues.append(_issue(path, sheet, row, "DUPLICATE_STABLE_ID", f"{key} 重复"))
        else:
            seen[value] = row
    return issues


def structured_expert_value(row: dict[str, Any]) -> dict[str, Any]:
    """读取受限结构化修订；禁止用自由文本绕过字段级权限。"""

    raw = row.get("expert_value")
    if raw in (None, ""):
        patch: dict[str, Any] = {}
    elif isinstance(raw, dict):
        patch = dict(raw)
    elif isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("expert_value 必须是 JSON object") from exc
        if not isinstance(decoded, dict):
            raise ValueError("expert_value 必须是 JSON object")
        patch = dict(decoded)
    else:
        raise ValueError("expert_value 必须是 JSON object")

    for column, field in _EXPERT_COLUMN_PATCHES.items():
        value = row.get(column)
        if value in (None, ""):
            continue
        if field in patch and patch[field] != value:
            raise ValueError(f"expert_value 与 {column} 冲突")
        patch[field] = value

    entity_type = str(row.get("entity_type") or "").strip()
    if not entity_type and row.get("atom_id"):
        entity_type = "curated_knowledge_atom"
    decision = str(row.get("review_decision") or "").strip()
    if not patch:
        if decision == "APPROVE_WITH_EDIT":
            raise ValueError("APPROVE_WITH_EDIT 必须提供结构化修订")
        return {}
    allowed = _EDITABLE_FIELDS.get(entity_type)
    if allowed is None or not set(patch).issubset(allowed):
        raise ValueError("expert_value 包含不允许修改的字段")
    if decision != "APPROVE_WITH_EDIT" and entity_type != "curated_knowledge_atom":
        raise ValueError("内容修订必须选择 APPROVE_WITH_EDIT")

    effective_fields = {
        "effective_from",
        "effective_to",
        "effective_date_basis",
        "date_override_reason",
        "date_review_comment",
    }
    if set(patch) & effective_fields:
        required = effective_fields
        if not required.issubset(patch):
            raise ValueError("有效期修订必须完整填写起止日、basis、原因和审核意见")
        try:
            start = date.fromisoformat(str(patch["effective_from"]))
            end = date.fromisoformat(str(patch["effective_to"]))
        except ValueError as exc:
            raise ValueError("有效期修订日期格式非法") from exc
        if start > end:
            raise ValueError("effective_from 不能晚于 effective_to")
        if (
            str(patch["effective_date_basis"]) != "EXPERT_OVERRIDE"
            or not str(patch["date_override_reason"]).strip()
            or not str(patch["date_review_comment"]).strip()
        ):
            raise ValueError("有效期修订必须使用 EXPERT_OVERRIDE 并填写原因和审核意见")

    if entity_type == "curated_knowledge_atom":
        status = str(patch.get("migration_status") or "")
        if status and status not in {"MAPPED", "VERIFIED"}:
            raise ValueError("精选知识专家状态仅允许 MAPPED/VERIFIED")
        if decision == "APPROVE_WITH_EDIT" and status == "VERIFIED":
            raise ValueError("修订批次不得直接把精选知识置为 VERIFIED")
    return patch


def _review_issues(path: Path, sheet: str, rows: Iterable[dict[str, Any]]) -> list[ValidationIssue]:
    issues = []
    for row in rows:
        decision = str(row.get("review_decision") or "").strip()
        if not decision:
            continue
        if decision not in REVIEW_DECISIONS:
            issues.append(_issue(path, sheet, row, "REVIEW_DECISION_INVALID", "审核决定不在支持枚举中"))
            continue
        required = ["reviewer_id", "reviewed_at", "expert_comment"]
        if decision == "APPROVE_WITH_EDIT":
            required.extend(["expert_value", "evidence_reference"])
        for field in required:
            if not str(row.get(field) or "").strip():
                issues.append(_issue(path, sheet, row, "REVIEW_FIELD_REQUIRED", f"审核决定缺少 {field}"))
        try:
            structured_expert_value(row)
        except ValueError:
            issues.append(
                _issue(
                    path,
                    sheet,
                    row,
                    "EXPERT_VALUE_INVALID",
                    "结构化修订字段、枚举或有效期规则非法",
                )
            )
    return issues


def _privacy_issues(path: Path, sheet: str, rows: Iterable[dict[str, Any]]) -> list[ValidationIssue]:
    issues = []
    for row in rows:
        for key, value in row.items():
            if key.startswith("_") or value in (None, ""):
                continue
            if _SENSITIVE_RE.search(str(value)):
                issues.append(_issue(path, sheet, row, "SENSITIVE_CONTENT", f"字段 {key} 命中隐私/凭据模式"))
    return issues


def _eligibility_issues(path: Path, parsed: dict[str, Any]) -> list[ValidationIssue]:
    sheets = parsed["sheets"]
    issues = []
    keys = {
        "02_药品产品": "drug_product_id",
        "03_来源原文": "source_fragment_id",
        "04_适应证分支": "branch_id",
        "05_条件节点": "node_id",
        "09_肿瘤知识保全": "atom_id",
    }
    for sheet, key in keys.items():
        issues.extend(_duplicate_issues(path, sheet, sheets[sheet], key))
    for row in sheets["09_肿瘤知识保全"]:
        if type(row.get("oncology")) is not bool:
            issues.append(
                _issue(
                    path,
                    "09_肿瘤知识保全",
                    row,
                    "ONCOLOGY_AUTHORITY_INVALID",
                    "oncology 必须为布尔值",
                )
            )
        if str(row.get("rule_status") or "") not in CURATED_RULE_STATUSES:
            issues.append(
                _issue(
                    path,
                    "09_肿瘤知识保全",
                    row,
                    "RULE_STATUS_INVALID",
                    "rule_status 不在支持枚举中",
                )
            )
    fragments = {str(row["source_fragment_id"]) for row in sheets["03_来源原文"]}
    branches = {str(row["branch_id"]): row for row in sheets["04_适应证分支"]}
    nodes = {str(row["node_id"]): row for row in sheets["05_条件节点"]}
    children: dict[str, list[dict[str, Any]]] = {}
    roots: dict[str, int] = {}
    sibling_keys: set[tuple[str, str, str]] = set()
    for row in sheets["05_条件节点"]:
        branch = str(row.get("branch_id") or "")
        parent = str(row.get("parent_node_id") or "")
        if branch not in branches:
            issues.append(_issue(path, "05_条件节点", row, "BRANCH_REFERENCE_MISSING", "节点引用不存在的分支"))
        if str(row.get("source_fragment_id") or "") not in fragments:
            issues.append(_issue(path, "05_条件节点", row, "SOURCE_REFERENCE_MISSING", "节点来源片段不存在"))
        if parent:
            children.setdefault(parent, []).append(row)
            if parent not in nodes:
                issues.append(_issue(path, "05_条件节点", row, "PARENT_REFERENCE_MISSING", "父节点不存在"))
        else:
            roots[branch] = roots.get(branch, 0) + 1
        sibling_key = (branch, parent, str(row.get("sibling_order")))
        if sibling_key in sibling_keys:
            issues.append(_issue(path, "05_条件节点", row, "SIBLING_ORDER_DUPLICATE", "同层 sibling_order 重复"))
        sibling_keys.add(sibling_key)
    for branch_id, row in branches.items():
        if roots.get(branch_id, 0) != 1:
            issues.append(_issue(path, "04_适应证分支", row, "ROOT_COUNT", "分支必须恰有一个根节点"))
        try:
            start = date.fromisoformat(str(row.get("effective_from")))
            end = date.fromisoformat(str(row.get("effective_to")))
            if start > end:
                raise ValueError
        except ValueError:
            issues.append(_issue(path, "04_适应证分支", row, "EFFECTIVE_DATE_INVALID", "有效期必须为合法 inclusive 区间"))
        changed = str(row.get("effective_from")) != "2026-01-01" or str(row.get("effective_to")) != "2027-12-31"
        if changed and (
            row.get("effective_date_basis") != "EXPERT_OVERRIDE"
            or not str(row.get("date_override_reason") or "").strip()
            or not str(row.get("date_review_comment") or "").strip()
        ):
            issues.append(_issue(path, "04_适应证分支", row, "DATE_OVERRIDE_INCOMPLETE", "日期覆盖缺少 basis、原因或审核意见"))
        if changed and str(row.get("lifecycle") or "") not in {"DRAFT", "CHANGES_REQUESTED"}:
            issues.append(_issue(path, "04_适应证分支", row, "IMMUTABLE_DATE_EDIT", "只有 DRAFT/CHANGES_REQUESTED 可修改日期"))
    for node_id, row in nodes.items():
        kind = str(row.get("node_kind") or "")
        has_children = bool(children.get(node_id))
        if kind == "LEAF" and has_children:
            issues.append(_issue(path, "05_条件节点", row, "LEAF_HAS_CHILDREN", "叶子不能包含子节点"))
        if kind in {"ALL", "ANY"} and not has_children:
            issues.append(_issue(path, "05_条件节点", row, "AGGREGATE_NO_CHILDREN", "聚合节点必须包含子节点"))
    overlap_rows = [
        {
            "logical_id": row.get("logical_rule_id"),
            "revision_id": row.get("rule_revision_id"),
            "lifecycle": row.get("lifecycle"),
            "effective_from": row.get("effective_from"),
            "effective_to": row.get("effective_to"),
        }
        for row in sheets["04_适应证分支"]
    ]
    for left, right in validate_effective_window_overlaps(overlap_rows):
        row = next(
            item
            for item in sheets["04_适应证分支"]
            if item.get("rule_revision_id") in {left, right}
        )
        issues.append(
            _issue(
                path,
                "04_适应证分支",
                row,
                "EFFECTIVE_WINDOW_OVERLAP",
                f"可发布 revisions {left} 与 {right} inclusive 有效期重叠",
            )
        )
    return issues


def _regimen_issues(path: Path, parsed: dict[str, Any]) -> list[ValidationIssue]:
    sheets = parsed["sheets"]
    issues = []
    keys = {
        "02_方案主表": "regimen_revision_id",
        "03_方案别名": "alias_id",
        "04_方案上下文": "context_id",
        "05_方案组分": "component_id",
        "06_预留给药字段": "schedule_component_id",
    }
    for sheet, key in keys.items():
        issues.extend(_duplicate_issues(path, sheet, sheets[sheet], key))
    revisions = {str(row["regimen_revision_id"]) for row in sheets["02_方案主表"]}
    for sheet in ("04_方案上下文", "05_方案组分", "06_预留给药字段"):
        for row in sheets[sheet]:
            if str(row.get("regimen_revision_id") or "") not in revisions:
                issues.append(_issue(path, sheet, row, "REGIMEN_REFERENCE_MISSING", "引用的方案 revision 不存在"))
    by_alias: dict[str, set[str]] = {}
    for row in sheets["03_方案别名"]:
        revision = str(row.get("regimen_revision_id") or "")
        if revision:
            by_alias.setdefault(str(row.get("normalized_alias") or ""), set()).add(revision)
    for alias, mapped in by_alias.items():
        if len(mapped) > 1:
            row = next(item for item in sheets["03_方案别名"] if item.get("normalized_alias") == alias)
            issues.append(_issue(path, "03_方案别名", row, "AMBIGUOUS_ALIAS", "归一别名映射多个方案，禁止自动选择"))
    return issues


def validate_workbook(path: Path, *, kind: str) -> list[ValidationIssue]:
    safe = _safe_basename(path)
    expected_sheets = ELIGIBILITY_SHEETS if kind == "eligibility" else REGIMEN_SHEETS
    workbook = load_workbook(path, data_only=False, read_only=True)
    schema_issues: list[ValidationIssue] = []
    for sheet in expected_sheets:
        if sheet not in workbook.sheetnames:
            schema_issues.append(ValidationIssue(safe, sheet, 0, "", "SHEET_MISSING", "缺少必需 sheet"))
    if schema_issues:
        return sorted(schema_issues)
    for sheet, required in REQUIRED_COLUMNS[kind].items():
        headers = {str(cell.value or "").strip() for cell in next(workbook[sheet].iter_rows(min_row=1, max_row=1))}
        for column in sorted(required - headers):
            schema_issues.append(ValidationIssue(safe, sheet, 1, "", "COLUMN_MISSING", f"缺少必需列 {column}"))
    if schema_issues:
        return sorted(schema_issues)
    parsed = parse_workbook(path, kind=kind)
    issues: list[ValidationIssue] = []
    if parsed["metadata"].get("template_schema_version") != "1.0.0":
        issues.append(
            ValidationIssue(_safe_basename(path), "01_批次元数据", 2, "", "TEMPLATE_VERSION_UNSUPPORTED", "模板版本不受支持")
        )
    for sheet, rows in parsed["sheets"].items():
        for row in rows:
            if row.get("row_checksum") and row["row_checksum"] != _row_checksum(row):
                issues.append(_issue(path, sheet, row, "ROW_CHECKSUM_MISMATCH", "机器列 checksum 不一致"))
        issues.extend(_review_issues(path, sheet, rows))
        issues.extend(_privacy_issues(path, sheet, rows))
    issues.extend(_eligibility_issues(path, parsed) if kind == "eligibility" else _regimen_issues(path, parsed))
    return sorted(set(issues))


def canonical_workbook_payload(path: Path, *, kind: str) -> bytes:
    parsed = parse_workbook(path, kind=kind)
    canonical = {
        "kind": kind,
        "metadata": {
            key: value
            for key, value in parsed["metadata"].items()
            if key not in {"export_batch_id", "generated_at"}
        },
        "sheets": {
            name: sorted(
                ({key: value for key, value in row.items() if key != "_excel_row"} for row in rows),
                key=lambda row: canonical_json_bytes(row),
            )
            for name, rows in parsed["sheets"].items()
        },
    }
    return canonical_json_bytes(canonical)


def protect_workbook(path: Path) -> None:
    """artifact-tool 生成后只补 sheet protection；不改值、样式或公式。"""
    workbook = load_workbook(path)
    for sheet in workbook.worksheets:
        if sheet.title == "00_使用说明":
            sheet.freeze_panes = "A3"
        elif sheet.title == "01_批次元数据":
            sheet.freeze_panes = "A2"
        elif sheet.title == "06_预留给药字段":
            sheet.freeze_panes = "D2"
        elif sheet.title.startswith(("02_", "03_", "04_", "05_", "06_", "07_", "09_")):
            sheet.freeze_panes = "C2"
        else:
            sheet.freeze_panes = "A2"
        if sheet.title in {"00_使用说明", "01_批次元数据", "07_术语字典", "08_QA问题"}:
            sheet.protection.sheet = True
            continue
        headers = {str(cell.value or ""): cell.column for cell in sheet[1]}
        for name, column in headers.items():
            unlocked = name in EXPERT_COLUMNS
            for cell in sheet.iter_cols(min_col=column, max_col=column, min_row=2, max_row=sheet.max_row):
                cell[0].protection = Protection(locked=not unlocked)
        sheet.protection.sheet = True
        sheet.protection.autoFilter = False
        sheet.protection.sort = False
        sheet.protection.selectUnlockedCells = False
    workbook.save(path)


def write_validation_report(path: Path, issues: list[ValidationIssue]) -> None:
    payload = {
        "schema_version": "1.0.0",
        "valid": not issues,
        "error_count": len(issues),
        "errors": [issue.as_dict() for issue in issues],
    }
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
