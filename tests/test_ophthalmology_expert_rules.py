# -*- coding: utf-8 -*-
"""R319-R322 眼科专家扩展规则：加载、Router 召回与预检。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from javert.audit.precheck import CLEAN, FACTS, run_precheck
from javert.audit.rule_loader import load_rule
from javert.routing.router import RuleRouter
from javert.routing.types import FeeItem, PatientRecord

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("rule_id", ["R319", "R320", "R321", "R322"])
def test_expert_rule_is_ready_and_complete(rule_id: str):
    rule = load_rule(ROOT / "configs" / "rules" / f"{rule_id}.yaml")
    assert rule.status == "ready"
    assert rule.domain == "眼科"
    assert len(rule.prompt_addon) >= 500
    assert len(rule.trigger_keywords) >= 5
    assert rule.trigger_codes
    assert rule.suggested_tools
    assert rule.expected_signal
    assert rule.precheck is not None
    assert "专家扩展规则" in rule.notes


def _record(*fees: FeeItem) -> PatientRecord:
    return PatientRecord(patient_id="SYNTH-EYE-ROUTER", fee_items=list(fees))


def _fee(name: str, code: str = "") -> FeeItem:
    return FeeItem(
        item_sn=name,
        medins_list_name=name,
        med_list_codg=code,
    )


def test_router_recalls_name_suffixes_and_codes():
    router = RuleRouter.from_defaults()

    billing = router.route(_record(_fee("眼压检查费/单侧"))).final_rules
    treatment = router.route(_record(_fee("睑治疗费/单睑"))).final_rules
    bedside = router.route(_record(_fee("移动检查别名", "003107010010003-A310701001-4"))).final_rules

    assert "R319" in billing
    assert "R320" in treatment
    assert "R321" in bedside


def test_router_and_precheck_handle_ab_ultrasound_coexistence():
    router = RuleRouter.from_defaults()
    fees = [
        _fee("A型超声检查/单侧", "012302010010000"),
        _fee("B型超声检查/部位", "012302020010000"),
    ]
    assert "R322" in router.route(_record(*fees)).final_rules

    rule = load_rule(ROOT / "configs" / "rules" / "R322.yaml")
    frame = pd.DataFrame([{
        "medins_list_name": fee.medins_list_name,
        "med_list_codg": fee.med_list_codg,
        "cnt": 1,
        "det_item_fee_sumamt": 30,
    } for fee in fees])
    result = run_precheck(rule.precheck, frame)
    assert result.outcome == FACTS
    assert "不预设二者为附属" in result.fact_block
    assert "应打包" not in result.fact_block


@pytest.mark.parametrize(
    ("rule_id", "target_name", "expected_outcome"),
    [
        ("R319", "眼压检查费/单侧", FACTS),
        ("R320", "睑治疗费/单睑", FACTS),
        ("R321", "床头心电图/次", "review"),
    ],
)
def test_presence_precheck_only_runs_for_target_fee(
    rule_id: str, target_name: str, expected_outcome: str
):
    rule = load_rule(ROOT / "configs" / "rules" / f"{rule_id}.yaml")
    unrelated = pd.DataFrame([{
        "medins_list_name": "普通诊查费", "cnt": 1, "det_item_fee_sumamt": 10,
    }])
    target = pd.DataFrame([{
        "medins_list_name": target_name, "cnt": 1, "det_item_fee_sumamt": 10,
    }])

    assert run_precheck(rule.precheck, unrelated).outcome == CLEAN
    assert run_precheck(rule.precheck, target).outcome == expected_outcome
