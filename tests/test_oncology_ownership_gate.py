from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from javert.audit.rule import Rule
from javert.commands.audit_patient import _resolve_selection
from javert.data.loader import DataLoader
from javert.routing.router import RuleRouter
from javert.routing.types import FeeItem, PatientRecord
from javert.tools.drug_audit_lookup import lookup_patient_drugs


class _Loader(DataLoader):
    def __init__(self, fees: pd.DataFrame):
        self._fees = fees

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return self._fees

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        return pd.DataFrame()

    def all_fees(self) -> pd.DataFrame:
        return self._fees

    def all_notes(self) -> pd.DataFrame:
        return pd.DataFrame()


def _rule(rule_id: str, status: str) -> Rule:
    return Rule(
        rule_id=rule_id,
        domain="药品",
        violation_type="合成测试",
        question="合成测试",
        status=status,
        priority="P1",
    )


def _write_ownership_kb(path: Path) -> Path:
    entries = [
        {
            "rule_type": "限适应症",
            "source_type": "insurance",
            "basis": "医保限定合成依据",
            "source_refs": ["insurance:synthetic"],
        },
        {
            "rule_type": "超说明书",
            "source_type": "guideline",
            "basis": "指南适应证合成依据",
            "source_refs": ["guideline:synthetic"],
        },
        {"rule_type": "限二线", "basis": "二线合成依据"},
        {"rule_type": "禁忌症", "basis": "禁忌合成依据"},
    ]
    path.write_text(
        json.dumps(
            {
                "version": "ownership-gate-test",
                "drugs": {
                    "合成肿瘤药": {
                        "codes": ["ONC001"],
                        "oncology": {"drug_concept_id": "oncology-synthetic"},
                        "entries": entries,
                    },
                    "合成普通药": {
                        "codes": ["GEN001"],
                        "entries": entries,
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _lookup(
    *,
    loader: _Loader,
    kb_path: Path,
    zd_path: Path,
    rule_id: str,
    rule_type: str | None,
    source_type: str | None = None,
    oncology_kb_path: Path | None = None,
) -> dict:
    return lookup_patient_drugs(
        "OWNERSHIP-PATIENT",
        loader,
        kb_path,
        zd_path,
        rule_type=rule_type,
        source_type=source_type,
        oncology_v2_mode="on",
        audit_rule_id=rule_id,
        oncology_kb_path=oncology_kb_path,
    )


def test_oncology_eligibility_ownership_is_disjoint_across_all_bulk_rules(
    tmp_path: Path,
) -> None:
    kb_path = _write_ownership_kb(tmp_path / "drug-kb.json")
    zd_path = tmp_path / "zd.csv"
    pd.DataFrame(columns=["ba_id", "diag_name"]).to_csv(zd_path, index=False)
    loader = _Loader(
        pd.DataFrame(
            {
                "bah": ["H-OWNERSHIP-PATIENT"] * 2,
                "cnt": [1, 1],
                "medins_list_name": ["合成肿瘤药", "合成普通药"],
                "medins_chrgitm_type": ["西药", "西药"],
                "med_list_codg": ["ONC001", "GEN001"],
            }
        )
    )

    # 即使旧 RD04 prompt 仍传医保过滤，owner gate 也必须返回两个肿瘤资格 scope。
    rd04 = _lookup(
        loader=loader,
        kb_path=kb_path,
        zd_path=zd_path,
        rule_id="RD04",
        rule_type="限适应症",
        source_type="insurance",
    )
    assert {
        (item["generic_name"], item["source_type"])
        for item in rd04["matches"]
    } == {
        ("合成肿瘤药", "insurance"),
        ("合成肿瘤药", "guideline"),
    }

    for rule_id, rule_type in (
        ("R007", "限适应症"),
        ("RD01", "超说明书"),
        ("RD02", "限二线"),
    ):
        result = _lookup(
            loader=loader,
            kb_path=kb_path,
            zd_path=zd_path,
            rule_id=rule_id,
            rule_type=rule_type,
        )
        assert {item["generic_name"] for item in result["matches"]} == {
            "合成普通药"
        }

    rd03 = _lookup(
        loader=loader,
        kb_path=kb_path,
        zd_path=zd_path,
        rule_id="RD03",
        rule_type="禁忌症",
    )
    assert {item["generic_name"] for item in rd03["matches"]} == {
        "合成肿瘤药",
        "合成普通药",
    }


def test_published_oncology_drug_asset_overlays_legacy_without_dropping_general_kb(
    tmp_path: Path,
) -> None:
    legacy_path = _write_ownership_kb(tmp_path / "legacy-drug-kb.json")
    released_path = tmp_path / "oncology_drug_kb.json"
    released_path.write_text(
        json.dumps(
            {
                "version": "published-test",
                "drugs": {
                    "合成肿瘤药": {
                        "codes": ["ONC-RELEASED"],
                        "oncology": {
                            "drug_concept_id": "oncology-synthetic",
                            "name_fallback": "exact_entity_name",
                        },
                        "entries": [
                            {
                                "rule_type": "限适应症",
                                "source_type": "insurance",
                                "basis": "published 医保限定",
                            },
                            {
                                "rule_type": "限适应症",
                                "source_type": "guideline",
                                "basis": "published 指南适应证",
                            },
                            {"rule_type": "禁忌症", "source_type": "safety", "basis": "published 安全边界"},
                        ],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    zd_path = tmp_path / "zd.csv"
    pd.DataFrame(columns=["ba_id", "diag_name"]).to_csv(zd_path, index=False)
    loader = _Loader(
        pd.DataFrame(
            {
                "bah": ["H-OWNERSHIP-PATIENT"] * 3,
                "cnt": [1, 1, 1],
                "medins_list_name": ["合成肿瘤药", "合成肿瘤药", "合成普通药"],
                "medins_chrgitm_type": ["西药", "西药", "西药"],
                "med_list_codg": ["ONC001", "ONC-RELEASED", "GEN001"],
            }
        )
    )

    rd04 = _lookup(
        loader=loader,
        kb_path=legacy_path,
        oncology_kb_path=released_path,
        zd_path=zd_path,
        rule_id="RD04",
        rule_type="限适应症",
    )
    assert {item["source_type"] for item in rd04["matches"]} == {
        "insurance",
        "guideline",
    }
    assert all(item["fee_codes"] == ["ONC-RELEASED"] for item in rd04["matches"])

    rd03 = _lookup(
        loader=loader,
        kb_path=legacy_path,
        oncology_kb_path=released_path,
        zd_path=zd_path,
        rule_id="RD03",
        rule_type="禁忌症",
    )
    assert {item["generic_name"] for item in rd03["matches"]} == {
        "合成肿瘤药",
        "合成普通药",
    }
    released_oncology = next(
        item for item in rd03["matches"] if item["generic_name"] == "合成肿瘤药"
    )
    assert released_oncology["basis"] == "published 安全边界"


def test_published_oncology_asset_preserves_legacy_rd03_when_release_has_no_safety(
    tmp_path: Path,
) -> None:
    legacy_path = _write_ownership_kb(tmp_path / "legacy-drug-kb.json")
    released_path = tmp_path / "oncology_drug_kb.json"
    released_path.write_text(
        json.dumps(
            {
                "version": "published-test",
                "drugs": {
                    "合成肿瘤药": {
                        "codes": ["ONC-RELEASED"],
                        "oncology": {"drug_concept_id": "oncology-synthetic"},
                        "entries": [
                            {
                                "rule_type": "限适应症",
                                "source_type": "insurance",
                                "basis": "published 医保限定",
                            }
                        ],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    zd_path = tmp_path / "zd.csv"
    pd.DataFrame(columns=["ba_id", "diag_name"]).to_csv(zd_path, index=False)
    loader = _Loader(
        pd.DataFrame(
            {
                "bah": ["H-OWNERSHIP-PATIENT"],
                "cnt": [1],
                "medins_list_name": ["合成肿瘤药"],
                "medins_chrgitm_type": ["西药"],
                "med_list_codg": ["ONC-RELEASED"],
            }
        )
    )

    rd03 = _lookup(
        loader=loader,
        kb_path=legacy_path,
        oncology_kb_path=released_path,
        zd_path=zd_path,
        rule_id="RD03",
        rule_type="禁忌症",
    )
    assert len(rd03["matches"]) == 1
    assert rd03["matches"][0]["basis"] == "禁忌合成依据"


def test_curated_drafting_and_abandoned_rules_require_explicit_selection() -> None:
    rules = {
        "R045": _rule("R045", "drafting"),
        "RD10": _rule("RD10", "drafting"),
        "RD11": _rule("RD11", "abandoned"),
    }

    selected, _, _ = _resolve_selection(rules, "P1", None)
    assert [item.rule_id for item in selected] == ["R045"]

    explicit, _, forced = _resolve_selection(rules, "P1", "RD10,RD11")
    assert [item.rule_id for item in explicit] == ["RD10", "RD11"]
    assert forced == ["RD11"]


@pytest.mark.parametrize("status", ["drafting", "abandoned"])
def test_router_never_executes_non_ready_curated_status(status: str) -> None:
    router = RuleRouter(
        violation_dict={"entries": [], "keyword_index": {}},
        active_rules={},
        pruning_rules={},
        javert_index={
            "rules": [
                {
                    "rule_id": "RD20",
                    "status": status,
                    "priority": "P1",
                    "trigger_keywords": ["合成药"],
                    "trigger_codes": [],
                }
            ]
        },
        rule_mapping={},
    )
    record = PatientRecord(
        patient_id="OWNERSHIP-PATIENT",
        diagnoses=[],
        fee_items=[FeeItem(item_sn="1", medins_list_name="合成药")],
    )

    assert router.route(record).final_rules == []
