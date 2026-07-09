# -*- coding: utf-8 -*-
"""precheck 引擎 + M1 迁移解析 + Rule.precheck round-trip (pilot-deterministic-precheck)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import yaml as pyyaml

from javert.audit.precheck import CLEAN, FACTS, SKIP, run_precheck
from javert.audit.rule import PrecheckSpec, Rule
from javert.audit.rule_writer import dump_rule_to_string

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_SPEC = importlib.util.spec_from_file_location(
    "init_m1_precheck", PROJECT_ROOT / "scripts" / "init_m1_precheck.py"
)
migrate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migrate)


def _fee_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


_SPEC_AB = PrecheckSpec(a_items=["全身断层", "PET-CT"], b_items=["图文报告", "报告费"])


# ========== 引擎 (Task 2.3) ==========
def test_no_a_short_circuits_clean():
    df = _fee_df([{"medins_list_name": "血常规", "cnt": 1, "det_item_fee_sumamt": 10}])
    r = run_precheck(_SPEC_AB, df)
    assert r.outcome == CLEAN
    assert r.precheck_tag == "无A项"


def test_a_only_short_circuits_clean():
    df = _fee_df([{"medins_list_name": "PET-CT全身断层显像", "cnt": 1, "det_item_fee_sumamt": 3000}])
    r = run_precheck(_SPEC_AB, df)
    assert r.outcome == CLEAN
    assert r.precheck_tag == "无B项"


def test_a_and_b_facts_hold_with_anchors():
    df = _fee_df([
        {"medins_list_name": "PET-CT全身断层显像", "cnt": 1, "det_item_fee_sumamt": 3000,
         "fee_ocur_time": "2024-01-02 00:00:00"},
        {"medins_list_name": "PET-CT图文报告费", "cnt": 1, "det_item_fee_sumamt": 50,
         "fee_ocur_time": "2024-01-02 00:00:00"},
    ])
    r = run_precheck(_SPEC_AB, df)
    assert r.outcome == FACTS
    assert r.fact_block  # 非空
    assert "search_notes" in r.fact_block and "附属" in r.fact_block
    # evidence 带确定性费用行锚点 (source 含 fee → hit_resolver 可 join)
    locs = {(e.source, e.locator) for e in r.evidence}
    assert ("search_fees", "PET-CT全身断层显像") in locs
    assert ("search_fees", "PET-CT图文报告费") in locs


def test_fully_refunded_b_not_counted():
    """B 项被等量退费抵消 (净≤0) → 不算 B 命中 → 短路 CLEAN."""
    df = _fee_df([
        {"medins_list_name": "PET-CT全身断层显像", "med_list_codg": "A1", "cnt": 1,
         "det_item_fee_sumamt": 3000, "fee_ocur_time": "2024-01-02 00:00:00"},
        {"medins_list_name": "PET-CT图文报告费", "med_list_codg": "B1", "cnt": 1,
         "det_item_fee_sumamt": 50, "fee_ocur_time": "2024-01-02 00:00:00"},
        {"medins_list_name": "PET-CT图文报告费", "med_list_codg": "B1", "cnt": -1,
         "det_item_fee_sumamt": -50, "fee_ocur_time": "2024-01-03 00:00:00"},
    ])
    r = run_precheck(_SPEC_AB, df)
    assert r.outcome == CLEAN
    assert r.precheck_tag == "无B项"


def test_empty_or_missing_data_skips():
    assert run_precheck(_SPEC_AB, None).outcome == SKIP
    assert run_precheck(_SPEC_AB, pd.DataFrame()).outcome == SKIP
    # 缺 A 或 B 集 → skip (迁移不全的规则)
    assert run_precheck(PrecheckSpec(a_items=["x"], b_items=[]), _fee_df(
        [{"medins_list_name": "x", "cnt": 1}]
    )).outcome == SKIP


# ========== companion 模式 (add-fabrication-burden-of-proof) ==========
_SPEC_COMP = PrecheckSpec(
    a_items=["溶栓术"], b_items=["尿激酶", "阿替普酶", "rt-PA"], mode="companion"
)


def test_companion_no_a_clean():
    """companion: 无术式命中 → clean 短路."""
    df = _fee_df([{"medins_list_name": "取栓术", "cnt": 1, "det_item_fee_sumamt": 900}])
    r = run_precheck(_SPEC_COMP, df)
    assert r.outcome == CLEAN
    assert r.precheck_tag == "无术式项"


def test_companion_a_no_b_facts_with_anchor():
    """companion: 收术式但无配套 → facts + A 命中费用行机器锚点."""
    df = _fee_df([{
        "medins_list_name": "经皮穿刺脑血管腔内溶栓术", "cnt": 1,
        "det_item_fee_sumamt": 1100, "fee_ocur_time": "2024-12-22 00:00:00",
    }])
    r = run_precheck(_SPEC_COMP, df)
    assert r.outcome == FACTS
    assert r.precheck_tag == "收术式无配套待核反证"
    assert "search_notes" in r.fact_block and "配套" in r.fact_block
    locs = {(e.source, e.locator) for e in r.evidence}
    assert ("search_fees", "经皮穿刺脑血管腔内溶栓术") in locs
    # B 无命中 → evidence 只含 A
    assert all(e.source == "search_fees" for e in r.evidence)


def test_companion_a_and_b_skip_no_bias():
    """companion: 术式 + 配套均在场 → skip, 无事实块 (不注偏置)."""
    df = _fee_df([
        {"medins_list_name": "经皮穿刺脑血管腔内溶栓术", "cnt": 1, "det_item_fee_sumamt": 1100},
        {"medins_list_name": "注射用阿替普酶", "cnt": 1, "det_item_fee_sumamt": 5000},
    ])
    r = run_precheck(_SPEC_COMP, df)
    assert r.outcome == SKIP
    assert r.fact_block == ""


def test_default_mode_is_coexist_byte_identical():
    """未声明 mode 的 spec 与显式 coexist 结果逐字一致 (companion 分支不影响 M1)."""
    default_spec = PrecheckSpec(a_items=["PET-CT"], b_items=["图文报告"])
    coexist_spec = PrecheckSpec(a_items=["PET-CT"], b_items=["图文报告"], mode="coexist")
    df = _fee_df([
        {"medins_list_name": "PET-CT全身断层显像", "cnt": 1, "det_item_fee_sumamt": 3000},
        {"medins_list_name": "PET-CT图文报告费", "cnt": 1, "det_item_fee_sumamt": 50},
    ])
    d, c = run_precheck(default_spec, df), run_precheck(coexist_spec, df)
    assert d.outcome == c.outcome == FACTS
    assert d.fact_block == c.fact_block
    assert [(e.source, e.locator) for e in d.evidence] == [(e.source, e.locator) for e in c.evidence]


# ========== 迁移解析 (Task 4.2) ==========
_R191_ADDON = (
    "本规则关注: ...\n"
    "审计步骤:\n"
    '   A 类 (扫描): "PET-CT 全身显像" / "全身断层" / "肿瘤全身断层显像"\n'
    '   B 类 (报告): "图文报告" / "人工报告"\n'
    "注意: 患者基础诊断必须含肿瘤.\n"
)


def test_extract_regular_m1():
    a, b, reason = migrate.extract_precheck(_R191_ADDON)
    assert reason == ""
    assert "全身断层" in a and "图文报告" in b


def test_extract_bespoke_skipped():
    """R112 式带 STEP/search_examinations/触发器 → 跳过."""
    bespoke = (
        "⚡ v1.7 STEP 0a: search_examinations 对比 checkDate 不同日期 → CLEAN\n"
        '   A 类 (增强): "CT 增强扫描"\n   B 类 (平扫): "CT 平扫"\n'
        "📌 专家共识触发器 ...\n"
    )
    a, b, reason = migrate.extract_precheck(bespoke)
    assert reason  # 非空 = 跳过
    assert "bespoke" in reason


# ========== round-trip (Task 1.3) ==========
def test_rule_precheck_round_trip():
    rule = Rule(
        rule_id="R191", domain="肿瘤", violation_type="重复收费",
        question="q", derived_from_template="M1",
        precheck=PrecheckSpec(a_items=["全身断层"], b_items=["图文报告"]),
    )
    s = dump_rule_to_string(rule)
    assert "precheck" in s and "全身断层" in s
    back = Rule(**pyyaml.safe_load(s))
    assert back.precheck is not None
    assert back.precheck.a_items == ["全身断层"]
    assert back.precheck.b_items == ["图文报告"]


def test_rule_without_precheck_loads():
    rule = Rule(rule_id="R001", domain="d", violation_type="v", question="q")
    back = Rule(**pyyaml.safe_load(dump_rule_to_string(rule)))
    assert back.precheck is None
