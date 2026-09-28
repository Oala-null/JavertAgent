# -*- coding: utf-8 -*-
"""search_fees 退费净额行为 (fix-fee-refund-netting): 全退项剔除 / 退费行不列 / 合计净."""

from __future__ import annotations

import pandas as pd

from javert.data.loader import DataLoader
from javert.tools.search_fees import create_executor


class _StubLoader(DataLoader):
    def __init__(self, fees: pd.DataFrame):
        self._fees = fees

    def get_notes(self, patient_id):  # pragma: no cover - 未用
        return pd.DataFrame()

    def get_fees(self, patient_id):
        return self._fees[self._fees["bah"].astype(str).str.contains(patient_id, na=False)]

    def all_notes(self):  # pragma: no cover - 未用
        return pd.DataFrame()

    def all_fees(self):
        return self._fees


def _loader() -> _StubLoader:
    fees = pd.DataFrame({
        "bah": ["H-J13365 "] * 6,
        "fee_ocur_time": ["1/8/2024 00:00:00", "1/8/2024 00:00:00",
                          "2/8/2024 00:00:00", "3/8/2024 00:00:00",
                          "4/8/2024 00:00:00", "5/8/2024 00:00:00"],
        "cnt": [1.0, -1.0, 2.0, -1.0, 1.0, 1.0],
        "pric": [10.0, 10.0, 20.0, 20.0, 30.0, 50.0],
        "det_item_fee_sumamt": [10.0, -10.0, 40.0, -20.0, 30.0, 50.0],
        "med_list_codg": ["DZX", "DZX", "PART", "PART", "KEEP", "KEEP2"],
        "medins_list_name": ["地佐辛注射液", "地佐辛注射液", "某项目", "某项目",
                             "某药A", "某药B"],
    })
    return _StubLoader(fees)


def test_full_refund_item_excluded_from_keyword():
    ex = create_executor(_loader())
    out = ex("J13365", keyword="地佐辛")
    assert "未找到" in out  # 净 0 → 整组剔除


def test_partial_refund_keeps_net_total_and_hides_refund_row():
    ex = create_executor(_loader())
    out = ex("J13365", keyword="某项目")
    # 净额: +40 -20 = 20; 按项目名聚合为 1 项 (recover-deterministic-recall 1.1)
    assert "共1项" in out
    assert "合计: ¥20.00" in out
    # 退费行 (¥-20) 不出现
    assert "-20" not in out and "-20.00" not in out
    # 部分退费 → 行尾注记 (spec: 「含 N 次退费已抵消」)
    assert "含1次退费已抵消" in out


def test_catalog_excludes_full_refund_item():
    ex = create_executor(_loader())
    out = ex("J13365")  # 目录模式
    assert "地佐辛" not in out  # 全退药不进任何类别 Top3


def _loader_two_charge_one_refund() -> _StubLoader:
    """211440399 式: 某手术项目收费 2 行 + 退费 1 行 (净 1 次), 不应呈现"出现 2 次"."""
    fees = pd.DataFrame({
        "bah": ["H-P1 "] * 3,
        "fee_ocur_time": ["1/8/2024 00:00:00"] * 3,
        "cnt": [1.0, 1.0, -1.0],
        "pric": [500.0, 500.0, 500.0],
        "det_item_fee_sumamt": [500.0, 500.0, -500.0],
        "med_list_codg": ["OP1", "OP1", "OP1"],
        "medins_list_name": ["肘关节截骨术", "肘关节截骨术", "肘关节截骨术"],
    })
    return _StubLoader(fees)


def test_two_charge_one_refund_nets_to_single_item():
    ex = create_executor(_loader_two_charge_one_refund())
    out = ex("P1", keyword="肘关节")
    # 聚合为 1 项, 净额 500 (1000 - 500), 净数量 1, 不再"共2条/×2"
    assert "共1项" in out
    assert "合计: ¥500.00" in out
    assert "含1次退费已抵消" in out
    assert "×2" not in out  # 净 1 次, 绝不呈现两次


def _loader_fractional_qty() -> _StubLoader:
    """同切口次要手术 75% 计价: cnt=0.75 必须原样可见, 不取整为 0/1 (task 1.2)."""
    fees = pd.DataFrame({
        "bah": ["H-P2 "],
        "fee_ocur_time": ["1/8/2024 00:00:00"],
        "cnt": [0.75],
        "pric": [800.0],
        "det_item_fee_sumamt": [600.0],
        "med_list_codg": ["OP2"],
        "medins_list_name": ["肘关节截骨术"],
    })
    return _StubLoader(fees)


def test_fractional_quantity_preserved():
    ex = create_executor(_loader_fractional_qty())
    out = ex("P2", keyword="肘关节")
    assert "×0.75" in out  # 小数保真, 不取整为 ×0 / ×1
