# -*- coding: utf-8 -*-
"""tests for web/patient_overview.py 卡片摘要 (enhance-workbench-usability G1).

get_fees_sum_map (进程缓存 + 已知 patient 命中) + get_primary_dx (maindiag_flag=1
优先 / note 兜底 / 再无返回空).
"""

from __future__ import annotations

import pandas as pd
import pytest

from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.web import patient_overview as po
from javert.web.patient_overview import build_overview


@pytest.fixture(autouse=True)
def _fresh_caches():
    po.reset_caches()
    yield
    po.reset_caches()


def test_get_fees_sum_map_known_patient():
    """J66252 费用合计 ≈ 已知值 (来自 shi_fee groupby), map 非空."""
    m = po.get_fees_sum_map()
    assert len(m) > 100  # 全院 3000+ patients
    assert "J66252" in m
    assert m["J66252"] == pytest.approx(26871.39, abs=1.0)


def test_get_fees_sum_map_cached_once():
    """二次调用返回同一缓存对象 (不重复 groupby 全表)."""
    first = po.get_fees_sum_map()
    second = po.get_fees_sum_map()
    assert first is second


def test_get_primary_dx_maindiag_flag():
    """病案首页 maindiag_flag=1 主诊 (J66252 = 甲状腺恶性肿瘤 C73.x00)."""
    dx = po.get_primary_dx("J66252")
    assert "甲状腺" in dx
    assert "C73" in dx


def test_get_primary_dx_missing_returns_empty():
    """无病案首页 + 无 loader → 返回空字符串 (不报错)."""
    assert po.get_primary_dx("__NO_SUCH_PATIENT__") == ""


def test_get_primary_dx_note_fallback(tmp_path):
    """无病案首页但有文书出院诊断 → note 派生兜底."""
    notes = pd.DataFrame(
        {
            "住院号": ["TESTPT001"],
            "阶段": ["住院"],
            "子阶段": ["出院诊断"],
            "内容": ["甲状腺乳头状癌"],
        }
    )
    fees = pd.DataFrame({"bah": ["HX-TESTPT001"], "det_item_fee_sumamt": ["1.0"]})
    notes_path = tmp_path / "notes.csv"
    fees_path = tmp_path / "fees.csv"
    notes.to_csv(notes_path, index=False)
    fees.to_csv(fees_path, index=False)
    loader = CsvLoader(notes_path, fees_path)
    # TESTPT001 不在真实 shi_zd → 走 note 兜底
    dx = po.get_primary_dx("TESTPT001", loader)
    assert dx == "甲状腺乳头状癌"


# =========================================================
# G3: 费用类别就地展开明细
# =========================================================
def test_build_overview_fee_category_items_consistency():
    """每类别 items 非空; 未截断类别的 items 金额之和 == 该类别汇总金额."""
    cfg = get_config()
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    ov = build_overview("J66252", loader)
    cats = ov["fee_categories"]
    assert cats
    checked = 0
    for c in cats:
        assert c["items"], f"类别 {c['label']} 明细为空"
        assert c["items_total"] >= len(c["items"])
        if not c["items_truncated"]:
            items_sum = sum(it["sum"] for it in c["items"])
            assert items_sum == pytest.approx(c["sum"], abs=0.01), c["label"]
            checked += 1
    assert checked >= 1


# =========================================================
# boost-llm-efficiency: build_overview 真缓存
# =========================================================
def test_build_overview_cached_but_isolated_copies():
    """同 (patient_id, loader) 二次调用: 内层缓存不重算, 但外层返回独立深拷贝.

    fix-scan-residuals: 内层 _build_overview_cached 命中同一对象 (不重算), 公共
    build_overview 返回 deepcopy → 两次结果值相等但非同一对象, 改一个不污染另一个.
    """
    cfg = get_config()
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    first = build_overview("J66252", loader)
    second = build_overview("J66252", loader)
    assert first is not second                       # 独立对象
    assert first["patient_id"] == second["patient_id"]
    # 内层缓存命中 (不重算): 底层缓存 dict 是同一个
    assert (po._build_overview_cached("J66252", loader)
            is po._build_overview_cached("J66252", loader))
    # 改 first 不污染 second (跨请求串数据回归面)
    first["fee_categories"].append({"label": "污染"})
    assert not any(c.get("label") == "污染" for c in second["fee_categories"])


def test_build_overview_cache_cleared_by_reset():
    """reset_caches() 后重算 (新对象) — onboarding 载入新数据的失效钩子."""
    cfg = get_config()
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    first = build_overview("J66252", loader)
    po.reset_caches()
    second = build_overview("J66252", loader)
    assert first is not second
    assert first["patient_id"] == second["patient_id"] == "J66252"
