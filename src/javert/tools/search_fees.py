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


def _classify_by_label(label: str) -> str | None:
    for needles, cat in _LABEL_CATEGORY:
        if any(n in label for n in needles):
            return cat
    return None


def _classify(name: str, chrgitm_label: str = "") -> str:
    by_label = _classify_by_label(chrgitm_label)
    if by_label:
        return by_label
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(kw in name for kw in kws):
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

        def _row_extra(row) -> str:
            parts = []
            if pric_col and cnt_col and pd.notna(row["_pric"]) and row["_pric"] > 0:
                cnt_v = row["_cnt"]
                cnt_str = f"{int(cnt_v)}" if float(cnt_v).is_integer() else f"{cnt_v:g}"
                parts.append(f"单价{row['_pric']:.2f}×{cnt_str}")
            who = "/".join(
                x for x in (
                    _clean_str(row.get(dept_col)) if dept_col else "",
                    _clean_str(row.get(dr_col)) if dr_col else "",
                ) if x
            )
            if who:
                parts.append(f"[开单:{who}]")
            return (" " + " ".join(parts)) if parts else ""

        has_code = "med_list_codg" in fees.columns
        net_items = net_fee_items(patient_fees)
        full_refunded = {k for k, it in net_items.items() if it.is_full_refund}

        def _gkey(row) -> str:
            code = str(row.get("med_list_codg") or "").strip() if has_code else ""
            return fee_group_key(code, row["_name"])

        fees["_gkey"] = fees.apply(_gkey, axis=1)
        # kept: 去掉完全充退组 (净0, 总额不受影响); 明细/计数再过滤掉退费行 (_cnt<=0)
        fees = fees[~fees["_gkey"].isin(full_refunded)].copy()

        if keyword:
            mask = fees["_name"].str.contains(keyword, na=False)
            matched = fees[mask]
            if matched.empty:
                return f"未找到包含 '{keyword}' 的费用项目 (退费已抵消项不计)"
            # 合计走全量净 (含退费行自动相抵); 明细/计数只列净正收费行 (_cnt>0)
            total = matched["_amount"].sum()
            display = matched[matched["_cnt"] > 0].sort_values(
                ["_date", "_amount"], ascending=[True, False]
            )
            lines = [f"关键词'{keyword}'搜索结果 (共{len(display)}条):", ""]
            # 日期 → 列表 显示每天的明细 (帮 R112 对比日期)
            for i, (_, row) in enumerate(display.iterrows(), 1):
                date_str = f"  [{row['_date']}]" if row['_date'] else ""
                # v0.9 (前向 locator): fee 行定位标记 — 让新审计锚点能精确指回该费用行
                loc = f" ⟨行={i} 项目={row['_name']}⟩"
                lines.append(f"  {row['_name']}: ¥{row['_amount']:.2f}{date_str}{_row_extra(row)}{loc}")
                if i >= 20:
                    lines.append(f"... 共{len(display)}条, 已显示前 20 条")
                    break
            lines.append("")
            lines.append(f"合计: ¥{total:.2f}")
            # 跨天统计 (v0.9) — 日期按净正收费行 (2.4)
            if date_col:
                distinct_dates = display[display['_date'] != '']['_date'].unique()
                if len(distinct_dates) >= 2:
                    lines.append(
                        f"📅 跨 {len(distinct_dates)} 天 ({sorted(distinct_dates)[0]} → {sorted(distinct_dates)[-1]})"
                    )
            return "\n".join(lines)

        fees["_category"] = fees.apply(lambda r: _classify(r["_name"], r["_chrgitm_label"]), axis=1)
        # 明细/计数只看净正收费行 (退费行 _cnt<=0 不进列表); 合计仍走 fees 全量 (net)
        display_fees = fees[fees["_cnt"] > 0]

        if category:
            if category not in _VALID_CATEGORIES:
                return f"未知类别 '{category}', 可选: {'、'.join(_VALID_CATEGORIES)}"
            cat_all = fees[fees["_category"] == category]
            if cat_all.empty:
                return f"该患者无 {category} 费用记录"
            cat_disp = display_fees[display_fees["_category"] == category].sort_values(
                "_amount", ascending=False
            )
            lines = [f"{category}明细 (共{len(cat_disp)}项):", ""]
            for i, (_, row) in enumerate(cat_disp.iterrows(), 1):
                lines.append(f"  {i}. {row['_name']}: ¥{row['_amount']:.2f}{_row_extra(row)}")
            total = cat_all["_amount"].sum()
            lines.append("")
            lines.append(f"{category}合计: ¥{total:.2f}")
            return "\n".join(lines)

        # 目录模式 — 计数/Top3 按净正收费, 合计按 net (2.3)
        total_amount = fees["_amount"].sum()
        lines = [f"费用分类目录 (共{len(display_fees)}项, 总额¥{total_amount:.2f}):", ""]
        for cat in _VALID_CATEGORIES:
            cat_all = fees[fees["_category"] == cat]
            if cat_all.empty:
                continue
            cat_disp = display_fees[display_fees["_category"] == cat].sort_values(
                "_amount", ascending=False
            )
            cat_total = cat_all["_amount"].sum()
            pct = (cat_total / total_amount * 100) if total_amount > 0 else 0
            lines.append(f"【{cat}】{len(cat_disp)}项, 合计¥{cat_total:.2f} ({pct:.1f}%)")
            for i, (_, row) in enumerate(cat_disp.head(3).iterrows(), 1):
                lines.append(f"    {i}. {row['_name']}: ¥{row['_amount']:.2f}")
            if len(cat_disp) > 3:
                lines.append(f"    ... 还有 {len(cat_disp) - 3} 项")
            lines.append("")

        lines.append(f"用 category 取该类详情, 例: search_fees(patient_id=\"{patient_id}\", category=\"手术类\")")
        return "\n".join(lines)

    return execute
