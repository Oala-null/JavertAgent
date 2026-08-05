# -*- coding: utf-8 -*-
"""hit_resolver — 确定性「命中项目 / 锚点」解析器 (evidence-anchoring, D1/D3/D4).

把一条已落库审计 (run.evidence_json + run.tool_calls_json) 解析成统一的
``HitItem[]`` 结构, 一个组件喂两处渲染:
  - 违规卡「命中项目」块 (#5): 渲 `code_nat · name`, drug 规则附「限定内容」
  - 点证据跳原文 (#3): 每条 HitItem 带 `anchor` 供前端 openSourcePanel

**纯函数, 无 LLM / 无网络 / 不写库** (spec evidence-anchoring 硬约束):
  - 编码: join 该 **patient 实际 fee 行** (按 medins_list_name stem 匹配) → med_list_codg /
          medins_list_codg; 同名多规格按行列出, 不做全局猜 (D4)
  - 限定内容: rule.drug_rule_type 非空时 join configs/drug_audit_kb.json drugs[通用名]
              该 rule_type 的 basis; 剂型/复方需复核时标注 (D4)
  - 锚点: D3 阶梯 keyword → locator → text n-gram → tab-only, 永不静默失败

复用 ``drug_audit_lookup`` 的 stem 匹配 (fee_clean / kb_stem) 与 KB 加载, 保持与
M8 药品审计同一套确定性匹配规则.

Source: 本项目原创 (enhance-workbench-usability).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from javert.config import get_config
from javert.data.fee_netting import fee_group_key, fully_refunded_keys
from javert.tools.drug_audit_lookup import (
    _load_kb,
    code_match,
    fee_clean,
    kb_codes,
    kb_entries,
    kb_stem,
)

logger = logging.getLogger("javert.web.hit_resolver")


# =========================================================
# 模型
# =========================================================
class Anchor(BaseModel):
    """一条命中项目在原文 (文书/费用) 里的定位锚点.

    match_level (诊断/调试用, D3 阶梯哪一级命中):
      keyword — tool_calls 里 LLM 实搜的 keyword / 实际 fee 名, 原文精确子串 [最硬]
      locator — 落到 evidence.locator (子阶段) 那一段
      text    — evidence.text 去省略号后的最长 n-gram / 引文
      tab     — 都不中, 只开对的 tab (unresolved=True)              [兜底]
    """

    tab: str  # "notes" | "fees"
    subsection: str = ""
    query: str = ""
    char_start: int | None = None
    char_end: int | None = None
    unresolved: bool = False
    match_level: str = "tab"


class HitItem(BaseModel):
    """一条违规的命中项目 (#5 展示 + #3 跳转共用)."""

    source: str  # "drug" | "fee" | "note" (归一后的 kind)
    name: str  # 通用名 / 项目名 / 子阶段
    code_nat: str = ""  # med_list_codg (国家医保码)
    code_local: str = ""  # medins_list_codg (院内码)
    restriction: str = ""  # KB basis (仅 drug 规则非空)
    matched_fee_name: str = ""  # 原始 fee 名 verbatim (剂型/复方复核, D4)
    review_note: str = ""  # 例: "按通用名匹配, 剂型/复方需复核"
    anchor: Anchor


# =========================================================
# source → kind / tab 归一
# =========================================================
def _classify_source(source: str) -> tuple[str, str] | None:
    """evidence.source (tool 名 drug_audit_lookup / note_diagnosis / search_fees /
    search_lab_results / search_examinations) → (kind, tab).

    add-verdict-gate-layer: lab/exam 不再静默丢弃, surface 为可见命中项目 (项名+日期+数值);
    etl_warning (文件缺失标记, 非实证) 仍返 None 不锚.
    """
    s = (source or "").lower()
    if "etl" in s:
        return None  # 缺失标记: 表征文件没数字化, 不是命中项目
    if "drug" in s:
        return "drug", "fees"
    if "fee" in s:
        return "fee", "fees"
    if "lab" in s or "检验" in s or "化验" in s:
        return "lab", "labs"
    if "exam" in s or "检查" in s or "影像" in s:
        return "exam", "exams"
    if "note" in s or "diag" in s:
        return "note", "notes"
    return None


# =========================================================
# 编码富集 (D4) — 该 patient 实际 fee 行 stem 匹配
# =========================================================
_MAX_ROWS_PER_HIT = 8  # 同名多规格按行列出的上限 (实数据通常 1-3)


def _clean_code(v: Any) -> str:
    s = "" if v is None else str(v).strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def _drug_fee_rows(df) -> list[tuple[str, str, str]]:
    """fee_df → [(fee_name, code_nat, code_local), ...] (去 nan 名).

    fix-fee-refund-netting (4.2): 完全充退项 (净≤0) 的行不返回 — 避免命中锚到已抵消收费.
    """
    has_nat = "med_list_codg" in df.columns
    has_local = "medins_list_codg" in df.columns
    full_refunded = fully_refunded_keys(df)
    out: list[tuple[str, str, str]] = []
    for _, r in df.iterrows():
        raw = str(r.get("medins_list_name") or "").strip()
        if not raw or raw.lower() == "nan":
            continue
        code_nat = _clean_code(r.get("med_list_codg")) if has_nat else ""
        if fee_group_key(code_nat, raw) in full_refunded:
            continue
        out.append((
            raw,
            code_nat,
            _clean_code(r.get("medins_list_codg")) if has_local else "",
        ))
    return out


def _dedup_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for d in rows:
        key = (d["fee_name"], d["code_nat"])
        if key not in seen:
            seen[key] = d
    out = sorted(seen.values(), key=lambda d: (d["fee_name"], d["code_nat"]))
    return out[:_MAX_ROWS_PER_HIT]


def _match_fee_rows(
    name: str,
    fee_df: pd.DataFrame | None,
    *,
    is_drug: bool,
    kb_code_set: set[str] | None = None,
) -> list[dict[str, str]]:
    """返回该 patient fee 表里匹配 name 的 distinct (fee_name, code) 行.

    drug (KB 有码): 行国家码 ∈ kb_code_set → 命中 (码精确, 相似药零串味);
                    码全不中 → 退 kb_stem 子串, 但编码列留空 + needs_review (不猜配相似药码, D4/4.2).
    drug (KB 无码, 旧 KB/未 join): kb_stem(name) 子串匹配, 保留行码 (向后兼容).
    fee : fee_clean(name) 与 fee_clean(行名) 互为子串 (≥2 字), 保留行码.
    按 (fee_name, code_nat) 去重, 排序确定 (merge-stable).
    """
    if fee_df is None or len(fee_df) == 0 or "medins_list_name" not in fee_df.columns:
        return []
    all_rows = _drug_fee_rows(fee_df)

    if is_drug:
        codeset = set(kb_code_set or ())
        probe = kb_stem(name)
        if codeset:
            # 码主路: 行国家码 ∈ KB 知识点 code set
            coded = [
                {"fee_name": n, "code_nat": cn, "code_local": cl}
                for n, cn, cl in all_rows
                if cn and code_match({cn}, codeset)
            ]
            if coded:
                return _dedup_rows(coded)
            # 码全不中 → 名兜底: 编码留空 + needs_review (不臆造相似药码)
            if len(probe) < 2:
                return []
            fb = [
                {"fee_name": n, "code_nat": "", "code_local": "", "needs_review": "1"}
                for n, _cn, _cl in all_rows
                if probe in fee_clean(n)
            ]
            return _dedup_rows(fb)
        # 旧 KB 无码 → 名子串保留行码 (向后兼容)
        if len(probe) < 2:
            return []
        rows = [
            {"fee_name": n, "code_nat": cn, "code_local": cl}
            for n, cn, cl in all_rows
            if probe in fee_clean(n)
        ]
        return _dedup_rows(rows)

    # fee 分支不变: fee_clean 互为子串
    target = fee_clean(name).strip()
    if len(target) < 2:
        return []
    rows = [
        {"fee_name": n, "code_nat": cn, "code_local": cl}
        for n, cn, cl in all_rows
        if (lambda rc: target in rc or rc in target)(fee_clean(n))
    ]
    return _dedup_rows(rows)


# =========================================================
# 限定内容富集 (D4) — drug_audit_kb basis + 剂型复核
# =========================================================
def _lookup_kb_drug(
    generic_name: str, kb_drugs: dict[str, Any]
) -> tuple[str, list[dict], list[str]]:
    """按通用名 (精确 → kb_stem 兜底) 找 KB 条目, 返回 (matched_generic, entries, codes).

    兼容新版 drugs[通用名]={entries,codes} 与旧版 (值即 entries list).
    历史 evidence 的 locator 偶尔带尾部商品名 (如 ``盐酸尼卡地平注射液(佩尔)``);
    精确名未命中时再剥一层尾括号尝试, 但 KB 本身含括号的规范名仍由精确分支优先保护.
    """
    candidates = [generic_name]
    without_brand = re.sub(r"[（(][^（）()]{1,30}[)）]\s*$", "", generic_name).strip()
    if without_brand and without_brand != generic_name:
        candidates.append(without_brand)
    for candidate in candidates:
        val = kb_drugs.get(candidate)
        if val is not None:
            return candidate, kb_entries(val), kb_codes(val)
    for candidate in candidates:
        probe = kb_stem(candidate)
        if len(probe) >= 2:
            for g, v in kb_drugs.items():
                if probe == kb_stem(g) or probe in g:
                    return g, kb_entries(v), kb_codes(v)
    return generic_name, [], []


def _enrich_restriction(
    generic_name: str,
    drug_rule_type: str | None,
    kb_drugs: dict[str, Any],
    matched_fee_name: str,
    evidence_basis: str = "",
) -> tuple[str, str]:
    """(restriction, review_note). drug_rule_type 空 → ("", "").

    restriction 优先采用该次历史审计 evidence 明确引用的依据原文; evidence 无依据时,
    再取 KB drugs[通用名] 下该 rule_type 的 basis. 这样旧结果不会被现行 KB 静默改写.
    review_note: 通用名全名不是 matched fee 名 (cleaned) 子串 → "按通用名匹配, 剂型/复方需复核".
    """
    if not drug_rule_type:
        return "", ""
    evidence_basis = (evidence_basis or "").strip()
    generic_name, entries, _codes = _lookup_kb_drug(generic_name, kb_drugs)
    basis = evidence_basis
    if not basis and entries:
        basis = next(
            (e.get("basis", "") for e in entries if e.get("rule_type") == drug_rule_type),
            "",
        )
        if not basis:
            # rule_type 不匹配时退回第一条 basis (仍给专家看依据)
            basis = entries[0].get("basis", "")
    review = ""
    if matched_fee_name and generic_name not in fee_clean(matched_fee_name):
        review = "按通用名匹配, 剂型/复方需复核"
    return basis, review


# =========================================================
# 历史 drug evidence 兼容 — 显式药品名 / 依据原文 / note-like 纠偏
# =========================================================
_DRUG_NAME_IN_TEXT = re.compile(
    r"(?:药品|通用名)\s*[:：]\s*[「『“‘\"']?"
    r"([^；;\r\n「」『』“”‘’\"']{2,80}?)"
    r"[」』”’\"']?\s*(?=[；;]|\r?\n|$)"
)
_DRUG_NAME_IN_LOCATOR = re.compile(
    r"^\s*(?:药品|通用名)\s*[:：]\s*[「『“‘\"']?"
    r"(.{2,80}?)"
    r"[」』”’\"']?\s*$"
)
_BASIS_END = r"(?=\s*(?:检出逻辑|依据层级)\s*[:：]|\r?\n|$)"
_EXPLICIT_BASIS = re.compile(
    r"(?:限定/说明书依据|说明书依据|说明书适应[证症]|医保限定支付条件|医保限定条件|限定支付条件)"
    r"\s*[:：]\s*([^\r\n]*?)" + _BASIS_END
)
_BARE_BASIS = re.compile(
    r"依据(?!层级)\s*[:：]\s*([^\r\n]*?)" + _BASIS_END
)
_NOTE_LIKE_DRUG_LOCATORS = (
    "诊断",
    "病史",
    "病程",
    "入院记录",
    "出院记录",
    "出院小结",
    "现病史",
    "既往史",
    "个人史",
    "家族史",
    "体格检查",
    "手术记录",
    "检查记录",
    "检验记录",
)


def _extract_drug_evidence(locator: str, text: str) -> tuple[str, str]:
    """从历史 evidence 提取 (显式药品名, 显式依据原文).

    只认带 ``药品/通用名`` 与 ``依据/说明书依据/医保限定支付条件`` 前缀的字段,
    不从自由叙述猜测; 依据在 ``检出逻辑`` / ``依据层级`` / 换行前截断.
    """
    locator = (locator or "").strip()
    text = text or ""
    name = ""
    m = _DRUG_NAME_IN_TEXT.search(text)
    if m:
        name = m.group(1).strip()
    if not name:
        m = _DRUG_NAME_IN_LOCATOR.match(locator)
        if m:
            name = m.group(1).strip()

    basis = ""
    m = _EXPLICIT_BASIS.search(text)
    if m:
        basis = m.group(1).strip().rstrip("；;").strip()
    elif name:
        # 裸 ``依据:`` 只在同条 evidence 已显式标出药品名时接受, 避免把诊断依据误当药品依据.
        m = _BARE_BASIS.search(text)
        if m:
            basis = m.group(1).strip().rstrip("；;").strip()
    return name, basis


def _is_note_like_drug_evidence(locator: str, explicit_drug_name: str) -> bool:
    """旧结果把 drug_audit_lookup 返回的诊断也标成 source=drug; 按 locator 纠偏."""
    if explicit_drug_name:
        return False
    value = (locator or "").strip()
    return any(marker in value for marker in _NOTE_LIKE_DRUG_LOCATORS)


# =========================================================
# 通用占位 locator → 提取实际被查项目名
# =========================================================
# LLM 搜索 fee/lab/exam 未命中时, 常把 locator 写成动作占位 ("费用明细检索" /
# "药品类明细检索" / "search_lab_results(...)") 而非具体项目名, 导致命中按钮只显示
# "费用明细" 之类无意义词. 这里识别这类占位并从 text/locator 里捞出真实被查项目名.
_GENERIC_MARKERS = (
    "费用明细", "药品类明细", "药品明细", "明细检索", "明细搜索",
    "费用检索", "费用搜索", "检验报告检索", "检验报告索引",
    "检查报告检索", "检查报告索引", "sy_检验", "sy_patient_examination",
)
# keyword='X' / item_keyword='X' (X 截到下一个引号/逗号/括号前)
_KEYWORD_ARG = re.compile(
    r"(?:item_keyword|keyword)\s*=\s*['\"“‘「『]?([^'\"“”‘’」』，,）)]{2,40})"
)
# 多引号风格成对捕获 (「」/『』/“”/‘’/ASCII 双引/ASCII 单引)
_QUOTE_PAIRS = re.compile(
    r"「([^」]{2,40})」"
    r"|『([^』]{2,40})』"
    r"|“([^”]{2,40})”"
    r"|‘([^’]{2,40})’"
    r"|\"([^\"]{2,40})\""
    r"|'([^']{2,40})'"
)
# locator 括号内 (如 "费用明细搜索(肩锁)")
_PAREN = re.compile(r"[(（]([^)）]{2,40})[)）]")


def _is_generic_locator(name: str) -> bool:
    """locator 是否为"搜索动作"占位 (而非具体项目/药品/检验名)."""
    n = (name or "").strip()
    if not n:
        return False
    if n.startswith("search_"):
        return True
    if n.endswith(("索引", "检索", "搜索")):
        return True
    if n in ("检验报告", "检查报告"):
        return True
    return any(mk in n for mk in _GENERIC_MARKERS)


def _extract_searched_term(locator: str, text: str, search_keywords: tuple = ()) -> str:
    """从 locator/text/工具实搜词捞真实被查项目名.

    优先级: keyword= 参数 > 引文 > locator 括号内 > tool_call 实搜关键词.
    最后一档处理"汇总型"证据 (文书里只列了一堆找到的项 / 或"均未找到"概述, 没有单一
    可提取项名), 此时退回该 run 实际搜过的关键词 (search_fees/lab/exam 的 keyword):
    文中出现过的优先, 否则取第一个 —— 总比按钮上显示"费用明细"强. 都没有 → "".
    """
    for s in (locator, text):
        if not s:
            continue
        m = _KEYWORD_ARG.search(s)
        if m:
            return m.group(1).strip()
    for s in (text, locator):  # text 信息更全, 优先
        if not s:
            continue
        m = _QUOTE_PAIRS.search(s)
        if m:
            return next(g for g in m.groups() if g).strip()
    if locator:
        m = _PAREN.search(locator)
        if m:
            return m.group(1).strip()
    kws = [k for k in search_keywords if k and len(k) >= 2]
    if kws:
        return next((k for k in kws if text and k in text), kws[0])
    return ""


# =========================================================
# 锚点解析 (D3 阶梯)
# =========================================================
_QUOTED = re.compile(r"[「『]([^」』]{2,40})[」』]")
_CJK_RUN = re.compile(r"[一-鿿A-Za-z0-9]{2,}")


def _quoted_or_ngram(text: str) -> str:
    """evidence.text 去省略号后取一个可高亮子串: 优先 「」/『』 引文, 否则最长 CJK/字母数字 run."""
    text = (text or "").strip().strip("·").strip()
    m = _QUOTED.search(text)
    if m:
        return m.group(1).strip()
    runs = _CJK_RUN.findall(text)
    if runs:
        return max(runs, key=len)[:24]
    return ""


def _first_keyword_in_text(keywords: list[str], text: str) -> str:
    """tool_calls 里出现且为 evidence.text 子串的最长 keyword (原文精确子串)."""
    text = text or ""
    cands = [k for k in keywords if k and len(k) >= 2 and k in text]
    return max(cands, key=len) if cands else ""


def _resolve_note_anchor(
    locator: str, text: str, note_keywords: list[str], ev_anchor: dict | None = None,
) -> Anchor:
    locator = (locator or "").strip()
    text = text or ""
    # 0. evidence.anchor — 工具增强后回传的精确锚点 (前向, 最高优先).
    #    anchor 是未受 pydantic 校验的信任边界 (Evidence.anchor: dict|None), 非 int
    #    偏移要 coerce 成 None, 否则 Anchor 构造抛 ValidationError 拖垮整个 run / backfill.
    if ev_anchor and ev_anchor.get("query"):
        cs = ev_anchor.get("char_start")
        ce = ev_anchor.get("char_end")
        return Anchor(
            tab="notes",
            subsection=str(ev_anchor.get("subsection") or locator),
            query=str(ev_anchor.get("query")),
            char_start=cs if isinstance(cs, int) else None,
            char_end=ce if isinstance(ce, int) else None,
            match_level="evidence",
        )
    # 1. keyword — 原文精确子串, char offset 精确
    kw = _first_keyword_in_text(note_keywords, text)
    if kw:
        pos = text.find(kw)
        return Anchor(
            tab="notes", subsection=locator, query=kw,
            char_start=pos, char_end=pos + len(kw), match_level="keyword",
        )
    soft = _quoted_or_ngram(text)
    # 2. locator — 落到子阶段, 软 query 段内高亮
    if locator:
        return Anchor(tab="notes", subsection=locator, query=soft, match_level="locator")
    # 3. text n-gram
    if soft:
        return Anchor(tab="notes", subsection="", query=soft, match_level="text")
    # 4. tab-only 兜底
    return Anchor(tab="notes", subsection="", query="", unresolved=True, match_level="tab")


def _resolve_fee_anchor(matched_fee_name: str, hit_name: str = "") -> Anchor:
    """费用/药品命中项目锚点 (D1: 跳转与编码富集解耦).

    - 有匹配的实际 fee 名 → 用它做 query (费用 tab 内必是 verbatim 子串), match_level=keyword.
    - 无 fee 行匹配 (拿不到国家码) → 退而用**命中名**做 query, match_level=name: 费用 tab
      子串高亮任何含该名的行 (模糊但永远可跳). 不再 unresolved —— 修复「重组人血」跳不动.
    - 命中名也空 (理论不至, 兜底) → tab-only unresolved.

    注: 跳转可达性与编码富集 (med_list_codg) 解耦 —— 编码匹配严格不变, 只是缺码不再拖累跳转.
    """
    q = (matched_fee_name or "").strip()
    if q:
        return Anchor(tab="fees", subsection="", query=q, match_level="keyword")
    name = (hit_name or "").strip()
    if name:
        return Anchor(tab="fees", subsection="", query=name, match_level="name")
    return Anchor(tab="fees", subsection="", query="", unresolved=True, match_level="tab")


# =========================================================
# 主入口
# =========================================================
def _collect_note_keywords(tool_calls: list[dict]) -> list[str]:
    """note 类 tool_call 的 keyword / section 参数 (原文子串候选)."""
    out: list[str] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):  # 信任边界: 损坏 JSON 里的非 dict 元素跳过
            continue
        name = str(tc.get("tool_name") or "").lower()
        if "note" not in name:
            continue
        args = tc.get("arguments")
        if not isinstance(args, dict):
            continue
        for k in ("keyword", "section"):
            v = args.get(k)
            if v and isinstance(v, str):
                out.append(v.strip())
    return out


