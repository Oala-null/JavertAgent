# -*- coding: utf-8 -*-
"""search_fees — 费用检索 (分类聚合 / 类别详情 / 关键词搜索).

Source: zadig_agent/src/skills/search_fees.py (snapshot @ 2026-05-08)
改动: fee_df 来源改为 DataLoader.all_fees().
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from javert.data.fee_netting import fee_group_key, net_fee_items
from javert.data.loader import DataLoader

REQUIRES_PATIENT_ID = True

DESCRIPTION = (
    "检索患者全量费用. 无参数返回按类别聚合的摘要 (手术/药品/耗材/检查/其他, 各 Top3); "
    "传 category 返回该类全部明细; 传 keyword 搜索项目名."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号"},
        "category": {"type": "string", "description": "费用类别: 手术类/药品类/耗材类/检查类/其他类"},
        "keyword": {"type": "string", "description": "费用项目名搜索关键词"},
    },
    "required": ["patient_id"],
}

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "手术类": [
        "手术", "切除", "切开", "缝合", "清扫", "消融", "活检",
        "造瘘", "引流", "吻合", "修补", "移植", "置换", "固定",
        "截骨", "植骨", "探查", "减压", "成形", "造影", "穿刺",
        "介入", "栓塞", "扩张", "取石",
    ],
    "药品类": [
        "注射", "片", "胶囊", "口服", "药", "素", "霉素", "唑",
        "西林", "沙坦", "普利", "洛尔", "他汀", "芬", "因子",
        "白蛋白", "丙球", "免疫球蛋白", "PD-1", "替尼",
    ],
    "耗材类": [
        "导管", "缝线", "钉", "支架", "敷料", "引流管", "管",
        "导丝", "球囊", "膜", "海绵", "骨水泥", "螺钉", "钢板",
        "假体", "网片",
    ],
    "检查类": [
        "CT", "MRI", "B超", "超声", "X线", "DR", "造影",
        "心电", "脑电", "肌电", "检验", "检查", "化验",
        "病理", "培养", "涂片", "血常规", "生化",
    ],
}

_VALID_CATEGORIES = ["手术类", "药品类", "耗材类", "检查类", "其他类"]

# 官方类别标签 (medins_chrgitm_type) → 自信桶. data-hub 已把 MXFYLB 2 位国标码回填成同款
# 中文, 故跨院可移植. 只在标签明确落桶时覆盖; 模糊标签 (治疗/床位/护理/其他/麻醉…) 与缺列
# 回退名称启发式 = 最小漂移. 数字码 med_chrgitm_type 本院脏码, 不参与.
_LABEL_CATEGORY: list[tuple[tuple[str, ...], str]] = [
    (("手术",), "手术类"),
    (("药",), "药品类"),           # 西药/中药/中成药/药品 皆含"药"
    (("材料", "耗材"), "耗材类"),
    (("检查", "化验", "检验", "CT", "MRI", "拍片", "病理", "影像", "超声", "B超"), "检查类"),
]


def _clean_label(value: object) -> str:
    """Normalize SQL/pandas values before substring matching (NaN is a float)."""
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _classify_by_label(label: object) -> str | None:
    label_text = _clean_label(label)
    for needles, cat in _LABEL_CATEGORY:
        if any(n in label_text for n in needles):
            return cat
    return None


def _classify(name: object, chrgitm_label: object = "") -> str:
    by_label = _classify_by_label(chrgitm_label)
    if by_label:
        return by_label
    name_text = _clean_label(name)
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(kw in name_text for kw in kws):
            return cat
    return "其他类"


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(f"找不到列, 候选: {candidates}, 实际: {df.columns.tolist()}")


def create_executor(loader: DataLoader) -> Callable[..., str]:
    """绑定 DataLoader, 返回 search_fees(patient_id, category?, keyword?) 函数."""

    def execute(patient_id: str, category: str | None = None, keyword: str | None = None, **_kwargs) -> str:
        fee_df = loader.all_fees()
        if "bah" in fee_df.columns:
            mask = fee_df["bah"].astype(str).str.contains(patient_id, na=False)
            patient_fees = fee_df[mask]
        else:
            patient_fees = fee_df[fee_df.iloc[:, 0].astype(str).str.contains(patient_id, na=False)]

        if patient_fees.empty:
            return "该患者无费用记录"

        name_col = _find_col(patient_fees, ["medins_list_name", "prodname", "item_name", "项目名称", "name"])
        amount_col = _find_col(patient_fees, ["det_item_fee_sumamt", "unit_price", "pric", "金额"])

        fees = patient_fees.copy()
        fees["_amount"] = pd.to_numeric(fees[amount_col], errors="coerce").fillna(0)
        fees["_name"] = fees[name_col].astype(str)
        # make-rules-code-portable: 官方类别标签优先分类 (缺列→空串→纯名称兜底)
        fees["_chrgitm_label"] = (
            fees["medins_chrgitm_type"].astype(str) if "medins_chrgitm_type" in fees.columns else ""
        )

        # v0.9 — 加日期返回, R112 等需要对比平扫/增强是否不同日
        date_col = _find_col(patient_fees, ["fee_ocur_time", "事件时间", "fee_date"])
        if date_col:
            fees["_date"] = fees[date_col].astype(str).str[:10]
        else:
            fees["_date"] = ""

        # fix-fee-refund-netting: 退费净额 (设计 D1/D3). 完全充退项 (净≤0) 整组剔除明细/计数,
        # 退费行 (cnt<0) 不进明细; 合计仍走全量 sum 自动净 (kept 集去掉净0组后总额不变).
        cnt_col = next((c for c in ("cnt", "数量") if c in patient_fees.columns), None)
        if cnt_col:
            fees["_cnt"] = pd.to_numeric(fees[cnt_col], errors="coerce").fillna(0)
        else:
            fees["_cnt"] = 1.0  # 无 cnt 列 → 每行视为 1 次正收费 (不净额)
        # boost-llm-efficiency: 量价 + 开单科室/医师 信号 (M4 超标准/分解/串换科室类规则依赖).
        # 源数据缺列时整体省略 (不出现占位符); 行锚与既有列文本不变, 只追加.
        pric_col = next((c for c in ("pric", "unit_price", "单价") if c in patient_fees.columns), None)
        unit_col = next((c for c in ("unit", "计价单位", "单位") if c in patient_fees.columns), None)
        order_col = next((c for c in ("order_id", "YZID", "医嘱ID") if c in patient_fees.columns), None)
        dept_col = next(
            (c for c in ("acord_dept_name", "bilg_dept_name", "开单科室") if c in patient_fees.columns), None
        )
        dr_col = next(
            (c for c in ("orders_dr_name", "bilg_dr_name", "开单医师") if c in patient_fees.columns), None
        )
        if pric_col:
            fees["_pric"] = pd.to_numeric(fees[pric_col], errors="coerce")

        def _clean_str(v) -> str:
            s = str(v).strip()
            return "" if s.lower() in ("nan", "none") else s

        has_code = "med_list_codg" in fees.columns
        net_items = net_fee_items(patient_fees)
        full_refunded = {k for k, it in net_items.items() if it.is_full_refund}

        def _gkey(row) -> str:
            code = str(row.get("med_list_codg") or "").strip() if has_code else ""
            return fee_group_key(code, row["_name"])

        fees["_gkey"] = fees.apply(_gkey, axis=1)
        # kept: 去掉完全充退组 (净0, 总额不受影响); 下方按项目名净额聚合 (退费行自动相抵)
        fees = fees[~fees["_gkey"].isin(full_refunded)].copy()
        fees["_category"] = fees.apply(lambda r: _classify(r["_name"], r["_chrgitm_label"]), axis=1)

        def _fmt_qty(q: float) -> str:
            # 数量小数保真 (1.2): 0.75 计价行绝不取整为 0 或 1
            return f"{int(q)}" if float(q).is_integer() else f"{q:g}"

        def _agg(subset: pd.DataFrame) -> list[dict]:
            """按项目名 (_gkey) 聚合退费后净额 → 净额降序的条目列表 (设计 D3).

            金额走全量 (含退费行自动相抵); 数量/退费次数取 NetItem; 消除"同项目多行=多次"误读.
            """
            items: list[dict] = []
            for gkey, grp in subset.groupby("_gkey", sort=False):
                pos = grp[grp["_cnt"] > 0]
                if pos.empty:
                    continue  # 该组仅剩退费行 (完全充退已剔, 兜底)
                first = pos.iloc[0]
                netit = net_items.get(gkey)
                who = "/".join(
                    x for x in (
                        _clean_str(first.get(dept_col)) if dept_col else "",
                        _clean_str(first.get(dr_col)) if dr_col else "",
                    ) if x
                )
                items.append({
                    "name": str(first["_name"]),
                    "amount": float(grp["_amount"].sum()),  # 净金额
                    "cnt": float(netit.net_qty) if netit else float(pos["_cnt"].sum()),
                    "dates": sorted({d for d in pos["_date"].tolist() if d}),
                    "pric": float(first["_pric"]) if (pric_col and pd.notna(first["_pric"])) else None,
                    "unit": _clean_str(first.get(unit_col)) if unit_col else "",
                    "order_id": _clean_str(first.get(order_col)) if order_col else "",
                    "who": who,
                    "refunds": int(netit.refund_count) if netit else 0,
                    "category": str(first["_category"]),
                })
            items.sort(key=lambda d: -d["amount"])
            return items

        def _extra(it: dict) -> str:
            parts = []
            q = _fmt_qty(it["cnt"])
            if it["pric"] is not None and it["pric"] > 0:
                parts.append(f"单价{it['pric']:.2f}×{q}")
            elif it["cnt"] != 1:
                parts.append(f"×{q}")
            if it["unit"]:
                parts.append(f"单位={it['unit']}")
            if it["order_id"]:
                parts.append("医嘱关联=有")
            if it["who"]:
                parts.append(f"[开单:{it['who']}]")
            note = f" [含{it['refunds']}次退费已抵消]" if it["refunds"] > 0 else ""
            return ((" " + " ".join(parts)) if parts else "") + note

        if keyword:
            matched = fees[fees["_name"].str.contains(keyword, na=False)]
            if matched.empty:
                return f"未找到包含 '{keyword}' 的费用项目 (退费已抵消项不计)"
            items = _agg(matched)
            total = sum(it["amount"] for it in items)
            lines = [f"关键词'{keyword}'搜索结果 (共{len(items)}项, 已净退费):", ""]
            for i, it in enumerate(items, 1):
                ds = it["dates"]
                date_str = (
                    f"  [{ds[0]}]" if len(ds) == 1 else (f"  [{ds[0]}→{ds[-1]}]" if ds else "")
                )
                # v0.9 (前向 locator): fee 行定位标记 — 让新审计锚点能精确指回该费用项
                loc = f" ⟨行={i} 项目={it['name']}⟩"
                lines.append(f"  {it['name']}: ¥{it['amount']:.2f}{date_str}{_extra(it)}{loc}")
                if i >= 20:
                    lines.append(f"... 共{len(items)}项, 已显示前 20 项")
                    break
            lines.append("")
            lines.append(f"合计: ¥{total:.2f}")
            # 跨天统计 (v0.9) — 按聚合条目覆盖的净收费日期
            all_dates = sorted({d for it in items for d in it["dates"]})
            if len(all_dates) >= 2:
                lines.append(f"📅 跨 {len(all_dates)} 天 ({all_dates[0]} → {all_dates[-1]})")
            return "\n".join(lines)

        if category:
            if category not in _VALID_CATEGORIES:
                return f"未知类别 '{category}', 可选: {'、'.join(_VALID_CATEGORIES)}"
            cat_rows = fees[fees["_category"] == category]
            if cat_rows.empty:
                return f"该患者无 {category} 费用记录"
            items = _agg(cat_rows)
            lines = [f"{category}明细 (共{len(items)}项):", ""]
            for i, it in enumerate(items, 1):
                lines.append(f"  {i}. {it['name']}: ¥{it['amount']:.2f}{_extra(it)}")
            total = sum(it["amount"] for it in items)
            lines.append("")
            lines.append(f"{category}合计: ¥{total:.2f}")
            return "\n".join(lines)

        # 目录模式 — 计数/Top3/合计 均按项目名净额聚合 (2.3)
        all_items = _agg(fees)
        total_amount = sum(it["amount"] for it in all_items)
        lines = [f"费用分类目录 (共{len(all_items)}项, 总额¥{total_amount:.2f}):", ""]
        for cat in _VALID_CATEGORIES:
            cat_items = [it for it in all_items if it["category"] == cat]
            if not cat_items:
                continue
            cat_total = sum(it["amount"] for it in cat_items)
            pct = (cat_total / total_amount * 100) if total_amount > 0 else 0
            lines.append(f"【{cat}】{len(cat_items)}项, 合计¥{cat_total:.2f} ({pct:.1f}%)")
            for i, it in enumerate(cat_items[:3], 1):
                lines.append(f"    {i}. {it['name']}: ¥{it['amount']:.2f}")
            if len(cat_items) > 3:
                lines.append(f"    ... 还有 {len(cat_items) - 3} 项")
            lines.append("")

        lines.append(f"用 category 取该类详情, 例: search_fees(patient_id=\"{patient_id}\", category=\"手术类\")")
        return "\n".join(lines)

    return execute
