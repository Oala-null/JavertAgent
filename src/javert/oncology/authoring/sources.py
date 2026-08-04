"""把当前肿瘤来源投影为统一 source/concept/rule authoring 合同。"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .baseline import file_checksum
from .ids import (
    checksum,
    logical_rule_id,
    revision_id,
    source_document_id,
    source_fragment_id,
    stable_id,
)
from .models import (
    AuthoringSnapshot,
    CandidateDisposition,
    CandidateUniverseRecord,
    DrugConceptRecord,
    DrugProductRecord,
    EffectiveWindow,
    RevisionLifecycle,
    SourceDocument,
    SourceFragment,
    SourceRule,
    SourceType,
)


_SPACE_RE = re.compile(r"[\s\-—_/（）()]+")
_PAGE_RE = re.compile(r"(?:pdf_pages?_?|pages?_)(\d+)(?:[-_](\d+))?", re.I)

_SALT_PREFIXES = (
    "甲苯磺酸", "甲磺酸", "羟乙磺酸", "门冬氨酸", "枸橼酸", "苹果酸",
    "富马酸", "马来酸", "琥珀酸", "己二酸", "苯磺酸", "酒石酸", "氢溴酸",
    "棕榈酸", "盐酸", "磷酸", "硫酸", "醋酸", "乳酸", "草酸",
)
_DOSAGE_PREFIXES = ("注射用", "吸入用")
_DOSAGE_SUFFIXES = (
    "脂质体注射液", "聚合物胶束", "肠溶胶囊", "缓释胶囊", "口服混悬液",
    "口服溶液", "干混悬剂", "冻干粉针剂", "软胶囊", "肠溶片", "缓释片",
    "分散片", "咀嚼片", "泡腾片", "口崩片", "注射液", "注射剂", "胶囊剂",
    "颗粒剂", "混悬液", "胶囊", "颗粒", "片剂", "乳剂", "片",
)


def normalize_name(value: str) -> str:
    return _SPACE_RE.sub("", str(value or "")).casefold()


def normalize_drug_concept_name(value: str) -> str:
    """保守归一通用名，仅用于 concept authority 全等匹配。

    concept 可聚合同通用名的多剂型产品，但 product/code 身份仍独立保留。
    """
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = re.sub(r"[\s\-‐‑‒–—―·]", "", normalized)
    normalized = re.sub(
        r"[（(](?:I{1,4}|IV|V|VI{0,3}|Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ)[）)]$", "", normalized
    )
    normalized = re.sub(
        r"[（(](?:皮下|静脉|注射|口服)[^）)]*[）)]$", "", normalized
    )
    for prefix in _DOSAGE_PREFIXES:
        if normalized.startswith(prefix) and len(normalized) - len(prefix) >= 2:
            normalized = normalized[len(prefix) :]
            break
    for prefix in _SALT_PREFIXES:
        if normalized.startswith(prefix) and len(normalized) - len(prefix) >= 2:
            normalized = normalized[len(prefix) :]
            break
    changed = True
    while changed:
        changed = False
        for suffix in _DOSAGE_SUFFIXES:
            if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 2:
                normalized = normalized[: -len(suffix)]
                changed = True
                break
    return normalized.strip(" ,，。;；:：*#△☼").casefold()


def _legacy_concept_authority(
    root: Path,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    asset = _load(root / "configs/oncology_regimen_kb.json")
    by_generic: dict[str, str] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for raw in asset.get("drug_concepts", []):
        item = dict(raw)
        concept_id = str(item.get("concept_id") or "").strip()
        generic_name = str(item.get("generic_name") or "").strip()
        generic_key = normalize_drug_concept_name(generic_name)
        if not concept_id or not generic_key:
            raise ValueError("方案 drug_concepts 缺少 concept_id 或 generic_name")
        if concept_id in by_id and by_id[concept_id] != item:
            raise ValueError(f"方案 legacy concept_id 冲突: {concept_id}")
        existing = by_generic.get(generic_key)
        if existing and existing != concept_id:
            raise ValueError(f"方案通用名 authority 不唯一: {generic_name}")
        by_generic[generic_key] = concept_id
        by_id[concept_id] = item
    return by_generic, by_id


def _canonical_generic_name(raw_name: str, drug: dict[str, Any]) -> str:
    candidate = str(
        drug.get("canonical_match_name")
        or drug.get("canonical_name")
        or raw_name
    ).strip()
    return normalize_drug_concept_name(candidate) or candidate


def _concept_identity(
    raw_name: str,
    drug: dict[str, Any],
    legacy_by_generic: dict[str, str],
) -> tuple[str, str, str]:
    canonical_name = _canonical_generic_name(raw_name, drug)
    normalized_name = normalize_drug_concept_name(canonical_name)
    if not normalized_name:
        raise ValueError(f"药品通用名无法归一: {raw_name}")
    concept_id = legacy_by_generic.get(normalized_name) or stable_id(
        "drug", normalized_name
    )
    return concept_id, canonical_name, normalized_name


def _clean_catalog_cell(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\xa0", " ")
    if text.strip().casefold() in {"", "nan", "none", "null"}:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _hospital_catalog_rows(root: Path) -> list[dict[str, str]]:
    """只读已有医院总库，为方案缺失的精确成分补真实 product/code。"""
    import xlrd

    path = root / "docs/药品限制/药品总库医院_202606.xls"
    sheet = xlrd.open_workbook(path).sheet_by_index(0)
    headers = {
        _clean_catalog_cell(sheet.cell_value(0, column)): column
        for column in range(sheet.ncols)
    }
    required = {"药品编码", "药品通用名"}
    missing = sorted(required - headers.keys())
    if missing:
        raise ValueError(f"医院药品总库缺少字段: {','.join(missing)}")

    def value(row: int, column_name: str) -> str:
        column = headers.get(column_name)
        return "" if column is None else _clean_catalog_cell(
            sheet.cell_value(row, column)
        )

    rows: list[dict[str, str]] = []
    for row in range(1, sheet.nrows):
        drug_code = value(row, "药品编码")
        generic_name = value(row, "药品通用名")
        if not drug_code or not generic_name or value(row, "状态") == "无效":
            continue
        rows.append(
            {
                "drug_code": drug_code,
                "generic_name": generic_name,
                "specification": value(row, "规格包装"),
                "manufacturer": value(row, "生产厂家"),
            }
        )
    return rows


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_documents(root: Path) -> dict[SourceType, SourceDocument]:
    specs = {
        SourceType.INSURANCE_PAYMENT: (
            root / "docs/药品限制/药品总库医院_202606.xls",
            "医院药品总库与国家医保目录支付限定",
            "2026-06+2025",
            2026,
        ),
        SourceType.GUIDELINE_INDICATION: (
            root / "docs/药品限制/新型抗肿瘤药物临床应用指导原则（2025年版）.pdf",
            "新型抗肿瘤药物临床应用指导原则（2025年版）",
            "2025",
            2025,
        ),
    }
    return {
        source_type: SourceDocument(
            source_document_id=source_document_id(source_type, title, version),
            source_type=source_type,
            title=title,
            document_version=version,
            document_year=year,
            retrieval_date=date(2026, 7, 21),
            content_checksum=file_checksum(path),
        )
        for source_type, (path, title, version, year) in specs.items()
    }


def _pages(source_refs: list[str]) -> list[int]:
    pages: set[int] = set()
    for ref in source_refs:
        match = _PAGE_RE.search(ref)
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2) or start)
        pages.update(range(start, end + 1))
    return sorted(pages)


def build_authoring_snapshot(root: Path) -> AuthoringSnapshot:
    kb = _load(root / "configs/oncology_drug_kb.json")
    legacy_by_generic, _ = _legacy_concept_authority(root)
    documents = _source_documents(root)
    pending_rules: list[tuple[str, dict[str, Any], SourceType, tuple[str, str]]] = []
    fragment_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_name, drug in sorted((kb.get("drugs") or {}).items()):
        effective = drug.get("effective") or {}
        if not effective:
            continue
        source_type = {
            "insurance": SourceType.INSURANCE_PAYMENT,
            "guideline": SourceType.GUIDELINE_INDICATION,
        }.get(effective.get("source_type"))
        if source_type is None:
            continue
        source_refs = [str(value) for value in effective.get("source_refs", [])]
        text = str(effective.get("basis") or "").strip()
        document = documents[source_type]
        group_key = (document.source_document_id, checksum(text))
        group = fragment_groups.setdefault(
            group_key,
            {
                "document": document,
                "text": text,
                "anchors": set(),
                "pages": set(),
            },
        )
        if group["text"] != text:
            raise ValueError("source fragment checksum collision")
        group["anchors"].update(source_refs or [f"drug:{raw_name}"])
        group["pages"].update(_pages(source_refs))
        pending_rules.append((raw_name, drug, source_type, group_key))

    fragments_by_group: dict[tuple[str, str], SourceFragment] = {}
    for group_key, group in sorted(fragment_groups.items()):
        document = group["document"]
        text = str(group["text"])
        anchor = "|".join(sorted(group["anchors"]))
        fragments_by_group[group_key] = SourceFragment(
            source_fragment_id=source_fragment_id(
                document.source_document_id, anchor, text
            ),
            source_document_id=document.source_document_id,
            anchor=anchor,
            original_text=text,
            page_numbers=sorted(group["pages"]),
            content_checksum=group_key[1],
        )

    rules: dict[str, SourceRule] = {}
    for raw_name, drug, source_type, group_key in pending_rules:
        fragment = fragments_by_group[group_key]
        fragment_id = fragment.source_fragment_id
        concept_id, _, _ = _concept_identity(raw_name, drug, legacy_by_generic)
        logical_id = logical_rule_id(source_type, concept_id, fragment_id)
        payload = {
            "source_fragment_id": fragment_id,
            "drug_concept_id": concept_id,
            "policy_scope": source_type,
            "effective_window": EffectiveWindow().model_dump(mode="json"),
        }
        rule = SourceRule(
            logical_rule_id=logical_id,
            revision_id=revision_id(logical_id, payload),
            source_fragment_id=fragment_id,
            drug_concept_id=concept_id,
            policy_scope=source_type,
            lifecycle=RevisionLifecycle.DRAFT,
            label_source_missing=(
                source_type == SourceType.INSURANCE_PAYMENT and "说明书" in text
            ),
        )
        rules[rule.revision_id] = rule
    return AuthoringSnapshot(
        source_documents=list(documents.values()),
        source_fragments=[
            fragments_by_group[key] for key in sorted(fragments_by_group)
        ],
        source_rules=[rules[key] for key in sorted(rules)],
    )


def build_drug_crosswalk(
    root: Path,
    *,
    supplemental_generic_names: Iterable[str] = (),
) -> tuple[list[DrugConceptRecord], list[DrugProductRecord]]:
    kb = _load(root / "configs/oncology_drug_kb.json")
    legacy_by_generic, legacy_by_id = _legacy_concept_authority(root)
    regimen = _load(root / "configs/oncology_regimen_kb.json")
    used_legacy_ids = {
        str(component.get("drug_concept_id") or "").strip()
        for entry in regimen.get("entries", [])
        for component in entry.get("components", [])
        if not component.get("target_kind") and not component.get("target_id")
    }
    requested_names = {
        str(value).strip()
        for value in supplemental_generic_names
        if str(value).strip()
    }
    requested_names.update(
        str(legacy_by_id[concept_id].get("generic_name") or "").strip()
        for concept_id in used_legacy_ids
        if concept_id in legacy_by_id
    )

    concept_values: dict[str, dict[str, Any]] = {}
    concept_id_by_normalized: dict[str, str] = {}
    products: list[DrugProductRecord] = []

    def add_concept(
        concept_id: str,
        canonical_name: str,
        normalized_name: str,
        aliases: Iterable[str],
    ) -> None:
        existing_id = concept_id_by_normalized.get(normalized_name)
        if existing_id and existing_id != concept_id:
            raise ValueError(f"通用名 concept authority 冲突: {canonical_name}")
        concept_id_by_normalized[normalized_name] = concept_id
        current = concept_values.get(concept_id)
        if current and current["normalized_name"] != normalized_name:
            raise ValueError(f"concept_id 对应多个通用名: {concept_id}")
        if current is None:
            current = {
                "canonical_name": canonical_name,
                "normalized_name": normalized_name,
                "aliases": set(),
            }
            concept_values[concept_id] = current
        current["aliases"].update(
            str(value).strip()
            for value in aliases
            if str(value).strip() and str(value).strip() != canonical_name
        )

    def add_product(
        *,
        concept_id: str,
        source_kind: str,
        product_name: str,
        dosage_form: str = "",
        manufacturer: str = "",
        insurance_code: str = "",
        hospital_code: str = "",
    ) -> None:
        identity = [
            concept_id,
            source_kind,
            normalize_name(product_name),
            dosage_form,
            insurance_code,
            hospital_code,
            manufacturer,
        ]
        products.append(
            DrugProductRecord(
                drug_product_id=stable_id("product", *identity),
                drug_concept_id=concept_id,
                product_name=product_name,
                dosage_form=dosage_form,
                manufacturer=manufacturer,
                insurance_code=insurance_code,
                hospital_code=hospital_code,
                source_kind=source_kind,
            )
        )

    for raw_name, drug in sorted((kb.get("drugs") or {}).items()):
        concept_id, canonical_name, normalized_name = _concept_identity(
            raw_name, drug, legacy_by_generic
        )
        aliases = {
            raw_name,
            str(drug.get("canonical_name") or ""),
            str(drug.get("canonical_match_name") or ""),
            *(str(value) for value in (drug.get("aliases") or [])),
        }
        add_concept(concept_id, canonical_name, normalized_name, aliases)
        for source_kind, rows in sorted((drug.get("sources") or {}).items()):
            for row in rows:
                product_name = str(
                    row.get("product_name")
                    or row.get("drug_name")
                    or row.get("generic_name")
                    or raw_name
                ).strip()
                dosage_form = str(
                    row.get("dosage_form")
                    or row.get("form_and_strength")
                    or row.get("specification")
                    or ""
                ).strip()
                insurance_code = str(
                    row.get("insurance_code") or row.get("medical_code") or row.get("catalog_number") or ""
                ).strip()
                hospital_code = str(
                    row.get("hospital_code") or row.get("drug_code") or row.get("code") or ""
                ).strip()
                manufacturer = str(row.get("manufacturer") or row.get("manufacturer_name") or "").strip()
                add_product(
                    concept_id=concept_id,
                    source_kind=source_kind,
                    product_name=product_name,
                    dosage_form=dosage_form,
                    manufacturer=manufacturer,
                    insurance_code=insurance_code,
                    hospital_code=hospital_code,
                )

    missing_keys = {
        normalize_drug_concept_name(name)
        for name in requested_names
        if normalize_drug_concept_name(name) not in concept_id_by_normalized
    }
    if missing_keys:
        hospital_rows = _hospital_catalog_rows(root)
        rows_by_generic: dict[str, list[dict[str, str]]] = {}
        for row in hospital_rows:
            key = normalize_drug_concept_name(row["generic_name"])
            if key in missing_keys:
                rows_by_generic.setdefault(key, []).append(row)
        for generic_key in sorted(missing_keys):
            matching_rows = rows_by_generic.get(generic_key, [])
            if not matching_rows:
                continue
            concept_id = legacy_by_generic.get(generic_key) or stable_id(
                "drug", generic_key
            )
            aliases = {row["generic_name"] for row in matching_rows}
            legacy = legacy_by_id.get(concept_id)
            if legacy:
                aliases.add(str(legacy.get("generic_name") or ""))
                aliases.update(
                    str(alias.get("value") if isinstance(alias, dict) else alias)
                    for alias in legacy.get("aliases", [])
                )
            add_concept(concept_id, generic_key, generic_key, aliases)
            for row in matching_rows:
                add_product(
                    concept_id=concept_id,
                    source_kind="hospital_catalog",
                    product_name=row["generic_name"],
                    dosage_form=row["specification"],
                    manufacturer=row["manufacturer"],
                    hospital_code=row["drug_code"],
                )

    concepts = [
        DrugConceptRecord(
            drug_concept_id=concept_id,
            canonical_name=values["canonical_name"],
            normalized_name=values["normalized_name"],
            aliases=sorted(values["aliases"]),
        )
        for concept_id, values in sorted(concept_values.items())
    ]
    unique = {item.drug_product_id: item for item in products}
    return concepts, [unique[key] for key in sorted(unique)]


def _rule_drug_name(path: Path) -> str:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    keywords = raw.get("trigger_keywords") or []
    return str(keywords[0] if keywords else raw.get("question") or path.stem)


def build_candidate_universe(root: Path) -> list[CandidateUniverseRecord]:
    kb = _load(root / "configs/oncology_drug_kb.json")
    members: dict[str, dict[str, Any]] = {}

    def add(name: str, source: str) -> None:
        key = normalize_name(name)
        if not key:
            return
        item = members.setdefault(key, {"name": name, "sources": set()})
        item["sources"].add(source)

    for name, drug in (kb.get("drugs") or {}).items():
        add(name, "oncology_drug_asset")
        for source, rows in (drug.get("sources") or {}).items():
            if rows:
                add(name, source)
    for asset_name in ("oncology_eligibility_rules.json", "pathology_biomarker_kb.json"):
        asset = _load(root / "configs" / asset_name)
        for entry in asset.get("entries", []):
            concept = str(entry.get("drug_concept_id") or entry.get("policy_context") or "")
            if concept:
                add(concept.removeprefix("candidate:"), "structured_asset")
    for path in sorted((root / "configs/rules").glob("RD[1-3][0-9].yaml")):
        number = int(path.stem[2:])
        if 10 <= number <= 37:
            add(_rule_drug_name(path), "rule_yaml")

    source_names = (
        "hospital_catalog",
        "national_catalog",
        "guideline",
        "oncology_drug_asset",
        "structured_asset",
        "rule_yaml",
    )
    result = []
    for key, item in sorted(members.items()):
        source_set = item["sources"]
        oncology_confirmed = bool(
            source_set & {"guideline", "oncology_drug_asset", "structured_asset"}
        )
        disposition = CandidateDisposition.INCLUDED if oncology_confirmed else CandidateDisposition.NEEDS_REVIEW
        result.append(
            CandidateUniverseRecord(
                candidate_id=stable_id("candidate", key),
                canonical_name=item["name"],
                source_membership={name: name in source_set for name in source_names},
                normalization_status="normalized" if key else "unknown",
                disposition=disposition,
                disposition_reason=(
                    "肿瘤来源已确认" if oncology_confirmed else "仅规则/非肿瘤来源，需确认肿瘤归属"
                ),
            )
        )
    return result


def build_pathology_links(root: Path, condition_nodes: list[Any]) -> list[dict[str, Any]]:
    """把病理候选关联到 biomarker 叶子；未知阈值保持非 approved。"""
    pathology = _load(root / "configs/pathology_biomarker_kb.json")
    marker_nodes: dict[str, list[Any]] = {}
    for node in condition_nodes:
        criterion = getattr(node, "criterion_type", None)
        if str(criterion) not in {"biomarker", "CriterionType.BIOMARKER"}:
            continue
        value = str(getattr(node, "target_id", ""))
        marker_nodes.setdefault(value, []).append(node)
    links: list[dict[str, Any]] = []
    for entry in pathology.get("entries", []):
        marker = str(entry.get("marker_id") or "").upper()
        marker_id = stable_id("marker", marker)
        threshold = entry.get("threshold") or {}
        threshold_known = bool(threshold) and set(threshold) != {"raw"}
        review_status = str(entry.get("metadata", {}).get("review_status") or "needs_review")
        for node in marker_nodes.get(marker_id, []):
            links.append(
                {
                    "link_id": stable_id("pathlink", entry.get("entry_id"), node.node_id),
                    "pathology_entry_id": entry.get("entry_id"),
                    "condition_node_id": node.node_id,
                    "source_restriction_id": node.source_fragment_id,
                    "marker": marker,
                    "methods": sorted(entry.get("methods") or []),
                    "cancer_contexts": sorted(entry.get("cancer_contexts") or []),
                    "threshold": threshold,
                    "threshold_known": threshold_known,
                    "review_status": review_status,
                    "approved_assertion": review_status == "approved" and threshold_known,
                }
            )
    return sorted(links, key=lambda item: item["link_id"])
