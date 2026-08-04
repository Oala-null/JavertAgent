"""从当前文件动态生成 authoring 基线，不在长期文档手抄库存数字。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .ids import canonical_json_bytes, checksum


SOURCE_FILES = {
    "hospital_catalog": Path("docs/药品限制/药品总库医院_202606.xls"),
    "national_catalog": Path("docs/药品限制/2025药品目录（国家）.pdf"),
    "guideline": Path("docs/药品限制/新型抗肿瘤药物临床应用指导原则（2025年版）.pdf"),
    "oncology_drug_asset": Path("configs/oncology_drug_kb.json"),
    "eligibility_asset": Path("configs/oncology_eligibility_rules.json"),
    "pathology_asset": Path("configs/pathology_biomarker_kb.json"),
    "regimen_asset": Path("configs/oncology_regimen_kb.json"),
    "regimen_alias_candidates": Path("docs/oncology/regimen_alias_candidates.json"),
}


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rule_snapshot(root: Path) -> dict[str, Any]:
    paths = sorted((root / "configs/rules").glob("RD*.yaml"))
    entries = [{"path": str(path.relative_to(root)), "checksum": file_checksum(path)} for path in paths]
    return {"count": len(entries), "checksum": checksum(entries), "files": entries}


def build_baseline(root: Path) -> dict[str, Any]:
    paths = {name: root / path for name, path in SOURCE_FILES.items()}
    missing = [str(path.relative_to(root)) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"缺少 authoring 基线来源: {missing}")

    drug = _json(paths["oncology_drug_asset"])
    eligibility = _json(paths["eligibility_asset"])
    pathology = _json(paths["pathology_asset"])
    regimen = _json(paths["regimen_asset"])
    aliases = _json(paths["regimen_alias_candidates"])

    drugs = list((drug.get("drugs") or {}).values())
    effective = [item.get("effective") for item in drugs if item.get("effective")]
    insurance = [item for item in effective if item.get("source_type") == "insurance"]
    guideline = [item for item in effective if item.get("source_type") == "guideline"]
    approved_branches = [
        item for item in eligibility.get("entries", [])
        if item.get("metadata", {}).get("review_status") == "approved"
    ]
    approved_pathology = [
        item for item in pathology.get("entries", [])
        if item.get("metadata", {}).get("review_status") == "approved"
    ]
    approved_regimens = [
        item for item in regimen.get("entries", [])
        if item.get("metadata", {}).get("review_status") == "approved"
    ]

    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "sources": {
            name: {
                "path": str(path.relative_to(root)),
                "checksum": file_checksum(path),
                "size_bytes": path.stat().st_size,
            }
            for name, path in sorted(paths.items())
        },
        "rule_yaml_snapshot": _rule_snapshot(root),
        "counts": {
            "drug_entities": len(drugs),
            "insurance_restrictions": len(insurance),
            "guideline_indications": len(guideline),
            "approved_condition_branches": len(approved_branches),
            "pathology_candidates": len(pathology.get("entries", [])),
            "approved_pathology_entries": len(approved_pathology),
            "approved_regimens": len(approved_regimens),
            "regimen_alias_candidates": len(aliases.get("candidates", [])),
        },
    }
    report["snapshot_checksum"] = checksum(report)
    return report


def write_baseline(root: Path, output: Path) -> dict[str, Any]:
    report = build_baseline(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json_bytes(report) + b"\n")
    return report
