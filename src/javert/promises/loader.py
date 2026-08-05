# -*- coding: utf-8 -*-
"""Promise 治理资产加载与一次性 fail-closed 校验。"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import yaml
from pydantic import ValidationError

from javert.audit.rule_loader import load_all

from .models import DriftCase, PromiseCase, PromiseDefinition
from .registry import validate_definition_registry

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DEFINITIONS_DIR = PROJECT_ROOT / "configs" / "promises"
DEFAULT_CASES_DIR = PROJECT_ROOT / "tests" / "promise_cases"
DEFAULT_RULES_DIR = PROJECT_ROOT / "configs" / "rules"
DEFAULT_BEHAVIOR_MAP = PROJECT_ROOT / "configs" / "behavior_names.yaml"
DEFAULT_SOURCE_WORKBOOK = (
    PROJECT_ROOT / "docs" / "templates" / "260611医保基金监管规则框架总表.xlsx"
)


@dataclass(frozen=True, order=True)
class ValidationIssue:
    code: str
    asset_id: str
    field: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "asset_id": self.asset_id, "field": self.field}


class PromiseValidationError(ValueError):
    def __init__(self, issues: Iterable[ValidationIssue]):
        self.issues = tuple(sorted(issues))
        super().__init__(f"Promise 资产校验失败（{len(self.issues)} 项）")


@dataclass(frozen=True)
class PromiseRepository:
    definitions: tuple[PromiseDefinition, ...]
    drift_cases: tuple[DriftCase, ...]
    cases: tuple[PromiseCase, ...]

    @property
    def active_definitions(self) -> tuple[PromiseDefinition, ...]:
        return tuple(item for item in self.definitions if item.status == "active")

    def definition(self, promise_id: str, version: int) -> PromiseDefinition:
        for item in self.definitions:
            if item.promise_id == promise_id and item.version == version:
                return item
        raise KeyError((promise_id, version))


def _safe_validation_issues(path: Path, exc: ValidationError) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for error in exc.errors(include_input=False, include_context=False):
        field = ".".join(str(part) for part in error.get("loc", ()))
        issues.append(ValidationIssue("SCHEMA_INVALID", path.name, field))
    return issues or [ValidationIssue("SCHEMA_INVALID", path.name)]


def _load_assets(
    directories: Iterable[Path],
) -> tuple[list[PromiseDefinition], list[DriftCase], list[PromiseCase], list[ValidationIssue]]:
    definitions: list[PromiseDefinition] = []
    drift_cases: list[DriftCase] = []
    cases: list[PromiseCase] = []
    issues: list[ValidationIssue] = []
    paths = sorted(path for directory in directories for path in directory.glob("*.yaml"))
    for path in paths:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            issues.append(ValidationIssue("YAML_INVALID", path.name))
            continue
        if not isinstance(raw, dict):
            issues.append(ValidationIssue("SCHEMA_INVALID", path.name))
            continue
        asset_type = raw.get("asset_type")
        model: type[PromiseDefinition] | type[DriftCase] | type[PromiseCase] | None = {
            "promise_definition": PromiseDefinition,
            "drift_case": DriftCase,
            "promise_case": PromiseCase,
        }.get(asset_type)
        if model is None:
            issues.append(ValidationIssue("UNKNOWN_ASSET_TYPE", path.name, "asset_type"))
            continue
        try:
            item = model.model_validate(raw)
        except ValidationError as exc:
            issues.extend(_safe_validation_issues(path, exc))
            continue
        if isinstance(item, PromiseDefinition):
            definitions.append(item)
        elif isinstance(item, DriftCase):
            drift_cases.append(item)
        else:
            cases.append(item)
    return definitions, drift_cases, cases, issues


def _validate_registry_and_scope(
    definitions: list[PromiseDefinition], rules_dir: Path
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        rules = load_all(rules_dir)
    except Exception:
        return [ValidationIssue("RULE_INVENTORY_UNAVAILABLE", str(rules_dir))]
    for definition in definitions:
        asset_id = f"{definition.promise_id}@{definition.version}"
        try:
            validate_definition_registry(definition)
        except ValidationError as exc:
            for issue in _safe_validation_issues(Path(asset_id), exc):
                issues.append(ValidationIssue("UNKNOWN_PROMISE_PARAM", asset_id, issue.field))
        except ValueError:
            issues.append(ValidationIssue("UNKNOWN_PROMISE_KIND", asset_id, "kind"))
        for rule_id in definition.scope.rule_ids:
            rule = rules.get(rule_id)
            if rule is None:
                issues.append(ValidationIssue("UNKNOWN_RULE_SCOPE", asset_id, rule_id))
            elif rule.status != "ready":
                issues.append(ValidationIssue("NON_READY_RULE_SCOPE", asset_id, rule_id))
    return issues


def _validate_version_chains(
    definitions: list[PromiseDefinition],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    by_id: dict[str, dict[int, PromiseDefinition]] = {}
    for definition in definitions:
        versions = by_id.setdefault(definition.promise_id, {})
        if definition.version in versions:
            issues.append(
                ValidationIssue(
                    "DUPLICATE_PROMISE_VERSION",
                    f"{definition.promise_id}@{definition.version}",
                )
            )
        versions[definition.version] = definition
    for promise_id, versions in sorted(by_id.items()):
        expected = set(range(1, max(versions, default=0) + 1))
        if set(versions) != expected:
            issues.append(ValidationIssue("MISSING_PROMISE_VERSION", promise_id))
        active = [item for item in versions.values() if item.status == "active"]
        if len(active) != 1:
            issues.append(ValidationIssue("ACTIVE_HEAD_NOT_UNIQUE", promise_id))
        children: dict[int, list[int]] = {}
        for version, definition in versions.items():
            if version == 1:
                continue
            parent = definition.supersedes
            if parent is None or parent not in versions:
                issues.append(
                    ValidationIssue("MISSING_SUPERSEDES", f"{promise_id}@{version}")
                )
                continue
            children.setdefault(parent, []).append(version)
        for parent, child_versions in children.items():
            if len(child_versions) > 1:
                issues.append(
                    ValidationIssue("PROMISE_VERSION_FORK", f"{promise_id}@{parent}")
                )
        for start in versions:
            seen: set[int] = set()
            cursor: int | None = start
            while cursor is not None and cursor in versions:
                if cursor in seen:
                    issues.append(
                        ValidationIssue("PROMISE_VERSION_CYCLE", f"{promise_id}@{start}")
                    )
                    break
                seen.add(cursor)
                cursor = versions[cursor].supersedes
        if active and any(children.get(active[0].version, [])):
            issues.append(ValidationIssue("ACTIVE_NOT_HEAD", promise_id))
    return issues


def _validate_references(
    definitions: list[PromiseDefinition],
    drift_cases: list[DriftCase],
    cases: list[PromiseCase],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    drift_by_id = {item.case_id: item for item in drift_cases}
    case_by_id = {item.case_id: item for item in cases}
    if len(drift_by_id) != len(drift_cases):
        issues.append(ValidationIssue("DUPLICATE_DRIFT_CASE", "drift_cases"))
    if len(case_by_id) != len(cases):
        issues.append(ValidationIssue("DUPLICATE_PROMISE_CASE", "promise_cases"))
    definitions_by_key = {(item.promise_id, item.version): item for item in definitions}
    for definition in definitions:
        asset_id = f"{definition.promise_id}@{definition.version}"
        referenced_cases: list[PromiseCase] = []
        for case_id in definition.source_cases:
            source = drift_by_id.get(case_id)
            if source is None:
                issues.append(ValidationIssue("MISSING_SOURCE_CASE", asset_id, case_id))
            elif definition.status == "active" and source.status != "promoted":
                issues.append(ValidationIssue("SOURCE_CASE_NOT_PROMOTED", asset_id, case_id))
        for case_id in definition.cases:
            case = case_by_id.get(case_id)
            if case is None:
                issues.append(ValidationIssue("MISSING_PROMISE_CASE", asset_id, case_id))
                continue
            referenced_cases.append(case)
            if (case.promise_id, case.promise_version) != (
                definition.promise_id,
                definition.version,
            ):
                issues.append(ValidationIssue("CASE_VERSION_MISMATCH", asset_id, case_id))
        types = {case.case_type for case in referenced_cases}
        if types != {"positive", "near_negative"}:
            issues.append(ValidationIssue("CASE_POLARITY_INCOMPLETE", asset_id))
    for case in cases:
        if (case.promise_id, case.promise_version) not in definitions_by_key:
            issues.append(ValidationIssue("ORPHAN_PROMISE_CASE", case.case_id))
        if case.source_case_id and case.source_case_id not in drift_by_id:
            issues.append(
                ValidationIssue("MISSING_SOURCE_CASE", case.case_id, case.source_case_id)
            )
    return issues


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    value = 0
    for char in letters.group(0) if letters else "":
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def _source_behavior_pairs(workbook: Path) -> set[tuple[str, str]]:
    """直接读取 OOXML，避免 CLI 校验依赖 Excel/网络。"""

    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
    office_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(workbook) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root.findall("m:si", ns)]
        wb = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels.findall("r:Relationship", rel_ns)
        }
        target = ""
        for sheet in wb.findall("m:sheets/m:sheet", ns):
            if sheet.attrib.get("name") == "两库汇总":
                rid = sheet.attrib.get(f"{{{office_rel}}}id", "")
                target = targets.get(rid, "")
                break
        if not target:
            raise ValueError("WORKBOOK_SHEET_MISSING")
        sheet_path = "xl/" + target.lstrip("/").removeprefix("xl/")
        root = ET.fromstring(archive.read(sheet_path))
        pairs: set[tuple[str, str]] = set()
        for row in root.findall("m:sheetData/m:row", ns):
            values = {7: "", 8: ""}
            for cell in row.findall("m:c", ns):
                index = _column_index(cell.attrib.get("r", ""))
                if index not in values:
                    continue
                kind = cell.attrib.get("t")
                if kind == "inlineStr":
                    value = "".join(cell.itertext())
                else:
                    node = cell.find("m:v", ns)
                    value = node.text if node is not None and node.text else ""
                    if kind == "s" and value:
                        value = shared[int(value)]
                values[index] = value.strip()
            if values[7] and values[8] and values[7] not in {"/", "行为认定编码"}:
                pairs.add((values[7], values[8]))
        return pairs


def validate_behavior_mapping(
    rules_dir: Path = DEFAULT_RULES_DIR,
    behavior_map_path: Path = DEFAULT_BEHAVIOR_MAP,
    workbook_path: Path = DEFAULT_SOURCE_WORKBOOK,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        rules = load_all(rules_dir)
        raw = yaml.safe_load(behavior_map_path.read_text(encoding="utf-8")) or {}
        mappings = raw.get("mappings", {})
        source_pairs = _source_behavior_pairs(workbook_path)
    except Exception:
        return [ValidationIssue("BEHAVIOR_SOURCE_UNAVAILABLE", "behavior_mapping")]
    for rule in rules.values():
        if rule.status != "ready":
            continue
        entry = mappings.get(rule.violation_type)
        if not isinstance(entry, dict):
            issues.append(
                ValidationIssue("BEHAVIOR_MAPPING_MISSING", rule.rule_id, rule.violation_type)
            )
            continue
        pair = (str(entry.get("code") or ""), str(entry.get("name") or ""))
        if pair in source_pairs:
            continue
        exception_valid = all(
            (
                entry.get("exception") is True,
                bool(entry.get("exception_key")),
                bool(entry.get("source_ref")),
                bool(pair[1]),
            )
        )
        if not exception_valid:
            issues.append(
                ValidationIssue("BEHAVIOR_MAPPING_UNVERIFIED", rule.rule_id, rule.violation_type)
            )
    return issues


def load_repository(
    definitions_dir: Path = DEFAULT_DEFINITIONS_DIR,
    cases_dir: Path = DEFAULT_CASES_DIR,
    rules_dir: Path = DEFAULT_RULES_DIR,
    *,
    validate_behavior: bool = True,
) -> PromiseRepository:
    definitions, drift_cases, cases, issues = _load_assets(
        (definitions_dir, cases_dir)
    )
    issues.extend(_validate_registry_and_scope(definitions, rules_dir))
    issues.extend(_validate_version_chains(definitions))
    issues.extend(_validate_references(definitions, drift_cases, cases))
    if validate_behavior:
        issues.extend(validate_behavior_mapping(rules_dir=rules_dir))
    if issues:
        raise PromiseValidationError(issues)
    return PromiseRepository(
        definitions=tuple(sorted(definitions, key=lambda item: (item.promise_id, item.version))),
        drift_cases=tuple(sorted(drift_cases, key=lambda item: item.case_id)),
        cases=tuple(sorted(cases, key=lambda item: item.case_id)),
    )


@lru_cache(maxsize=1)
def get_promise_repository() -> PromiseRepository:
    """运行时只加载并严格校验一次默认资产。"""

    return load_repository()


def validation_report(error: PromiseValidationError | None, repository: PromiseRepository | None) -> dict[str, Any]:
    issues = [] if error is None else [item.as_dict() for item in error.issues]
    explicit_exceptions = 0
    try:
        behavior_raw = yaml.safe_load(DEFAULT_BEHAVIOR_MAP.read_text(encoding="utf-8")) or {}
        explicit_exceptions = sum(
            isinstance(entry, dict) and entry.get("exception") is True
            for entry in (behavior_raw.get("mappings") or {}).values()
        )
    except Exception:
        explicit_exceptions = 0
    return {
        "status": "ok" if not issues else "failed",
        "counts": {
            "definitions": len(repository.definitions) if repository else 0,
            "drift_cases": len(repository.drift_cases) if repository else 0,
            "cases": len(repository.cases) if repository else 0,
            "issues": len(issues),
            "explicit_exceptions": explicit_exceptions,
        },
        "issues": issues,
    }


def report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
