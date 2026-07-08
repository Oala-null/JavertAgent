# -*- coding: utf-8 -*-
"""make-rules-code-portable: search_fees 分类切官方类别标签 单测.

- 标签自信桶优先 (西药→药品类, 造影按"检查"标签→检查类)
- 模糊标签 (治疗) 回退名称启发式
- 缺 medins_chrgitm_type 列 → 输出与旧纯名称启发式逐字一致
"""

from __future__ import annotations

import pandas as pd

from javert.data.loader import DataLoader
from javert.tools import search_fees
from javert.tools.search_fees import _classify


class _StubLoader(DataLoader):
    def __init__(self, fees: pd.DataFrame):
        self._fees = fees

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        return pd.DataFrame()

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return self._fees

    def all_notes(self) -> pd.DataFrame:
        return pd.DataFrame()

    def all_fees(self) -> pd.DataFrame:
        return self._fees


# ── 纯函数 _classify ──

def test_label_western_drug_to_drug_category():
    # 名称无药品关键词 (会落"其他类"), 但标签"西药"→药品类
    assert _classify("氯化钠", "西药") == "药品类"
    assert _classify("氯化钠", "") == "其他类"  # 无标签回退名称: 未命中 → 其他类


def test_label_contrast_by_label_not_dict_order():
    # "造影" 名称同时在手术类/检查类关键词表, 纯名称走手术类(字典顺序); 标签"检查"→检查类
    assert _classify("脑血管造影", "") == "手术类"
    assert _classify("脑血管造影", "检查") == "检查类"
    assert _classify("脑血管造影", "拍片") == "检查类"


def test_ambiguous_label_falls_back_to_name():
    # "治疗" 非自信桶 → 回退名称: "射频消融治疗" 含"消融"→手术类
    assert _classify("射频消融治疗", "治疗") == "手术类"
    # 模糊标签 + 名称也不命中 → 其他类
    assert _classify("某项目", "治疗") == "其他类"


# ── 集成: 缺列零回归 ──

def _fees_with_label() -> pd.DataFrame:
    return pd.DataFrame({
        "bah": ["H-P1 "] * 2,
        "fee_ocur_time": ["2026-01-01", "2026-01-02"],
        "det_item_fee_sumamt": [10.0, 20.0],
        "medins_list_name": ["氯化钠", "脑血管造影"],
        "medins_chrgitm_type": ["西药", "检查"],
    })


def _fees_no_label() -> pd.DataFrame:
    return _fees_with_label().drop(columns=["medins_chrgitm_type"])


def test_integration_label_drives_category():
    execute = search_fees.create_executor(_StubLoader(_fees_with_label()))
    out = execute("P1")
    assert "【药品类】" in out       # 氯化钠 按标签进药品类
    assert "【检查类】" in out       # 造影 按标签进检查类
    assert "【手术类】" not in out    # 造影 不再因字典顺序落手术类


def test_integration_missing_label_matches_name_heuristic():
    """缺 medins_chrgitm_type 列 → 造影按名称落手术类 (与本 change 前一致)."""
    execute = search_fees.create_executor(_StubLoader(_fees_no_label()))
    out = execute("P1")
    assert "【手术类】" in out       # 造影 名称启发式 → 手术类 (旧行为)
    assert "【其他类】" in out       # 氯化钠 名称未命中 → 其他类 (旧行为)
