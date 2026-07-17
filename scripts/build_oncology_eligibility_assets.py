# -*- coding: utf-8 -*-
"""现有肿瘤医保限定 → approved 条件树 + 全量 needs_review 覆盖资产."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from javert.oncology.eligibility import (
    ConditionNode,
    EligibilityRule,
    EligibilityRulesAsset,
)
from javert.oncology.knowledge import (
    KnowledgeEntryMetadata,
    KnowledgeMetadata,
    ReviewStatus,
    SourceReference,
    asset_payload_checksum,
    canonical_json_bytes,
    sha256_digest,
)
from javert.oncology.pathology import (
    BiomarkerMethod,
    BiomarkerRule,
    MarkerAlias,
    PathologyKnowledgeAsset,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_KB = ROOT / "configs" / "oncology_drug_kb.json"
ELIGIBILITY_OUT = ROOT / "configs" / "oncology_eligibility_rules.json"
PATHOLOGY_OUT = ROOT / "configs" / "pathology_biomarker_kb.json"
REVIEW_OUT = ROOT / "docs" / "oncology" / "oncology_eligibility_needs_review.json"
PATHOLOGY_REVIEW_OUT = ROOT / "docs" / "oncology" / "pathology_biomarker_needs_review.json"
BUILD_DATE = date(2026, 7, 17)

MARKERS = {
    "AKT1": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "ALK": ("fusion", BiomarkerMethod.MOLECULAR),
    "BCL2": ("protein_expression", BiomarkerMethod.IHC),
    "BRAF": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "BRCA": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "CD20": ("protein_expression", BiomarkerMethod.IHC),
    "CD30": ("protein_expression", BiomarkerMethod.IHC),
    "EGFR": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "ERBB2": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "HER2": ("protein_expression", BiomarkerMethod.IHC),
    "HR": ("protein_expression", BiomarkerMethod.IHC),
    "IDH1": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "KRAS": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "MET": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "MSI-H": ("composite_signature", BiomarkerMethod.MOLECULAR),
    "MYC": ("protein_expression", BiomarkerMethod.IHC),
    "NRAS": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "NTRK": ("fusion", BiomarkerMethod.MOLECULAR),
    "PD-L1": ("protein_expression", BiomarkerMethod.IHC),
    "PDGFRA": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "PIK3CA": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "PTEN": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "RAS": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "RET": ("fusion", BiomarkerMethod.MOLECULAR),
    "ROS1": ("fusion", BiomarkerMethod.MOLECULAR),
    "T315I": ("sequence_variant", BiomarkerMethod.MOLECULAR),
    "dMMR": ("composite_signature", BiomarkerMethod.MOLECULAR),
    "pMMR": ("composite_signature", BiomarkerMethod.MOLECULAR),
}

ALIASES = {
    marker: [marker]
    for marker in MARKERS
}
ALIASES["HER2"] = ["HER2", "HER-2", "HER2/neu", "c-erbB-2", "CerbB2"]
ALIASES["ERBB2"] = ["ERBB2"]

CANCER_KEYWORDS = (
    "尿路上皮癌",
    "胃癌",
    "胃食管结合部腺癌",
    "乳腺癌",
    "非小细胞肺癌",
    "结直肠癌",
    "宫颈癌",
    "黑色素瘤",
    "前列腺癌",
    "卵巢癌",
    "甲状腺癌",
    "弥漫大B细胞淋巴瘤",
    "淋巴瘤",
    "白血病",
    "实体瘤",
)

MANUAL_BRANCH_MANIFEST: dict[str, dict[str, Any]] = {
    "注射用维迪西妥单抗": {
        "source_basis": (
            "限:1.至少接受过2个系统化疗的HER2过表达局部晚期或转移性胃癌"
            "(包括胃食管结合部腺癌);2.既往接受过含铂化疗且HER2过表达"
            "局部晚期或转移性尿路上皮癌。"
        ),
        "branches": [
            {
                "branch_id": "gastric-prior-two-systemic-her2",
                "source_text": (
                    "至少接受过2个系统化疗的HER2过表达局部晚期或转移性胃癌"
                    "(包括胃食管结合部腺癌)"
                ),
                "review_status": "needs_review",
                "expected": {"minimum_systemic_therapy_count": 2},
                "reason": (
                    "当前 evaluator 不支持“至少2个系统化疗”的可靠计数判定"
                ),
            },
            {
                "branch_id": "urothelial-prior-platinum-her2",
                "source_text": (
                    "既往接受过含铂化疗且HER2过表达"
                    "局部晚期或转移性尿路上皮癌"
                ),
                "review_status": "approved",
            },
        ],
    },
    "注射用维泊妥珠单抗": {
        "source_basis": (
            "限:1.既往未经治疗的弥漫大B细胞淋巴瘤(DLBCL)成人患者;"
            "2.不适合接受造血干细胞移植的复发或难治性弥漫大B细胞淋巴瘤"
            "(DLBCL)成人患者。"
        ),
        "branches": [
            {
                "branch_id": "dlbcl-untreated",
                "source_text": (
                    "既往未经治疗的弥漫大B细胞淋巴瘤(DLBCL)成人患者"
                ),
                "review_status": "approved",
            },
            {
                "branch_id": (
                    "dlbcl-relapsed-refractory-transplant-ineligible"
                ),
                "source_text": (
                    "不适合接受造血干细胞移植的复发或难治性"
                    "弥漫大B细胞淋巴瘤(DLBCL)成人患者"
                ),
                "review_status": "approved",
            },
        ],
    },
}


def _source_ref(source_path: Path) -> SourceReference:
    return SourceReference(
        source_id="derived:oncology_drug_kb",
        title="Javert 肿瘤药医保限定编译输入",
        version="1.0",
        publication_date=date(2026, 6, 1),
        retrieval_date=BUILD_DATE,
        checksum=sha256_digest(source_path.read_bytes()),
    )


_VALIDITY_RE = re.compile(
    r"^\s*(\d{4})年(\d{1,2})月(\d{1,2})日"
    r"\s*至\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*$"
)


def _parse_validity(raw: str) -> tuple[date, date]:
    match = _VALIDITY_RE.fullmatch(raw)
    if match is None:
        raise ValueError(f"无法解析 national_catalog.validity: {raw!r}")
    values = [int(item) for item in match.groups()]
    return date(*values[:3]), date(*values[3:])


def _normalize_policy_text(value: Any) -> str:
    return (
        re.sub(r"\s+", "", str(value or ""))
        .replace("：", ":")
        .replace("；", ";")
        .replace("，", ",")
        .rstrip("。")
    )


def _policy_fingerprint(value: str) -> str:
    return hashlib.sha256(
        _normalize_policy_text(value).encode("utf-8")
    ).hexdigest()


def _split_numbered_branches(value: str) -> list[str]:
    normalized = _normalize_policy_text(value)
    normalized = re.sub(r"^限:", "", normalized)
    matches = list(re.finditer(r"(?:^|;)(\d+)\.", normalized))
    if not matches:
        raise ValueError(f"无法解析手工编译来源的编号分支: {value!r}")
    branches = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        branches.append(normalized[match.end() : end].strip(";。"))
    return branches


def _validate_manual_branch_manifest(
    rows_by_generic: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """手工编译来源必须与 manifest 的全文指纹和分支集合同时一致."""
    validations = []
    for generic_name, manifest in MANUAL_BRANCH_MANIFEST.items():
        row = rows_by_generic.get(generic_name)
        if row is None:
            raise ValueError(f"手工编译来源限定缺失: {generic_name}")
        expected_basis = manifest["source_basis"]
        expected_fingerprint = _policy_fingerprint(expected_basis)
        actual_fingerprint = _policy_fingerprint(row["basis"])
        expected_branches = {
            _normalize_policy_text(item["source_text"])
            for item in manifest["branches"]
        }
        actual_branches = {
            _normalize_policy_text(item)
            for item in _split_numbered_branches(row["basis"])
        }
        if (
            actual_fingerprint != expected_fingerprint
            or actual_branches != expected_branches
        ):
            raise ValueError(
                "手工编译来源限定与 branch manifest 不一致，拒绝生成 approved 资产: "
                f"{generic_name}; expected_fingerprint={expected_fingerprint}; "
                f"actual_fingerprint={actual_fingerprint}; "
                f"unknown_branches={sorted(actual_branches - expected_branches)}; "
                f"missing_branches={sorted(expected_branches - actual_branches)}"
            )
        validations.append(
            {
                "generic_name": generic_name,
                "source_restriction_id": row["source_restriction_id"],
                "normalized_basis_fingerprint": actual_fingerprint,
                "manifest_branch_ids": [
                    item["branch_id"] for item in manifest["branches"]
                ],
                "status": "matched",
            }
        )
    return validations


def _source_snapshot_start(source_data: dict[str, Any], source_name: str) -> date:
    title = str(source_data.get("sources", {}).get(source_name, {}).get("title") or "")
    match = re.search(r"(\d{4})(?:\s*[-年]\s*(\d{1,2}))?", title)
    if match is None:
        raise ValueError(f"无法从 {source_name} 来源标题解析日期: {title!r}")
    return date(int(match.group(1)), int(match.group(2) or 1), 1)


def _effective_range_for_restriction(
    source_data: dict[str, Any],
    drug: dict[str, Any],
    basis: str,
) -> tuple[date, date | None, str, str]:
    """优先使用国家目录有效期；无协商期时退回来源版本起点并保留来源类型."""
    normalized_basis = _normalize_policy_text(basis)
    national_rows = drug.get("sources", {}).get("national_catalog", []) or []
    matches = [
        item
        for item in national_rows
        if _normalize_policy_text(item.get("restriction")) == normalized_basis
    ]
    raw_validities = sorted(
        {
            str(item.get("validity") or "").strip()
            for item in matches
            if str(item.get("validity") or "").strip()
        }
    )
    if len(raw_validities) > 1:
        raise ValueError(f"同一限定匹配到冲突的 national_catalog.validity: {raw_validities}")
    if raw_validities:
        start, end = _parse_validity(raw_validities[0])
        return start, end, "national_catalog.validity", raw_validities[0]
    if matches:
        return (
            _source_snapshot_start(source_data, "national_catalog"),
            None,
            "national_catalog.catalog_version",
            "",
        )
    return (
        _source_snapshot_start(source_data, "hospital_catalog"),
        None,
        "hospital_catalog.snapshot",
        "",
    )


def _entry_meta(
    source: SourceReference,
    status: ReviewStatus,
    row: dict[str, Any],
) -> KnowledgeEntryMetadata:
    return KnowledgeEntryMetadata(
        content_version="2026.07.17",
        effective_from=date.fromisoformat(row["effective_from"]),
        effective_to=(
            date.fromisoformat(row["effective_to"])
            if row["effective_to"] is not None
            else None
        ),
        source_refs=[source],
        review_status=status,
    )


def _asset_meta(
    source: SourceReference,
    entries: list[EligibilityRule] | list[BiomarkerRule],
) -> KnowledgeMetadata:
    starts = [item.metadata.effective_from for item in entries]
    ends = [item.metadata.effective_to for item in entries]
    return KnowledgeMetadata(
        schema_version="1.0.0",
        content_version="2026.07.17",
        effective_from=min(starts),
        effective_to=max(ends) if all(end is not None for end in ends) else None,
        source_refs=[source],
        checksum="sha256:" + "0" * 64,
        review_status=ReviewStatus.APPROVED,
    )


def _leaf(
    criterion_id: str,
    criterion_type: str,
    expected: dict[str, Any],
    source_text: str,
    *,
    documentation_template: str = "",
) -> ConditionNode:
    return ConditionNode(
        node_id=criterion_id,
        kind="leaf",
        criterion_id=criterion_id,
        criterion_type=criterion_type,
        expected=expected,
        source_text=source_text,
        evidence_policy={"anchored": True, "missing_is": "UNKNOWN"},
        documentation_template=documentation_template,
    )


def _all(node_id: str, source_text: str, *children: ConditionNode) -> ConditionNode:
    return ConditionNode(
        node_id=node_id,
        kind="all",
        source_text=source_text,
        children=list(children),
    )


def _any(node_id: str, source_text: str, *children: ConditionNode) -> ConditionNode:
    return ConditionNode(
        node_id=node_id,
        kind="any",
        source_text=source_text,
        children=list(children),
    )


def _approved_rules(
    source: SourceReference,
    rows_by_generic: dict[str, dict[str, Any]],
) -> list[EligibilityRule]:
    disitamab_row = rows_by_generic["注射用维迪西妥单抗"]
    pola_row = rows_by_generic["注射用维泊妥珠单抗"]
    disitamab_basis = disitamab_row["basis"]
    pola_basis = pola_row["basis"]
    return [
        EligibilityRule(
            rule_id="elig-disitamab-urothelial-2026",
            drug_concept_id="disitamab-vedotin",
            indication_branch_id="urothelial-prior-platinum-her2",
            version="1.0.0",
            raw_restriction=disitamab_basis,
            metadata=_entry_meta(
                source,
                ReviewStatus.APPROVED,
                disitamab_row,
            ),
            condition_tree=_all(
                "urothelial-root",
                disitamab_basis,
                _leaf(
                    "urothelial-diagnosis",
                    "diagnosis",
                    {"includes": ["尿路上皮癌"]},
                    "尿路上皮癌",
                ),
                _any(
                    "urothelial-stage",
                    "局部晚期或转移性",
                    _leaf(
                        "urothelial-locally-advanced",
                        "stage",
                        {"equals": "locally_advanced"},
                        "局部晚期",
                    ),
                    _leaf(
                        "urothelial-metastatic",
                        "stage",
                        {"equals": "metastatic"},
                        "转移性",
                    ),
                ),
                _leaf(
                    "urothelial-prior-platinum",
                    "prior_therapy",
                    {"contains_class": "platinum"},
                    "既往接受过含铂化疗",
                ),
                _leaf(
                    "urothelial-her2-overexpression",
                    "biomarker",
                    {
                        "marker_id": "HER2",
                        "method": "IHC",
                        "accepted_values": ["2+", "3+"],
                        "threshold_id": "her2-urothelial-disitamab-ihc",
                    },
                    "HER2过表达",
                ),
            ),
        ),
        EligibilityRule(
            rule_id="elig-pola-dlbcl-untreated-2025",
            drug_concept_id="polatuzumab-vedotin",
            indication_branch_id="dlbcl-untreated",
            version="1.0.0",
            raw_restriction=pola_basis,
            metadata=_entry_meta(source, ReviewStatus.APPROVED, pola_row),
            condition_tree=_all(
                "pola-untreated-root",
                pola_basis,
                _leaf(
                    "pola-untreated-diagnosis",
                    "diagnosis",
                    {"includes": ["弥漫大B细胞淋巴瘤", "DLBCL"]},
                    "弥漫大B细胞淋巴瘤",
                ),
                _leaf(
                    "pola-untreated-adult",
                    "diagnosis",
                    {"age_gte": 18},
                    "成人患者",
                ),
                _leaf(
                    "pola-no-prior-treatment",
                    "prior_therapy",
                    {"equals": "untreated"},
                    "既往未经治疗",
                ),
            ),
        ),
        EligibilityRule(
            rule_id="elig-pola-dlbcl-rr-transplant-2025",
            drug_concept_id="polatuzumab-vedotin",
            indication_branch_id="dlbcl-relapsed-refractory-transplant-ineligible",
            version="1.0.0",
            raw_restriction=pola_basis,
            metadata=_entry_meta(source, ReviewStatus.APPROVED, pola_row),
            condition_tree=_all(
                "pola-rr-root",
                pola_basis,
                _leaf(
                    "pola-rr-diagnosis",
                    "diagnosis",
                    {"includes": ["弥漫大B细胞淋巴瘤", "DLBCL"]},
                    "弥漫大B细胞淋巴瘤",
                ),
                _any(
                    "pola-rr-status",
                    "复发或难治性",
                    _leaf(
                        "pola-relapsed",
                        "treatment_status",
                        {"equals": "relapsed"},
                        "复发",
                    ),
                    _leaf(
                        "pola-refractory",
                        "treatment_status",
                        {"equals": "refractory"},
                        "难治性",
                    ),
                ),
                _leaf(
                    "pola-transplant-ineligible",
                    "clinician_assessment",
                    {"equals": "hsct_ineligible"},
                    "不适合接受造血干细胞移植",
                    documentation_template=(
                        "患者{age}岁且已多线治疗；如拟使用该药，建议病程中补充"
                        "“不适合造血干细胞移植”及简要原因，避免因文书缺项影响医保报销。"
                    ),
                ),
            ),
        ),
    ]


def _marker_hits(text: str) -> list[str]:
    hits = []
    for marker in MARKERS:
        if re.search(re.escape(marker), text, re.I):
            hits.append(marker)
    return sorted(hits)


def _cancer_contexts(text: str) -> list[str]:
    hits = [keyword for keyword in CANCER_KEYWORDS if keyword in text]
    return sorted(set(hits)) or ["UNRESOLVED"]


def _source_insurance_rows(source_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for generic_name, drug in sorted(source_data.get("drugs", {}).items()):
        entries = drug.get("entries", []) if isinstance(drug, dict) else drug
        for entry in entries or []:
            if (
                entry.get("source_type") == "insurance"
                and entry.get("rule_type") == "限适应症"
            ):
                basis = str(entry.get("basis") or "").strip()
                if not basis:
                    continue
                start, end, validity_source, raw_validity = (
                    _effective_range_for_restriction(
                        source_data,
                        drug,
                        basis,
                    )
                )
                row = {
                    "generic_name": generic_name,
                    "codes": sorted(drug.get("codes", [])),
                    "basis": basis,
                    "source_refs": sorted(entry.get("source_refs", [])),
                    "effective_from": start.isoformat(),
                    "effective_to": end.isoformat() if end is not None else None,
                    "validity_source": validity_source,
                    "raw_validity": raw_validity,
                }
                row["source_restriction_id"] = (
                    f"restriction-{_restriction_key(row)}"
                )
                rows.append(row)
    return rows


def _restriction_key(row: dict[str, Any]) -> str:
    raw = f"{row['generic_name']}\0{row['basis']}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def build_assets(
    source_path: Path = SOURCE_KB,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_data = json.loads(source_path.read_text(encoding="utf-8"))
    source = _source_ref(source_path)
    rows = _source_insurance_rows(source_data)
    rows_by_generic = {row["generic_name"]: row for row in rows}
    manifest_validations = _validate_manual_branch_manifest(rows_by_generic)
    approved_rules = _approved_rules(source, rows_by_generic)
    disitamab_row = rows_by_generic["注射用维迪西妥单抗"]

    needs_review_branches: list[dict[str, Any]] = []
    for row in rows:
        manifest = MANUAL_BRANCH_MANIFEST.get(row["generic_name"])
        if manifest is not None:
            for branch in manifest["branches"]:
                if branch["review_status"] != "needs_review":
                    continue
                branch_id = branch["branch_id"]
                needs_review_branches.append(
                    {
                        "candidate_id": f"candidate-{branch_id}",
                        "branch_id": branch_id,
                        **row,
                        "branch_text": branch["source_text"],
                        "candidate_tree": {
                            "node_id": f"unsupported-{branch_id}",
                            "kind": "leaf",
                            "criterion_id": f"unsupported-{branch_id}",
                            "criterion_type": "unsupported",
                            "expected": {
                                **branch.get("expected", {}),
                                "raw_branch": branch["source_text"],
                            },
                        },
                        "review_status": "needs_review",
                        "reasons": [branch["reason"]],
                    }
                )
            continue
        key = _restriction_key(row)
        needs_review_branches.append(
            {
                "candidate_id": f"candidate-{key}",
                "branch_id": f"unresolved-{key}",
                **row,
                "branch_text": row["basis"],
                "candidate_tree": {
                    "node_id": f"unsupported-{key}",
                    "kind": "leaf",
                    "criterion_id": f"unsupported-{key}",
                    "criterion_type": "unsupported",
                    "expected": {"raw_restriction": row["basis"]},
                },
                "review_status": "needs_review",
                "reasons": ["尚未完成结构化条件拆分或医学来源审核"],
            }
        )

    eligibility = EligibilityRulesAsset(
        metadata=_asset_meta(source, approved_rules),
        entries=approved_rules,
    )
    eligibility_raw = eligibility.model_dump(mode="json")
    eligibility_raw["metadata"]["checksum"] = asset_payload_checksum(eligibility_raw)

    approved_her2 = BiomarkerRule(
        entry_id="her2-urothelial-disitamab-ihc",
        marker_id="HER2",
        aliases=[
            MarkerAlias(
                value=alias,
                alias_type="gene" if alias == "ERBB2" else "protein",
            )
            for alias in ALIASES["HER2"]
        ],
        observation_type="protein_expression",
        methods=[BiomarkerMethod.IHC],
        cancer_contexts=["尿路上皮癌"],
        policy_context="disitamab-urothelial-insurance",
        specimen_constraints=["肿瘤组织"],
        scoring_system="HER2 IHC categorical score",
        accepted_values=["2+", "3+"],
        threshold={"operator": "in", "values": ["2+", "3+"]},
        raw_condition="HER2过表达（IHC 2+或3+）",
        metadata=_entry_meta(
            source,
            ReviewStatus.APPROVED,
            disitamab_row,
        ),
    )
    gastric_her2 = BiomarkerRule(
        entry_id="her2-gastric-disitamab-ihc-needs-review",
        marker_id="HER2",
        aliases=[
            MarkerAlias(
                value=alias,
                alias_type="gene" if alias == "ERBB2" else "protein",
            )
            for alias in ALIASES["HER2"]
        ],
        observation_type="protein_expression",
        methods=[BiomarkerMethod.IHC],
        cancer_contexts=["胃癌", "胃食管结合部腺癌"],
        policy_context="disitamab-gastric-insurance",
        specimen_constraints=["肿瘤组织"],
        scoring_system="HER2 IHC categorical score",
        accepted_values=["2+", "3+"],
        threshold={"operator": "in", "values": ["2+", "3+"]},
        raw_condition="HER2过表达（IHC 2+或3+）",
        metadata=_entry_meta(
            source,
            ReviewStatus.NEEDS_REVIEW,
            disitamab_row,
        ),
    )
    marker_candidates: list[BiomarkerRule] = [approved_her2, gastric_her2]
    seen: set[tuple[str, str]] = set()
    pathology_review: list[dict[str, Any]] = [
        {
            "entry_id": gastric_her2.entry_id,
            "marker_id": "HER2",
            "generic_name": disitamab_row["generic_name"],
            "source_restriction_id": disitamab_row["source_restriction_id"],
            "indication_branch_id": "gastric-prior-two-systemic-her2",
            "cancer_contexts": gastric_her2.cancer_contexts,
            "policy_context": gastric_her2.policy_context,
            "method": BiomarkerMethod.IHC.value,
            "review_status": "needs_review",
            "raw_condition": gastric_her2.raw_condition,
            "effective_from": disitamab_row["effective_from"],
            "effective_to": disitamab_row["effective_to"],
            "reasons": ["对应胃癌分支尚未获批自动裁决"],
        }
    ]
    for row in rows:
        if row["generic_name"] == "注射用维迪西妥单抗":
            continue
        for marker in _marker_hits(row["basis"]):
            key = (marker, _restriction_key(row))
            if key in seen:
                continue
            seen.add(key)
            observation_type, method = MARKERS[marker]
            entry_id = f"candidate-{marker.lower()}-{key[1]}"
            candidate = BiomarkerRule(
                entry_id=entry_id,
                marker_id=marker,
                aliases=[
                    MarkerAlias(
                        value=alias,
                        alias_type=(
                            "gene"
                            if observation_type
                            in {"sequence_variant", "fusion", "gene_amplification"}
                            else "protein"
                        ),
                    )
                    for alias in ALIASES[marker]
                ],
                observation_type=observation_type,
                methods=[method],
                cancer_contexts=_cancer_contexts(row["basis"]),
                policy_context=f"candidate:{row['generic_name']}",
                accepted_values=[],
                threshold={"raw": row["basis"]},
                raw_condition=row["basis"],
                metadata=_entry_meta(
                    source,
                    ReviewStatus.NEEDS_REVIEW,
                    row,
                ),
            )
            marker_candidates.append(candidate)
            pathology_review.append(
                {
                    "entry_id": entry_id,
                    "marker_id": marker,
                    "generic_name": row["generic_name"],
                    "source_restriction_id": row["source_restriction_id"],
                    "cancer_contexts": candidate.cancer_contexts,
                    "policy_context": candidate.policy_context,
                    "method": method.value,
                    "review_status": "needs_review",
                    "raw_condition": row["basis"],
                    "effective_from": row["effective_from"],
                    "effective_to": row["effective_to"],
                }
            )

    pathology = PathologyKnowledgeAsset(
        metadata=_asset_meta(source, marker_candidates),
        entries=sorted(marker_candidates, key=lambda item: item.entry_id),
    )
    pathology_raw = pathology.model_dump(mode="json")
    pathology_raw["metadata"]["checksum"] = asset_payload_checksum(pathology_raw)

    generic_by_concept = {
        "disitamab-vedotin": "注射用维迪西妥单抗",
        "polatuzumab-vedotin": "注射用维泊妥珠单抗",
    }
    manifest_branches_by_id = {
        branch["branch_id"]: branch
        for manifest in MANUAL_BRANCH_MANIFEST.values()
        for branch in manifest["branches"]
    }
    approved_branches: list[dict[str, Any]] = []
    for rule in approved_rules:
        row = rows_by_generic[generic_by_concept[rule.drug_concept_id]]
        approved_branches.append(
            {
                "source_restriction_id": row["source_restriction_id"],
                "generic_name": row["generic_name"],
                "branch_id": rule.indication_branch_id,
                "rule_id": rule.rule_id,
                "branch_text": manifest_branches_by_id[
                    rule.indication_branch_id
                ]["source_text"],
                "review_status": "approved",
                "effective_from": rule.metadata.effective_from.isoformat(),
                "effective_to": (
                    rule.metadata.effective_to.isoformat()
                    if rule.metadata.effective_to is not None
                    else None
                ),
                "validity_source": row["validity_source"],
                "raw_validity": row["raw_validity"],
            }
        )

    approved_source_ids = [
        item["source_restriction_id"] for item in approved_branches
    ]
    review_source_ids = [
        item["source_restriction_id"] for item in needs_review_branches
    ]
    source_resolutions = []
    for row in rows:
        source_id = row["source_restriction_id"]
        approved_count = approved_source_ids.count(source_id)
        review_count = review_source_ids.count(source_id)
        status = (
            "fully_approved"
            if approved_count and not review_count
            else "partially_approved"
            if approved_count and review_count
            else "needs_review_only"
            if review_count
            else "uncovered"
        )
        source_resolutions.append(
            {
                "source_restriction_id": source_id,
                "generic_name": row["generic_name"],
                "resolution_status": status,
                "approved_branch_count": approved_count,
                "needs_review_branch_count": review_count,
            }
        )
    fully_approved_count = sum(
        item["resolution_status"] == "fully_approved"
        for item in source_resolutions
    )
    partially_approved_count = sum(
        item["resolution_status"] == "partially_approved"
        for item in source_resolutions
    )
    needs_review_only_count = sum(
        item["resolution_status"] == "needs_review_only"
        for item in source_resolutions
    )
    covered_source_ids = set(approved_source_ids) | set(review_source_ids)
    branch_count = len(approved_branches) + len(needs_review_branches)
    expected_manual_branches = {
        (
            rows_by_generic[generic_name]["source_restriction_id"],
            branch["branch_id"],
            branch["review_status"],
        )
        for generic_name, manifest in MANUAL_BRANCH_MANIFEST.items()
        for branch in manifest["branches"]
    }
    materialized_manual_branches = {
        (
            item["source_restriction_id"],
            item["branch_id"],
            item["review_status"],
        )
        for item in approved_branches + needs_review_branches
        if item["generic_name"] in MANUAL_BRANCH_MANIFEST
    }
    invariants = {
        "all_source_restrictions_have_at_least_one_branch": (
            len(covered_source_ids) == len(rows)
        ),
        "source_restriction_partition_is_complete": (
            fully_approved_count
            + partially_approved_count
            + needs_review_only_count
            == len(rows)
        ),
        "manual_manifest_sources_validated": (
            len(manifest_validations) == len(MANUAL_BRANCH_MANIFEST)
            and all(item["status"] == "matched" for item in manifest_validations)
        ),
        "manual_manifest_branches_materialized_exactly": (
            materialized_manual_branches == expected_manual_branches
        ),
        "approved_branches_match_eligibility_entries": (
            len(approved_branches) == len(approved_rules)
        ),
        "needs_review_branches_absent_from_eligibility_asset": not (
            {item["branch_id"] for item in needs_review_branches}
            & {rule.indication_branch_id for rule in approved_rules}
        ),
    }
    if not all(invariants.values()):
        raise AssertionError(f"eligibility 构建覆盖不变量失败: {invariants}")

    review_report = {
        "schema_version": "2.0.0",
        "source_checksum": source.checksum,
        "source_restriction_count": len(rows),
        "covered_source_restriction_count": len(covered_source_ids),
        "fully_approved_source_restriction_count": fully_approved_count,
        "partially_approved_source_restriction_count": partially_approved_count,
        "needs_review_only_source_restriction_count": needs_review_only_count,
        "branch_count": branch_count,
        "approved_branch_count": len(approved_branches),
        "needs_review_branch_count": len(needs_review_branches),
        "invariants": invariants,
        "manual_source_validations": manifest_validations,
        "source_restriction_resolutions": sorted(
            source_resolutions,
            key=lambda item: item["source_restriction_id"],
        ),
        "approved_branches": sorted(
            approved_branches,
            key=lambda item: (item["generic_name"], item["branch_id"]),
        ),
        "needs_review_branches": sorted(
            needs_review_branches,
            key=lambda item: (item["generic_name"], item["branch_id"]),
        ),
    }
    pathology_invariants = {
        "entry_partition_is_complete": (
            len(marker_candidates) == 1 + len(pathology_review)
        ),
        "gastric_her2_policy_is_explicit_needs_review": any(
            item["entry_id"] == "her2-gastric-disitamab-ihc-needs-review"
            and item["review_status"] == "needs_review"
            for item in pathology_review
        ),
    }
    if not all(pathology_invariants.values()):
        raise AssertionError(f"pathology 构建覆盖不变量失败: {pathology_invariants}")
    pathology_report = {
        "schema_version": "2.0.0",
        "source_checksum": source.checksum,
        "approved_entry_ids": ["her2-urothelial-disitamab-ihc"],
        "approved_entry_count": 1,
        "needs_review_entry_count": len(pathology_review),
        "entry_count": len(marker_candidates),
        "invariants": pathology_invariants,
        "needs_review_entries": sorted(
            pathology_review,
            key=lambda item: item["entry_id"],
        ),
    }
    return eligibility_raw, pathology_raw, review_report, pathology_report


def write_assets(
    *,
    source_path: Path = SOURCE_KB,
    eligibility_out: Path = ELIGIBILITY_OUT,
    pathology_out: Path = PATHOLOGY_OUT,
    review_out: Path = REVIEW_OUT,
    pathology_review_out: Path = PATHOLOGY_REVIEW_OUT,
) -> list[Path]:
    payloads = build_assets(source_path)
    paths = [eligibility_out, pathology_out, review_out, pathology_review_out]
    for path, payload in zip(paths, payloads, strict=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(payload))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE_KB)
    args = parser.parse_args()
    for path in write_assets(source_path=args.source):
        print(path)


if __name__ == "__main__":
    main()