def _collect_search_keywords(tool_calls: list[dict]) -> list[str]:
    """fee/lab/exam/drug 检索类 tool_call 的 keyword/item_keyword 参数 (实搜项名, 去重保序).

    汇总型证据 (无单一项名) 时给 _extract_searched_term 当兜底名 —— 显示实际搜过的项,
    而非按钮上无意义的"费用明细".
    """
    out: list[str] = []
    seen: set[str] = set()
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("tool_name") or "").lower()
        if not any(t in name for t in ("fee", "lab", "exam", "drug")):
            continue
        args = tc.get("arguments")
        if not isinstance(args, dict):
            continue
        for k in ("keyword", "item_keyword"):
            v = args.get(k)
            if v and isinstance(v, str):
                vv = v.strip()
                if vv and vv not in seen:
                    seen.add(vv)
                    out.append(vv)
    return out


def _actual_fee_line_name(hit: HitItem) -> str:
    """命中项对应的实际收费行名; 空表示没有可用于跨 source 合并的收费行业务键."""
    return fee_clean(hit.matched_fee_name).strip() if hit.matched_fee_name else ""


def _same_actual_fee_line(left: HitItem, right: HitItem) -> bool:
    """drug / fee 是否指向同一实际收费行.

    行名必须完全一致; 两边同时有国家码/院内码时还必须相等. 因此不同规格/不同收费行
    即使通用名相同也会保留, 不做 stem 级宽松合并.
    """
    left_name = _actual_fee_line_name(left)
    right_name = _actual_fee_line_name(right)
    if not left_name or left_name != right_name:
        return False
    if left.code_nat and right.code_nat and left.code_nat != right.code_nat:
        return False
    if left.code_local and right.code_local and left.code_local != right.code_local:
        return False
    return True


def _merge_drug_fee_pair(drug: HitItem, fee: HitItem) -> HitItem:
    """同收费行 drug+fee 合一，编码始终服从 drug 的防串药判定。

    drug 码不匹配时会刻意留空并要求复核；不得再用宽松 fee 命中的编码补回。
    """
    anchor = drug.anchor
    if anchor.unresolved and not fee.anchor.unresolved:
        anchor = fee.anchor
    return drug.model_copy(update={
        "matched_fee_name": drug.matched_fee_name or fee.matched_fee_name,
        "restriction": drug.restriction or fee.restriction,
        "review_note": drug.review_note or fee.review_note,
        "anchor": anchor,
    })


def _merge_drug_fee_hits(hits: list[HitItem]) -> list[HitItem]:
    """按实际收费行业务键合并重复的 drug+fee, 同一通用名的不同规格仍逐行保留."""
    out: list[HitItem] = []
    for hit in hits:
        if hit.source not in ("drug", "fee") or not _actual_fee_line_name(hit):
            out.append(hit)
            continue
        opposite = "fee" if hit.source == "drug" else "drug"
        match_index = next(
            (
                index
                for index, existing in enumerate(out)
                if existing.source == opposite and _same_actual_fee_line(existing, hit)
            ),
            None,
        )
        if match_index is None:
            out.append(hit)
            continue
        existing = out[match_index]
        drug = hit if hit.source == "drug" else existing
        fee = existing if hit.source == "drug" else hit
        out[match_index] = _merge_drug_fee_pair(drug, fee)
    return out


def has_duplicate_drug_fee_hits(hits: list[HitItem]) -> bool:
    """缓存里是否仍有指向同一实际收费行的 drug + fee 重复项。"""
    drugs = [h for h in hits if h.source == "drug"]
    fees = [h for h in hits if h.source == "fee"]
    return any(_same_actual_fee_line(drug, fee) for drug in drugs for fee in fees)


def resolve_hits_from_json(
    evidence_json: str | None,
    tool_calls_json: str | None,
    drug_rule_type: str | None,
    patient_fee_df: pd.DataFrame | None,
    kb_drugs: dict[str, Any] | None,
) -> list[HitItem]:
    """底层纯函数: 直接吃 json 串 + 已切片 fee df + KB drugs dict, 组装 HitItem[]."""
    try:
        evidence = json.loads(evidence_json) if evidence_json else []
    except (ValueError, TypeError):
        evidence = []
    try:
        tool_calls = json.loads(tool_calls_json) if tool_calls_json else []
    except (ValueError, TypeError):
        tool_calls = []
    if not isinstance(evidence, list):
        evidence = []
    if not isinstance(tool_calls, list):
        tool_calls = []
    kb_drugs = kb_drugs or {}
    note_keywords = _collect_note_keywords(tool_calls)
    search_keywords = tuple(_collect_search_keywords(tool_calls))

    hits: list[HitItem] = []
    seen: set[tuple] = set()
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        kind_tab = _classify_source(str(ev.get("source") or ""))
        if kind_tab is None:
            continue
        kind = kind_tab[0]
        raw_locator = str(ev.get("locator") or "").strip()
        text = str(ev.get("text") or "")
        explicit_drug_name = ""
        evidence_basis = ""
        if kind == "drug":
            explicit_drug_name, evidence_basis = _extract_drug_evidence(
                raw_locator, text
            )
            if _is_note_like_drug_evidence(raw_locator, explicit_drug_name):
                kind = "note"
        name = raw_locator or _quoted_or_ngram(text)  # locator 空时退回 text 引文做名字
        if not name:
            continue
        # 通用占位 locator (LLM 搜索未命中时常写 "费用明细检索"/"search_lab_results(...)")
        # → 捞出实际被查项目/药品名, 否则命中按钮只显示 "费用明细" 之类无意义词.
        if _is_generic_locator(name):
            real = _extract_searched_term(raw_locator, text, search_keywords)
            if real:
                name = real

        ev_anchor = ev.get("anchor") if isinstance(ev.get("anchor"), dict) else None
        if kind == "note":
            anchor = _resolve_note_anchor(raw_locator, text, note_keywords, ev_anchor)
            item = HitItem(source="note", name=name, anchor=anchor)
            key = ("note", name, anchor.query)
            if key not in seen:
                seen.add(key)
                hits.append(item)
            continue

        if kind in ("lab", "exam"):
            # 检验/检查指征 — surface 为可见命中项目 (项名 = name, 数值/结论 = text 摘录).
            # 锚点统一到合并的「检验记录」tab (labs); name 具体 → 可跳转高亮该项,
            # 仍是通用占位 → 仅切 tab (unresolved, 不强求高亮).
            value = _quoted_or_ngram(text) or (text or "").strip()[:40]
            generic = _is_generic_locator(name)
            anchor = Anchor(
                tab="labs", subsection=raw_locator,
                query=("" if generic else name),
                unresolved=generic,
                match_level=("tab" if generic else "name"),
            )
            item = HitItem(
                source=kind, name=name, matched_fee_name=value, anchor=anchor,
            )
            key = (kind, name, value)
            if key not in seen:
                seen.add(key)
                hits.append(item)
            continue

        # drug / fee — 按实际 fee 行出码 (可能多行多码)
        # drug 码优先: 取该证据通用名的 KB 国家码集合, 行码 ∈ 集合才命中 (相似药零串味)
        kb_code_set = None
        if kind == "drug":
            lookup_name = explicit_drug_name or name
            _g, _ents, _codes = _lookup_kb_drug(lookup_name, kb_drugs)
            # KB 命中时统一显示规范通用名; 未命中仍保留历史 evidence 的显式药品名.
            name = _g if _ents else lookup_name
            kb_code_set = set(_codes)
        rows = _match_fee_rows(
            name, patient_fee_df, is_drug=(kind == "drug"), kb_code_set=kb_code_set
        )
        if not rows:
            # 公开费用/药品命中必须关联患者实际净正收费组；搜索词和 locator 只留内部追溯。
            continue
        for row in rows:
            restriction, review = (
                _enrich_restriction(
                    name, drug_rule_type, kb_drugs, row["fee_name"],
                    evidence_basis=evidence_basis,
                )
                if kind == "drug"
                else ("", "")
            )
            # 码全不中、靠名兜底命中的行: 编码留空 + 复核标注 (不臆造相似药码, 4.2)
            if row.get("needs_review"):
                review = "无国家码匹配, 按通用名兜底, 需复核"
            anchor = _resolve_fee_anchor(row["fee_name"])
            item = HitItem(
                source=kind,
                name=name,
                code_nat=row["code_nat"],
                code_local=row["code_local"],
                restriction=restriction,
                matched_fee_name=row["fee_name"],
                review_note=review,
                anchor=anchor,
            )
            key = (kind, name, row["code_nat"], row["fee_name"])
            if key not in seen:
                seen.add(key)
                hits.append(item)
    return _merge_drug_fee_hits(hits)


# =========================================================
# 序列化 (anchors_json 缓存 + 回填脚本共用; 确定性 sort_keys)
# =========================================================
def hits_to_json(hits: list[HitItem]) -> str:
    """HitItem[] → 确定性 JSON 串 (sort_keys, 同输入 byte-identical)."""
    return json.dumps([h.model_dump() for h in hits], ensure_ascii=False, sort_keys=True)


def hits_from_json(s: str | None) -> list[HitItem] | None:
    """anchors_json 缓存 → HitItem[]; 空/损坏 → None (调用方回退现算)."""
    if not s:
        return None
    try:
        data = json.loads(s)
        if not isinstance(data, list):
            return None
        return [HitItem.model_validate(d) for d in data]
    except Exception:  # noqa: BLE001
        return None


def resolve_hits(
    run,
    rule_drug_type: str | None,
    *,
    patient_fee_df: pd.DataFrame | None = None,
    kb_drugs: dict[str, Any] | None = None,
) -> list[HitItem]:
    """纯函数. 读 run.evidence_json + run.tool_calls_json (source∈{fee,drug,note})
    + 已切片 patient_fee_df + KB drugs → HitItem[]. 二次调用 byte-identical.

    run 只需带 .evidence_json / .tool_calls_json (RunWithReviews 满足).
    """
    return resolve_hits_from_json(
        getattr(run, "evidence_json", None),
        getattr(run, "tool_calls_json", None),
        rule_drug_type,
        patient_fee_df,
        kb_drugs,
    )


# =========================================================
# loader / KB 加载便利封装 (路由用; 非纯, 读盘 + 进程缓存)
# =========================================================
_DEFAULT_KB_REL = "configs/drug_audit_kb.json"


def default_kb_path() -> Path:
    return get_config().resolve(_DEFAULT_KB_REL)


def load_kb_drugs(kb_path: Path | None = None) -> dict[str, Any]:
    """drug_audit_kb.json → drugs dict {通用名:{entries,codes}} (复用进程缓存)."""
    kb = _load_kb(kb_path or default_kb_path())
    return kb.get("drugs", {})


def resolve_hits_for_run(run, rule_drug_type: str | None, loader, kb_path=None) -> list[HitItem]:
    """路由用: 从 loader 取该 patient fee 切片 + KB, 现算 HitItem[]."""
    try:
        fee_df = loader.get_fees(run.patient_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("resolve_hits_for_run 取 fee 失败 patient=%s: %s", run.patient_id, e)
        fee_df = None
    kb_drugs = load_kb_drugs(kb_path) if rule_drug_type else {}
    return resolve_hits(run, rule_drug_type, patient_fee_df=fee_df, kb_drugs=kb_drugs)
