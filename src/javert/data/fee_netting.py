# -*- coding: utf-8 -*-
"""fee_netting — 退费净额聚合 (单点真值, data-access 层).

shi_fee 退费表现为 **负 `cnt` + 负 `det_item_fee_sumamt`**, 与正行成对:
  - 地佐辛(易可定)注射液 12 行 / 5 个日期 → sum(cnt)=0 (完全充退, 从没真用)
  - 住院诊疗费 40 行 → 净 33 / 33 个不同日期 (有退费, 行数虚高 7)

本模块按项目 (优先 `med_list_codg`, 无码退项目名) `sum(cnt)`, 暴露 per-item
``NetItem``, 净额 ≤ 0 的项 (`is_full_refund`) 供消费方从「用过 / 计数 / 明细」剔除。

`distinct_billing_dates` = 净正收费 (cnt > 0) 覆盖的不同 `fee_ocur_time` 日期数,
供 Change C「确保是不同的两次收费」次数门控读取 (设计 D2: 必须先净再数日期)。

**纯函数, 不改原始行**: 原始全量 fee (`DataLoader.all_fees`) 保留不动 (审计留痕),
净额是独立显式调用 (设计 D3)。

Source: 本项目原创 (fix-fee-refund-netting).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# shi_fee 标准列名 (全库统一; 外部数据经 ETL 也归一到这些列)
NAME_COL = "medins_list_name"
CODE_COL = "med_list_codg"
CNT_COL = "cnt"
DATE_COL = "fee_ocur_time"


@dataclass
class NetItem:
    """单个费用项目的退费净额聚合结果."""

    name: str
    code: str
    net_qty: float
    distinct_billing_dates: int
    has_refund: bool
    refund_count: int = field(default=0)  # 负 cnt 行数 (4.3 脚注「含 N 次退费」用)

    @property
    def is_full_refund(self) -> bool:
        """净额 ≤ 0 → 完全充退, 从「用过 / 计数 / 明细」剔除."""
        return self.net_qty <= 0


def fee_group_key(code: str | None, name: str | None) -> str:
    """分组键: 优先 `med_list_codg` (稳定, 同药不同名也聚到一起), 无码退项目名 (设计 D1)."""
    c = (code or "").strip()
    if c and c.lower() != "nan":
        return c
    return (name or "").strip()


def _safe_float(v) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _date_part(v) -> str:
    """fee_ocur_time → 日期段 ('4/8/2024 00:00:00' → '4/8/2024')."""
    if v is None:
        return ""
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat"):
        return ""
    return s.split()[0]


def net_fee_items(fee_df: pd.DataFrame | None) -> dict[str, NetItem]:
    """按 `med_list_codg` (无码退项目名) 分组 `sum(cnt)` → {group_key: NetItem}.

    缺关键列 (无 name 或 cnt) 时返回空 dict (调用方退化为不净额, 行为不变)。
    """
    out: dict[str, NetItem] = {}
    if fee_df is None or len(fee_df) == 0:
        return out
    cols = fee_df.columns
    if NAME_COL not in cols or CNT_COL not in cols:
        return out
    has_code = CODE_COL in cols
    has_date = DATE_COL in cols

    groups: dict[str, dict] = {}
    for _, r in fee_df.iterrows():
        name = str(r.get(NAME_COL) or "").strip()
        code = str(r.get(CODE_COL) or "").strip() if has_code else ""
        if code.lower() == "nan":
            code = ""
        key = fee_group_key(code, name)
        if not key:
            continue
        cnt = _safe_float(r.get(CNT_COL))
        g = groups.get(key)
        if g is None:
            g = {"name": name, "code": code, "net": 0.0, "dates": set(), "refunds": 0}
            groups[key] = g
        elif cnt > 0 and not g["name"]:
            g["name"] = name  # 用净正收费行的名字补全
        g["net"] += cnt
        if cnt < 0:
            g["refunds"] += 1
        elif cnt > 0 and has_date:
            ds = _date_part(r.get(DATE_COL))
            if ds:
                g["dates"].add(ds)

    for key, g in groups.items():
        out[key] = NetItem(
            name=g["name"],
            code=g["code"],
            net_qty=g["net"],
            distinct_billing_dates=len(g["dates"]),
            has_refund=g["refunds"] > 0,
            refund_count=g["refunds"],
        )
    return out


def fully_refunded_keys(fee_df: pd.DataFrame | None) -> set[str]:
    """净额 ≤ 0 的分组键集合 — 消费方据此整组剔除 (完全充退项)."""
    return {k for k, item in net_fee_items(fee_df).items() if item.is_full_refund}


def max_same_day_distinct_items(
    fee_df: pd.DataFrame | None, keywords: list[str]
) -> int | None:
    """套餐口径计数 (recover-deterministic-recall 2.1): 关键词族命中的净正收费项目,
    按 `fee_ocur_time` 日期分组, 返回**单日不同项目名数的最大值**.

    与 ②单次闸的"净不同收费次数"是不同口径: 11 项细胞因子同日打包 = 11 项 (≠ 1 次),
    据此判「多项目单日打包」的套餐形态。

    完全充退组整组剔除; 退费行 (cnt<0) 不计; 无匹配 / 无费用数据 → None (fail-open)。
    缺日期列时全部归一到同一天 (退化为全程不同项目名数)。
    """
    kws = [k for k in keywords if k]
    if fee_df is None or len(fee_df) == 0 or NAME_COL not in fee_df.columns or not kws:
        return None
    has_code = CODE_COL in fee_df.columns
    has_cnt = CNT_COL in fee_df.columns
    has_date = DATE_COL in fee_df.columns
    refunded = fully_refunded_keys(fee_df)

    per_day: dict[str, set[str]] = {}
    for _, r in fee_df.iterrows():
        name = str(r.get(NAME_COL) or "").strip()
        if not name or name.lower() == "nan":
            continue
        if not any(kw in name for kw in kws):
            continue
        code = str(r.get(CODE_COL) or "").strip() if has_code else ""
        if code.lower() == "nan":
            code = ""
        if fee_group_key(code, name) in refunded:
            continue
        if has_cnt and _safe_float(r.get(CNT_COL)) <= 0:
            continue  # 退费行 / 零量行不计
        day = _date_part(r.get(DATE_COL)) if has_date else ""
        per_day.setdefault(day, set()).add(name)

    if not per_day:
        return None
    return max(len(names) for names in per_day.values())
