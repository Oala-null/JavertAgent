"""RD10-RD37 精选知识原子提取、映射与确定性覆盖清单。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .ids import canonical_json_bytes, checksum, stable_id
from .models import (
    AtomType,
    CuratedKnowledgeAtom,
    MigrationStatus,
    PreservationTarget,
)
from .sources import build_candidate_universe, normalize_name


_ONCOLOGY_AUTHORITY_SOURCES = frozenset(
    {"guideline", "oncology_drug_asset", "structured_asset"}
)


def _atom(
    rule_id: str,
    oncology: bool,
    rule_status: str,
    field: str,
    atom_type: AtomType,
    payload: dict[str, Any],
    target_kind: PreservationTarget,
    target_id: str,
    source_value: Any | None = None,
) -> CuratedKnowledgeAtom:
    source_checksum = checksum(payload if source_value is None else source_value)
    return CuratedKnowledgeAtom(
        atom_id=stable_id("atom", rule_id, field, atom_type, source_checksum),
        source_rule_id=rule_id,
        oncology=oncology,
        rule_status=rule_status,
        source_field_or_test=field,
        atom_type=atom_type,
        canonical_payload=payload,
        source_checksum=source_checksum,
        migration_status=MigrationStatus.MAPPED,
        target_kind=target_kind,
        target_id=target_id,
    )


def extract_rule_atoms(path: Path, *, oncology: bool) -> list[CuratedKnowledgeAtom]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rule_id = str(raw["rule_id"])
    rule_status = str(raw.get("status") or "")
    keywords = [str(value) for value in raw.get("trigger_keywords") or []]
    drug_name = keywords[0] if keywords else str(raw.get("question") or rule_id)
    rule_type = str(raw.get("drug_rule_type") or raw.get("violation_type") or "")
    prompt = str(raw.get("prompt_addon") or "")
    policies = []
    for token, policy in (
        ("自费", "SELF_PAY_GATE"),
        ("病案首页诊断", "PRIMARY_DIAGNOSIS_FIRST"),
        ("同名异药", "DOSAGE_FORM_MISMATCH_REVIEW"),
        ("数据盲区", "MISSING_DATA_WARNING"),
        ("示踪剂", "TRACER_SURGERY_CONTEXT"),
    ):
        if token in prompt:
            policies.append(policy)
    atoms = [
        _atom(
            rule_id,
            oncology,
            rule_status,
            "question+drug_rule_type",
            AtomType.SOURCE_RULE,
            {"question": raw.get("question", ""), "drug_rule_type": rule_type},
            PreservationTarget.CONDITION,
            stable_id("bulk-condition", normalize_name(drug_name), rule_type),
        ),
        _atom(
            rule_id,
            oncology,
            rule_status,
            "trigger_keywords",
            AtomType.NORMALIZATION,
            {"aliases": sorted(set(keywords)), "canonical_name": drug_name},
            PreservationTarget.DICTIONARY,
            stable_id("drug-dictionary", normalize_name(drug_name)),
        ),
        _atom(
            rule_id,
            oncology,
            rule_status,
            "prompt_addon:evidence_policy",
            AtomType.EVIDENCE_POLICY,
            {"policies": policies},
            PreservationTarget.EVALUATOR_POLICY,
            stable_id("evaluator-policy", rule_type, "self-pay-and-evidence"),
            source_value=prompt,
        ),
        _atom(
            rule_id,
            oncology,
            rule_status,
            "notes",
            AtomType.DOCUMENTATION_GUIDANCE,
            {"text": str(raw.get("notes") or "")},
            PreservationTarget.REVIEW_GUIDANCE,
            stable_id("review-guidance", rule_id),
        ),
        _atom(
            rule_id,
            oncology,
            rule_status,
            "expected_signal",
            AtomType.REGRESSION_GOLD,
            {"text": str(raw.get("expected_signal") or "")},
            PreservationTarget.REGRESSION_CASE,
            stable_id("regression-case", rule_id),
        ),
    ]
    if "合理临床外延" in prompt or "合理外延" in prompt:
        atoms.append(
            _atom(
                rule_id,
                oncology,
                rule_status,
                "prompt_addon:clinical_extension",
                AtomType.CLINICAL_EXTENSION,
                {"extension_policy": "CLINICAL_RELEVANCE_MAY_SATISFY", "rule_type": rule_type},
                PreservationTarget.REVIEW_GUIDANCE,
                stable_id("clinical-extension", rule_id),
                source_value=prompt,
            )
        )
    return atoms


def build_manifest(root: Path) -> dict[str, Any]:
    atoms: list[CuratedKnowledgeAtom] = []
    rule_status: dict[str, str] = {}
    authority_by_name = {
        normalize_name(candidate.canonical_name): any(
            candidate.source_membership.get(source, False)
            for source in _ONCOLOGY_AUTHORITY_SOURCES
        )
        for candidate in build_candidate_universe(root)
    }
    for number in range(10, 38):
        path = root / f"configs/rules/RD{number:02d}.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rule_status[path.stem] = str(raw.get("status") or "")
        keywords = [str(value) for value in raw.get("trigger_keywords") or []]
        drug_name = keywords[0] if keywords else str(raw.get("question") or path.stem)
        normalized_name = normalize_name(drug_name)
        if normalized_name not in authority_by_name:
            raise ValueError(f"{path.stem} 药名未进入五源候选宇宙")
        atoms.extend(
            extract_rule_atoms(path, oncology=authority_by_name[normalized_name])
        )
    rows = [atom.model_dump(mode="json") for atom in sorted(atoms, key=lambda item: item.atom_id)]
    by_type = Counter(row["atom_type"] for row in rows)
    by_status = Counter(row["migration_status"] for row in rows)
    by_oncology = Counter("oncology" if row["oncology"] else "non_oncology" for row in rows)
    by_rule_status = Counter(row["rule_status"] for row in rows)
    allowed_rule_status = {
        rule_id: ("abandoned" if all(row["migration_status"] == "VERIFIED" for row in rows if row["source_rule_id"] == rule_id) else "drafting")
        for rule_id in sorted(rule_status)
    }
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "atoms": rows,
        "counts": {
            "rules": len(rule_status),
            "atoms": len(rows),
            "by_atom_type": dict(sorted(by_type.items())),
            "by_migration_status": dict(sorted(by_status.items())),
            "by_oncology": dict(sorted(by_oncology.items())),
            "by_rule_status": dict(sorted(by_rule_status.items())),
        },
        "rule_status": rule_status,
        "allowed_rule_status": allowed_rule_status,
    }
    manifest["manifest_checksum"] = checksum(manifest)
    return manifest


def write_manifest(root: Path, output: Path, report_output: Path) -> dict[str, Any]:
    manifest = build_manifest(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json_bytes(manifest) + b"\n")
    lines = [
        "# 精选药品知识迁移报告",
        "",
        f"- manifest checksum: `{manifest['manifest_checksum']}`",
        f"- 规则数: {manifest['counts']['rules']}",
        f"- 知识原子数: {manifest['counts']['atoms']}",
        "",
        "| 规则 | 当前 rule_status | 肿瘤归属 | 允许状态 | 原子数 | 未验证数 |",
        "|---|---|---|---|---:|---:|",
    ]
    for rule_id in sorted(manifest["rule_status"]):
        selected = [row for row in manifest["atoms"] if row["source_rule_id"] == rule_id]
        pending = sum(row["migration_status"] != "VERIFIED" for row in selected)
        oncology = "是" if any(row["oncology"] for row in selected) else "否"
        lines.append(
            f"| {rule_id} | {manifest['rule_status'][rule_id]} | {oncology} | {manifest['allowed_rule_status'][rule_id]} | {len(selected)} | {pending} |"
        )
    report_output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest
