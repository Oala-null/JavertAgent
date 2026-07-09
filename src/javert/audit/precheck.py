# -*- coding: utf-8 -*-
"""precheck — M1 重复收费的确定性事实预检 (pilot-deterministic-precheck).

M1 判定 = 「主项 A ∩ 附属 B 并存 + 文书无反证」。前半是一条查询能确定的事实,
本模块从规则声明的 A/B 项目集 (`Rule.precheck`) + 患者退费净额后的费用, 确定性判:
  - A 或 B 命中为空 → outcome=`clean` (短路 CLEAN, 不进 LLM) —— 原规则步骤 2/3.
  - A、B 都命中     → outcome=`facts` (事实成立, 给 LLM 窄问题 + 费用行锚点).
  - 费用数据不可用   → outcome=`skip` (fail-open, 走原 LLM 路径, 绝不误 CLEAN).

只认「A∩B 费用并存缺失」这一条最安全的短路 (设计 D2); 诊断指征/类别仍在
背景 prompt_addon 里由 LLM 兜底。匹配前先剔除完全充退项 (`fully_refunded_keys`)。

纯函数 (吃已切片 fee_df), 不调 LLM / 不写库。

Source: 本项目原创 (pilot-deterministic-precheck).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from javert.data.fee_netting import fee_group_key, fully_refunded_keys

from .result import Evidence
from .rule import PrecheckSpec

NAME_COL = "medins_list_name"
CODE_COL = "med_list_codg"
CNT_COL = "cnt"
DATE_COL = "fee_ocur_time"
_AMT_CANDIDATES = ("det_item_fee_sumamt", "unit_price", "pric", "金额")

CLEAN = "clean"
FACTS = "facts"
SKIP = "skip"

COEXIST = "coexist"
COMPANION = "companion"


@dataclass
class FeeHit:
    """一个命中的费用项目 (按项目名聚合净正收费)."""

    name: str
    amount: float = 0.0
    date: str = ""


@dataclass
class PrecheckResult:
    """预检结果. outcome ∈ {clean, facts, skip}."""

    outcome: str
    precheck_tag: str = ""
    reason: str = ""
    a_hits: list[FeeHit] = field(default_factory=list)
    b_hits: list[FeeHit] = field(default_factory=list)
    fact_block: str = ""
    evidence: list[Evidence] = field(default_factory=list)


def _safe_float(v) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _date_part(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat"):
        return ""
    return s.split()[0][:10]


def _norm(s: str) -> str:
    """去所有空白后比较 — 抹平录入空格差异 (项目名 "PET-CT 全身显像" vs fee "PET-CT全身显像"),
    与 LLM 的模糊匹配对齐, 避免因空格造成假 "无A项" 漏检 (换院字面鲁棒性另由
    make-rules-code-portable 解决)."""
    return "".join(s.split())


def _match_hits(items: list[str], rows: list[dict]) -> list[FeeHit]:
    """items 里任一项名 (去空白) 为 fee 行名 (去空白) 子串 → 命中; 按项目名聚合 (净正金额求和, 取最早日期)."""
    kws = [_norm(it) for it in items if it.strip()]
    agg: dict[str, FeeHit] = {}
    for r in rows:
        name = r["name"]
        nname = _norm(name)
        if not any(kw in nname for kw in kws):
            continue
        h = agg.get(name)
        if h is None:
            agg[name] = FeeHit(name=name, amount=r["amount"], date=r["date"])
        else:
            h.amount += r["amount"]
            if r["date"] and (not h.date or r["date"] < h.date):
                h.date = r["date"]
    return list(agg.values())


def _fact_lines(label: str, hits: list[FeeHit]) -> list[str]:
    out = [f"{label}:"]
    for h in hits:
        date = f"  [{h.date}]" if h.date else ""
        out.append(f"  - {h.name}  ¥{h.amount:.2f}{date}")
    return out


def _build_fact_block(a_hits: list[FeeHit], b_hits: list[FeeHit]) -> str:
    lines = ["【系统预检费用事实 (确定性, 已净退费)】"]
    lines += _fact_lines("A 类命中 (主项)", a_hits)
    lines += _fact_lines("B 类命中 (附属)", b_hits)
    lines += [
        "以上由系统按规则声明的 A/B 项目名对费用表做确定性子串匹配所得 "
        "(个别 B 项可能因名字相近而名义命中, 实际与 A 无关)。请按两步核实后裁决:",
        "(1) 确认上列 B 类确为 A 类的内涵/附属 (本应打包、不应单独收费)。"
        "若某 B 项与 A 无关 (例如呼吸/其它系统耗材因名字相近误入), 予以排除。",
        "(2) 排除后仍有真正附属 B 与 A 并存时, 调用 search_notes 核实文书是否存在反证 "
        "(分次手术 / 两次独立医嘱 / 第三方报告 / 不同时相或部位 / 特殊说明)。",
        "裁决:",
        "- 排除后无真正附属 B 与 A 并存 → CLEAN。",
        "- 有真正附属并存 且 无反证 → VIOLATION, evidence 引用上列相关费用项目 (系统已给编码锚点)。",
        "- 有真正附属并存 但 有反证 → CLEAN。",
        "费用事实已给定, 核实反证用 search_notes 即可, 通常不必再调 search_fees。",
    ]
    return "\n".join(lines)


def _build_companion_fact_block(a_hits: list[FeeHit], b_items: list[str]) -> str:
    """companion 模式事实块: 收了术式 A 但全费用单无任何必备配套 B (虚构信号, 非证明)."""
    lines = ["【系统预检费用事实 (确定性, 已净退费)】"]
    lines += _fact_lines("术式类命中 (A)", a_hits)
    kw = " / ".join(it for it in b_items if it.strip())
    lines += [
        f"必备配套 (B) 检索词: {kw}",
        "→ 全费用单无任何上述配套的净正收费命中。",
        "说明: 该术式若真实开展, 通常伴随上述配套药/耗材收费。配套全缺是虚构信号 (非证明)。",
        "请调用 search_notes 核实操作/手术记录, 按举证责任裁决:",
        "- 记录正面显示该操作确实执行 (配套缺失仅系记录/收费遗漏) → 视证据 CLEAN/INCONCLUSIVE。",
        "- 记录显示实际为其它操作 (如取栓而非溶栓) / 明确未执行该操作 → VIOLATION, "
        "evidence 引上列术式费用行 (系统已给编码锚点)。",
        "- 证据不足以正面反证操作已执行 → INCONCLUSIVE (证据缺失待人工)。",
        "配套缺失为确定性事实, 核实操作文书用 search_notes 即可, 通常不必再调 search_fees。",
    ]
    return "\n".join(lines)


def _hits_to_evidence(hits: list[FeeHit]) -> list[Evidence]:
    """命中费用行 → Evidence(source=search_fees, locator=项目名); hit_resolver 据此 join 码+锚点."""
    out: list[Evidence] = []
    for h in hits:
        date = f" [{h.date}]" if h.date else ""
        out.append(Evidence(
            source="search_fees",
            locator=h.name,
            text=f"¥{h.amount:.2f}{date} (系统预检确定性命中)",
        ))
    return out


def _extract_rows(fee_df: pd.DataFrame) -> list[dict]:
    """费用表 → 净正收费行 (剔除完全充退项 + 负 cnt 退费行). 纯提取, 不判命中."""
    refunded = fully_refunded_keys(fee_df)
    has_code = CODE_COL in fee_df.columns
    has_cnt = CNT_COL in fee_df.columns
    has_date = DATE_COL in fee_df.columns
    amt_col = next((c for c in _AMT_CANDIDATES if c in fee_df.columns), None)

    rows: list[dict] = []
    for _, r in fee_df.iterrows():
        name = str(r.get(NAME_COL) or "").strip()
        if not name or name.lower() == "nan":
            continue
        code = str(r.get(CODE_COL) or "").strip() if has_code else ""
        if code.lower() == "nan":
            code = ""
        if fee_group_key(code, name) in refunded:
            continue  # 完全充退项整组剔除
        cnt = _safe_float(r.get(CNT_COL)) if has_cnt else 1.0
        if has_cnt and cnt <= 0:
            continue  # 退费行 (负 cnt) 不进命中
        rows.append({
            "name": name,
            "amount": _safe_float(r.get(amt_col)) if amt_col else 0.0,
            "date": _date_part(r.get(DATE_COL)) if has_date else "",
        })
    return rows


def _coexist_result(a_hits: list[FeeHit], b_hits: list[FeeHit]) -> PrecheckResult:
    """M1 语义: A 或 B 空 → clean; A∩B 并存 → facts (核反证)."""
    if not a_hits:
        return PrecheckResult(
            outcome=CLEAN, precheck_tag="无A项",
            reason="预检: 未见 A 类 (主项) 费用命中, 规则不适用 → CLEAN",
        )
    if not b_hits:
        return PrecheckResult(
            outcome=CLEAN, precheck_tag="无B项", a_hits=a_hits,
            reason="预检: A 类命中但无 B 类 (附属) 费用, 未重复收费 → CLEAN",
        )
    return PrecheckResult(
        outcome=FACTS,
        precheck_tag="A∩B并存待核反证",
        reason="预检: A∩B 费用并存, 事实成立, 交 LLM 核实文书反证",
        a_hits=a_hits,
        b_hits=b_hits,
        fact_block=_build_fact_block(a_hits, b_hits),
        evidence=_hits_to_evidence(a_hits) + _hits_to_evidence(b_hits),
    )


def _companion_result(
    a_hits: list[FeeHit], b_hits: list[FeeHit], b_items: list[str]
) -> PrecheckResult:
    """companion 语义: A 无 → clean; A 有 B 无 → facts (虚构信号); A、B 均有 → skip 不注偏置."""
    if not a_hits:
        return PrecheckResult(
            outcome=CLEAN, precheck_tag="无术式项",
            reason="预检: 未见 A 类 (术式) 费用命中, 规则不适用 → CLEAN",
        )
    if b_hits:
        return PrecheckResult(
            outcome=SKIP, a_hits=a_hits, b_hits=b_hits,
            reason="预检: 术式与配套均在场, 虚构信号消失, 走原路径不注偏置",
        )
    return PrecheckResult(
        outcome=FACTS,
        precheck_tag="收术式无配套待核反证",
        reason="预检: 收术式但全费用单无必备配套, 事实成立, 交 LLM 核实操作文书反证",
        a_hits=a_hits,
        fact_block=_build_companion_fact_block(a_hits, b_items),
        evidence=_hits_to_evidence(a_hits),
    )


def run_precheck(spec: PrecheckSpec, fee_df: pd.DataFrame | None) -> PrecheckResult:
    """对一条规则的 A/B 项目集 + 患者费用做确定性预检. 纯函数.

    mode=coexist (M1 重复收费, 缺省): A∩B 并存缺失 → clean, 并存 → facts.
    mode=companion (术式↔配套): A 无 → clean, A 有 B 无 → facts, 双有 → skip.

    Args:
        spec: 规则的 PrecheckSpec (a_items / b_items / mode).
        fee_df: 该患者全量费用切片; None/空/缺列 → skip (fail-open).
    """
    if not spec.a_items or not spec.b_items:
        # 迁移不全的规则 (缺 A 或 B 集) → 无法预检, 走原路径
        return PrecheckResult(outcome=SKIP, reason="precheck spec 缺 A 或 B 项目集")

    if fee_df is None or len(fee_df) == 0 or NAME_COL not in fee_df.columns:
        return PrecheckResult(outcome=SKIP, reason="费用数据不可用 (缺表/缺列)")

    rows = _extract_rows(fee_df)
    a_hits = _match_hits(spec.a_items, rows)
    b_hits = _match_hits(spec.b_items, rows)

    if (spec.mode or COEXIST).lower() == COMPANION:
        return _companion_result(a_hits, b_hits, spec.b_items)
    return _coexist_result(a_hits, b_hits)
