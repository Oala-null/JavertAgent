# -*- coding: utf-8 -*-
"""build_drug_kb 肿瘤药知识库合并单测."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pandas as pd

from javert.tools import drug_audit_lookup as dal


ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "build_drug_kb", ROOT / "scripts" / "build_drug_kb.py"
)
build_drug_kb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_drug_kb)


def _write_oncology(path: Path, drugs: dict) -> None:
    path.write_text(
        json.dumps({"version": "2025.1", "drugs": drugs}, ensure_ascii=False),
        encoding="utf-8",
    )


def _entry(rule_type: str, basis: str) -> dict:
    return {"rule_type": rule_type, "detect_logic": "诊断不符", "basis": basis}


def test_oncology_insurance_replaces_indication_entries_by_code(tmp_path: Path):
    kb = {
        "version": "2.0",
        "drugs": {
            "同名药": {"codes": ["OTHER"], "entries": [_entry("限二线", "二线")]},
            "编码目标": {
                "codes": ["N1"],
                "entries": [
                    _entry("超说明书", "旧说明书"),
                    _entry("限适应症", "旧医保"),
                    _entry("禁忌症", "禁用于某病"),
                ],
            },
        },
    }
    path = tmp_path / "oncology.json"
    _write_oncology(path, {
        "同名药": {
            "codes": ["N1", "N2"],
            "entries": [{
                "rule_type": "限适应症",
                "detect_logic": "不符医保限定",
                "basis": "新医保限定",
                "source_type": "insurance",
            }],
            "effective": {"source_type": "insurance"},
            "aliases": [],
            "sources": {"national_catalog": [{"full": "不应复制"}], "guideline": []},
        },
    })

    merged = build_drug_kb._merge_oncology_kb(kb, path)

    assert set(merged["drugs"]) == {"同名药"}
    target = merged["drugs"]["同名药"]
    assert {entry["rule_type"] for entry in target["entries"]} == {
        "限适应症", "限二线", "禁忌症"
    }
    assert next(e for e in target["entries"] if e["rule_type"] == "限适应症")["basis"] == "新医保限定"
    assert target["codes"] == ["N1", "N2", "OTHER"]
    assert target["oncology"] == {
        "kb_version": "2025.1",
        "effective_source_type": "insurance",
        "source_keys": ["guideline", "national_catalog"],
        "name_fallback": "exact_entity_name",
    }
    assert "sources" not in target
    assert merged["per_type_counts"] == {"限二线": 1, "限适应症": 1, "禁忌症": 1}
    assert merged["version"] == "3.0"
    assert (merged["drug_count"], merged["code_count"], merged["drugs_with_codes"]) == (1, 3, 1)


def test_oncology_without_codes_uses_exact_name(tmp_path: Path):
    kb = {
        "version": "2.0",
        "drugs": {
            "贝伐珠单抗": {
                "codes": [],
                "entries": [_entry("超说明书", "旧内容"), _entry("限二线", "二线")],
            },
        },
    }
    path = tmp_path / "oncology.json"
    _write_oncology(path, {
        "贝伐珠单抗": {
            "codes": [],
            "entries": [_entry("超说明书", "2025 指导原则适应证")],
            "effective": {"source_type": "guideline"},
            "aliases": [],
            "sources": {"guideline": [{}]},
        },
    })

    merged = build_drug_kb._merge_oncology_kb(kb, path)

    assert [e["basis"] for e in merged["drugs"]["贝伐珠单抗"]["entries"]] == [
        "二线", "2025 指导原则适应证"
    ]


def test_missing_oncology_file_keeps_old_kb_unchanged(tmp_path: Path):
    kb = {
        "version": "2.0",
        "per_type_counts": {"超说明书": 1},
        "drugs": {"某药": {"codes": [], "entries": [_entry("超说明书", "旧内容")]}},
    }
    before = copy.deepcopy(kb)

    assert build_drug_kb._merge_oncology_kb(kb, tmp_path / "missing.json") == before


def test_oncology_never_uses_stem_to_merge_similar_drugs(tmp_path: Path):
    kb = {
        "version": "2.0",
        "drugs": {
            "曲妥珠单抗": {"codes": [], "entries": [_entry("禁忌症", "原药禁忌")]},
        },
    }
    path = tmp_path / "oncology.json"
    _write_oncology(path, {
        "德曲妥珠单抗": {
            "codes": [],
            "entries": [_entry("超说明书", "德曲妥珠单抗适应证")],
            "effective": {"source_type": "guideline"},
            "aliases": [],
            "sources": {"guideline": [{}]},
        },
        "曲妥珠单抗注射液": {
            "codes": ["SKIP"],
            "entries": [],
            "effective": {},
            "aliases": [],
            "sources": {},
        },
    })

    merged = build_drug_kb._merge_oncology_kb(kb, path)

    assert set(merged["drugs"]) == {"曲妥珠单抗", "德曲妥珠单抗"}
    assert merged["drugs"]["曲妥珠单抗"]["entries"] == [_entry("禁忌症", "原药禁忌")]
    assert merged["per_type_counts"] == {"超说明书": 1, "禁忌症": 1}


class _LookupLoader:
    def __init__(self, fees: pd.DataFrame):
        self.fees = fees

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return self.fees


def _build_split_oncology_kb(tmp_path: Path) -> tuple[dict, Path]:
    kb = {
        "version": "2.0",
        "drugs": {
            "曲妥珠单抗": {
                "codes": ["CODE-A", "CODE-B"],
                "entries": [_entry("限适应症", "旧统一限定"), _entry("禁忌症", "共同禁忌")],
            },
            "注射用曲妥珠单抗": {
                "codes": ["CODE-A-EXTRA"],
                "entries": [_entry("限二线", "二线保留")],
            },
        },
    }
    oncology_path = tmp_path / "oncology-split.json"
    _write_oncology(oncology_path, {
        "注射用曲妥珠单抗": {
            "codes": ["CODE-A"],
            "entries": [{**_entry("限适应症", "A 医保限定"), "source_type": "insurance"}],
            "effective": {"source_type": "insurance"},
            "aliases": [],
            "sources": {"hospital_catalog": [{}]},
        },
        "曲妥珠单抗注射液": {
            "codes": ["CODE-B"],
            "entries": [{**_entry("超说明书", "B 指导原则适应证"), "source_type": "guideline"}],
            "effective": {"source_type": "guideline"},
            "aliases": [],
            "sources": {"guideline": [{}]},
        },
    })
    merged = build_drug_kb._merge_oncology_kb(kb, oncology_path)
    merged_path = tmp_path / "merged.json"
    merged_path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    return merged, merged_path


def _lookup(merged_path: Path, tmp_path: Path, name: str, code: str, rule_type=None):
    fees = pd.DataFrame({
        "bah": ["H-P1"],
        "medins_list_name": [name],
        "medins_chrgitm_type": ["西药"],
        "med_list_codg": [code],
    })
    return dal.lookup_patient_drugs(
        "P1", _LookupLoader(fees), merged_path, tmp_path / "missing-zd.csv", rule_type
    )


def test_split_oncology_entities_keep_codes_and_lookup_bases_isolated(tmp_path: Path):
    merged, merged_path = _build_split_oncology_kb(tmp_path)

    assert set(merged["drugs"]) == {"注射用曲妥珠单抗", "曲妥珠单抗注射液"}
    assert merged["drugs"]["注射用曲妥珠单抗"]["codes"] == ["CODE-A", "CODE-A-EXTRA"]
    assert merged["drugs"]["曲妥珠单抗注射液"]["codes"] == ["CODE-B"]
    owners: dict[str, list[str]] = {}
    for generic, drug in merged["drugs"].items():
        for code in drug["codes"]:
            owners.setdefault(code, []).append(generic)
    assert all(len(names) == 1 for names in owners.values())

    a = _lookup(merged_path, tmp_path, "曲妥珠单抗", "CODE-A", "限适应症")
    b = _lookup(merged_path, tmp_path, "曲妥珠单抗", "CODE-B", "超说明书")
    assert [(m["generic_name"], m["basis"]) for m in a["matches"]] == [
        ("注射用曲妥珠单抗", "A 医保限定")
    ]
    assert [(m["generic_name"], m["basis"]) for m in b["matches"]] == [
        ("曲妥珠单抗注射液", "B 指导原则适应证")
    ]


def test_split_oncology_nocode_fallback_requires_exact_entity_name(tmp_path: Path):
    _, merged_path = _build_split_oncology_kb(tmp_path)

    exact = _lookup(merged_path, tmp_path, "(集)注射用曲妥珠单抗(商品A)", "")
    vague = _lookup(merged_path, tmp_path, "曲妥珠单抗", "")
    assert {m["generic_name"] for m in exact["matches"]} == {"注射用曲妥珠单抗"}
    assert vague["matches"] == []


def test_conflicted_active_code_is_dropped_from_all_entities(tmp_path: Path):
    kb = {
        "version": "2.0",
        "drugs": {"旧名": {"codes": ["DUP"], "entries": [_entry("限适应症", "旧限定")]}},
    }
    path = tmp_path / "oncology-conflict.json"
    _write_oncology(path, {
        name: {
            "codes": ["DUP"],
            "entries": [_entry("限适应症", basis)],
            "effective": {"source_type": "insurance"},
            "aliases": [],
            "sources": {},
        }
        for name, basis in (("实体A", "A限定"), ("实体B", "B限定"))
    })

    merged = build_drug_kb._merge_oncology_kb(kb, path)

    assert set(merged["drugs"]) == {"实体A", "实体B"}
    assert all(not drug["codes"] for drug in merged["drugs"].values())
