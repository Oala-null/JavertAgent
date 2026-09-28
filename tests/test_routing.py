# -*- coding: utf-8 -*-
"""make-rules-code-portable: RuleRouter 编码命中 + 单闸两步 单测 (RuleRouter 首套).

覆盖:
  - 编码前缀命中 (名称不同名仍召回) / 名称命中 / OR 语义
  - 无 trigger_codes 零回归 (编码命中恒假, 退回纯 keyword)
  - 换名模拟: 名换编码不变 → 编码路径命中、keyword 路径 miss (换院核心场景)
  - status 闸保留
  - RouterDecision 不再暴露 Case-A 字段
"""

from __future__ import annotations

from javert.routing.router import RuleRouter
from javert.routing.types import FeeItem, PatientRecord


def _router(rules: list[dict]) -> RuleRouter:
    return RuleRouter(
        violation_dict={"entries": [], "keyword_index": {}},
        active_rules={},
        pruning_rules={},
        javert_index={"rules": rules},
        rule_mapping={},
    )


def _rule(rid: str, *, keywords=(), codes=(), status="ready", priority="P0") -> dict:
    return {
        "rule_id": rid,
        "status": status,
        "priority": priority,
        "trigger_keywords": list(keywords),
        "trigger_codes": list(codes),
    }


def _record(*fees: FeeItem, diagnoses=()) -> PatientRecord:
    return PatientRecord(patient_id="P1", diagnoses=list(diagnoses), fee_items=list(fees))


def _fee(name: str, *, med_code=None, local_code=None, label=None) -> FeeItem:
    return FeeItem(
        item_sn="1",
        medins_list_name=name,
        med_list_codg=med_code,
        medins_list_codg=local_code,
        chrgitm_type=label,
    )


def test_code_prefix_hits_when_name_differs():
    """rule 只靠编码前缀, 项目名是异院异名 (keyword 不命中) 仍召回."""
    rules = [_rule("R001", keywords=["本院PETCT专名"], codes=["C03"])]
    rec = _record(_fee("异院造影检查术", med_code="C0302070300401003"))
    decision = _router(rules).route(rec)
    assert decision.final_rules == ["R001"]


def test_keyword_hit_independent():
    rules = [_rule("R002", keywords=["造影"], codes=[])]
    rec = _record(_fee("脑血管造影"))
    assert _router(rules).route(rec).final_rules == ["R002"]


def test_or_semantics_either_path():
    """名称命中 或 编码命中 任一即保留."""
    rules = [_rule("R003", keywords=["磁共振"], codes=["C99"])]
    only_name = _record(_fee("磁共振平扫"))
    only_code = _record(_fee("完全不相关名", med_code="C9900001"))
    assert _router(rules).route(only_name).final_rules == ["R003"]
    assert _router(rules).route(only_code).final_rules == ["R003"]


def test_no_trigger_codes_zero_regression():
    """空 codes → 编码命中恒假, 命中语义 == 纯 keyword."""
    rules = [_rule("R004", keywords=["不存在的项目名"], codes=[])]
    rec = _record(_fee("CT平扫", med_code="C0401011180"))
    assert _router(rules).route(rec).final_rules == []


def test_rename_simulation_code_survives_keyword_misses():
    """换院核心: 同一项目改名, 编码不变 → 编码规则命中、纯 keyword 规则漏检."""
    kw_rule = _rule("R_KW", keywords=["口腔颌面软组织清创术"], codes=[])
    code_rule = _rule("R_CODE", keywords=["口腔颌面软组织清创术"], codes=["C03470110900"])
    # 换院后同一编码项目被命名成完全不同的字面名
    renamed = _record(_fee("颌面部创面处理", med_code="C0347011090000003"))
    assert _router([kw_rule]).route(renamed).final_rules == []            # keyword 路径 miss
    assert _router([code_rule]).route(renamed).final_rules == ["R_CODE"]  # 编码路径命中


def test_local_code_and_label_also_in_code_set():
    """本院码 medins_list_codg 与类别标签 chrgitm_type 也进编码集."""
    rules = [_rule("R005", keywords=[], codes=["01-08"])]
    rec = _record(_fee("某耗材", local_code="01-08-09"))
    assert _router(rules).route(rec).final_rules == ["R005"]


def test_status_gate_prunes_abandoned():
    rules = [_rule("R006", keywords=["造影"], codes=[], status="abandoned")]
    rec = _record(_fee("脑血管造影"))
    assert _router(rules).route(rec).final_rules == []


def test_decision_has_no_case_a_fields():
    rules = [_rule("R007", keywords=["造影"])]
    decision = _router(rules).route(_record(_fee("脑造影")))
    assert not hasattr(decision, "overlapping_kept")
    assert not hasattr(decision, "javert_only_kept")
    assert "pruned_applicable" not in decision.stats
