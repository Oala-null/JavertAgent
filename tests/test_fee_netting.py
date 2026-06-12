# -*- coding: utf-8 -*-
"""fee_netting 退费净额聚合单测 (设计 D1/D2: 完全充退剔除 / 部分退保留 / 净后数日期)."""

from __future__ import annotations

import pandas as pd

from javert.data.fee_netting import (
    fee_group_key,
    fully_refunded_keys,
    net_fee_items,
)


def _fees(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_dezuoxin_full_refund_net_zero():
    """地佐辛 +1/-1 成对 → 净 0 → is_full_refund."""
    df = _fees([
        {"medins_list_name": "地佐辛(易可定)注射液", "med_list_codg": "XN02A", "cnt": 1.0, "fee_ocur_time": "1/8/2024 00:00:00"},
        {"medins_list_name": "地佐辛(易可定)注射液", "med_list_codg": "XN02A", "cnt": -1.0, "fee_ocur_time": "1/8/2024 00:00:00"},
        {"medins_list_name": "地佐辛(易可定)注射液", "med_list_codg": "XN02A", "cnt": 1.0, "fee_ocur_time": "2/8/2024 00:00:00"},
        {"medins_list_name": "地佐辛(易可定)注射液", "med_list_codg": "XN02A", "cnt": -1.0, "fee_ocur_time": "3/8/2024 00:00:00"},
    ])
    items = net_fee_items(df)
    it = items["XN02A"]
    assert it.net_qty == 0.0
    assert it.is_full_refund is True
    assert it.has_refund is True
    assert it.refund_count == 2
    assert "XN02A" in fully_refunded_keys(df)


def test_inpatient_fee_net_33_dates_33():
    """住院诊疗费: 净 33 / 净正收费覆盖 33 个不同日期 (含退费, 行数虚高)."""
    rows = []
    # 33 个不同日期各 +1
    for d in range(1, 34):
        rows.append({"medins_list_name": "住院诊疗费", "med_list_codg": "S1102", "cnt": 1.0, "fee_ocur_time": f"{d}/8/2024 00:00:00"})
    # 4 次额外正收费 (同已有日期, 不增日期数) — 模拟净正行 37 行但日期仍 33
    for d in (1, 2, 3, 4):
        rows.append({"medins_list_name": "住院诊疗费", "med_list_codg": "S1102", "cnt": 1.0, "fee_ocur_time": f"{d}/8/2024 00:00:00"})
    # 4 次退费冲掉上面 4 行 → 净 33
    for d in (1, 2, 3, 4):
        rows.append({"medins_list_name": "住院诊疗费", "med_list_codg": "S1102", "cnt": -1.0, "fee_ocur_time": f"{d}/8/2024 00:00:00"})
    it = net_fee_items(_fees(rows))["S1102"]
    assert it.net_qty == 33.0
    assert it.distinct_billing_dates == 33
    assert it.is_full_refund is False
    assert "S1102" not in fully_refunded_keys(_fees(rows))


def test_partial_refund_kept():
    """+2 / -1 → 净 1 → 保留, 不整条剔除."""
    df = _fees([
        {"medins_list_name": "某项目", "med_list_codg": "C001", "cnt": 2.0, "fee_ocur_time": "5/8/2024 00:00:00"},
        {"medins_list_name": "某项目", "med_list_codg": "C001", "cnt": -1.0, "fee_ocur_time": "6/8/2024 00:00:00"},
    ])
    it = net_fee_items(df)["C001"]
    assert it.net_qty == 1.0
    assert it.is_full_refund is False
    assert it.has_refund is True
    assert "C001" not in fully_refunded_keys(df)


def test_group_key_falls_back_to_name_when_no_code():
    """无码 → 按项目名分组."""
    assert fee_group_key("", "无码退项目") == "无码退项目"
    assert fee_group_key("nan", "X") == "X"
    assert fee_group_key("S1102", "住院诊疗费") == "S1102"
    df = _fees([
        {"medins_list_name": "无码退项目", "med_list_codg": "", "cnt": 1.0, "fee_ocur_time": "1/8/2024"},
        {"medins_list_name": "无码退项目", "med_list_codg": "", "cnt": -1.0, "fee_ocur_time": "1/8/2024"},
    ])
    items = net_fee_items(df)
    assert "无码退项目" in items
    assert items["无码退项目"].is_full_refund is True


def test_empty_or_missing_columns_returns_empty():
    assert net_fee_items(None) == {}
    assert net_fee_items(pd.DataFrame()) == {}
    # 缺 cnt 列 → 退化不净额
    assert net_fee_items(_fees([{"medins_list_name": "X"}])) == {}
