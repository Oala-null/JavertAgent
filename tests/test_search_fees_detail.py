# -*- coding: utf-8 -*-
"""boost-llm-efficiency: search_fees 行明细字段 (量价 + 开单科室/医师) 单测.

- 有列 → 行输出含 `单价X×N` 与 `[开单:科室/医师]`
- 缺列 → 整体省略 (无占位符、无报错), 输出与加列前一致
- 行锚 `⟨行=i 项目=...⟩` 与既有列文本不变 (只追加)
"""

from __future__ import annotations

import pandas as pd

from javert.data.loader import DataLoader
from javert.tools import search_fees


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


def _fees_full() -> pd.DataFrame:
    """全列: 单价/数量/开单科室/开单医师齐备."""
    return pd.DataFrame({
        "bah": ["H31010600042-J66252 "] * 2,
        "fee_ocur_time": ["2026-01-01", "2026-01-02"],
        "cnt": [3.0, 1.0],
        "pric": [86.0, 200.0],
        "det_item_fee_sumamt": [258.0, 200.0],
        "medins_list_name": ["麻醉监护", "CT平扫"],
        "acord_dept_name": ["骨科", "放射科"],
        "orders_dr_name": ["张三", "李四"],
    })


def _fees_minimal() -> pd.DataFrame:
    """外部院最小列: 无单价/科室/医师 (数量列也无)."""
    return pd.DataFrame({
        "bah": ["H31010600042-J66252 "] * 2,
        "fee_ocur_time": ["2026-01-01", "2026-01-02"],
        "det_item_fee_sumamt": [258.0, 200.0],
        "medins_list_name": ["麻醉监护", "CT平扫"],
    })


def _run(fees: pd.DataFrame, **kwargs) -> str:
    execute = search_fees.create_executor(_StubLoader(fees))
    return execute("J66252", **kwargs)


def test_keyword_row_shows_price_qty_dept_doctor():
    out = _run(_fees_full(), keyword="麻醉")
    assert "86.00×3" in out            # 量价形态 (spec scenario)
    assert "[开单:骨科/张三]" in out    # 科室/医师
    assert "¥258.00" in out            # 合计金额列不变


def test_keyword_row_anchor_and_existing_text_unchanged():
    out = _run(_fees_full(), keyword="麻醉")
    assert "⟨行=1 项目=麻醉监护⟩" in out          # 行锚逐字不变
    assert "麻醉监护: ¥258.00" in out             # 既有列文本不变 (只追加)
    assert "[2026-01-01]" in out


def test_category_row_shows_extra_fields():
    out = _run(_fees_full(), category="检查类")
    assert "200.00×1" in out
    assert "[开单:放射科/李四]" in out


def test_missing_columns_omitted_gracefully():
    """缺列 → 无新增字段、无占位符、无报错; 行文本与加列前一致."""
    out = _run(_fees_minimal(), keyword="麻醉")
    assert "单价" not in out
    assert "开单" not in out
    assert "×" not in out
    assert "麻醉监护: ¥258.00  [2026-01-01] ⟨行=1 项目=麻醉监护⟩" in out


def test_partial_columns_price_only():
    """只有单价+数量、无科室/医师 → 量价出现, 开单段省略."""
    fees = _fees_minimal().assign(cnt=[3.0, 1.0], pric=[86.0, 200.0])
    out = _run(fees, keyword="麻醉")
    assert "86.00×3" in out
    assert "开单" not in out


def test_optional_unit_and_order_id_are_visible():
    fees = _fees_full().assign(unit=["次", "部位"], order_id=["O-1", "O-2"])
    out = _run(fees, keyword="麻醉")
    assert "单位=次" in out
    assert "医嘱关联=有" in out
    assert "O-1" not in out
