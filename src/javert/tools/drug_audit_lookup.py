# -*- coding: utf-8 -*-
"""drug_audit_lookup — 药品违规审计工具 (4 份监管 KB × 患者用药确定性命中).

与 `drug_indication` (52 药, M2 反向洗白证据) 职责完全不同, 两者并存:
  - bulk  (patient_id[, rule_type]) → 该患者用药 ∩ KB 的命中药 + 各自限定/说明书原文
                                       + 患者病案首页诊断 (shi_zd ground truth)
  - single(drug_name)               → 单药 KB 事实 (全部 rule_type 条目) / 显式 not-found

确定性匹配 (设计 D3): 通用名 stem 子串.
  - fee 名剥 `(基)(集)(国谈)` 等前缀标记
  - KB 通用名剥剂型后缀 (片/胶囊/注射液/...)
  - stem 长度 ≥2 且为 (剥前缀后) fee 名子串 → 命中
  - 工具回传**原始 fee 名**, 供 LLM 复核复方/同名歧义

诊断源 (设计 D7): bulk 模式同时返回 shi_zd 病案首页诊断 (ground truth, 主诊 maindiag_flag=1),
供 M8 做「命中药 × 诊断」on-label 比对; LLM 仍可用 note_diagnosis 兜底.
之所以把诊断塞进本工具输出: Javert 无 shi_zd 工具暴露给 LLM (note_diagnosis 只读 case_notes),
而本工具本就在做患者级数据访问, 是承载 ground-truth 诊断的自然位置.

Source: 本项目原创 (add-drug-audit-rules), 非 zadig_agent 移植.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from javert.audit.runner import RETAIN_HEAD_MARKER  # 分段截断标记 (必留头部/可截明细)
from javert.data.fee_netting import fee_group_key, fully_refunded_keys
from javert.data.loader import DataLoader
from javert.routing.adapter import _load_zd_index  # 复用 routing 的 shi_zd 缓存索引

logger = logging.getLogger("javert.tools.drug_audit_lookup")

REQUIRES_PATIENT_ID = True

DESCRIPTION = (
    "药品违规审计: bulk(patient_id[, rule_type]) 返回该患者用药命中监管知识库的药品 "
    "(限适应症/超说明书/限二线/禁忌症) + 限定/说明书原文 + 病案首页诊断; "
    "single(drug_name) 返回单药知识库事实. 命中知识库≠违规, 需结合诊断判定."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号 (bulk 模式)"},
        "rule_type": {
            "type": "string",
            "description": "可选, 只看某类型: 限适应症/超说明书/限二线/禁忌症",
        },
        "drug_name": {
            "type": "string",
            "description": "药品通用名 (single 模式; 传了 drug_name 即走单药查询)",
        },
    },
}

# shi_fee.medins_chrgitm_type 里属于"药品"的三类标签 (其余是化验/检查/治疗/手术...)
DRUG_CHRGITM_TYPES = frozenset({"西药", "中药", "草药"})

RULE_TYPES = ("限适应症", "超说明书", "限二线", "禁忌症")

# ────────────────────────── stem 匹配 (确定性, 纯函数; build_drug_kb 复用) ──────────────────────────

# fee 名前缀标记: (基)(集)(国谈)(集）... 半角/全角括号都剥
_PREFIX_MARKER = re.compile(r"^[（(][^（()）]*[)）]")

# KB 通用名剂型前缀 (注射用头孢... 的"注射用")
_KB_PREFIX_TOKENS = ("注射用", "吸入用")

# KB 通用名剂型后缀 — 长 token 在前 (贪心剥最长), 反复剥 ("脂肪乳注射液" → "脂肪乳" → root)
_KB_SUFFIX_TOKENS = (
    "脂肪乳注射液", "脂肪乳", "肠溶胶囊", "肠溶片", "缓释胶囊", "缓释片",
    "分散片", "咀嚼片", "泡腾片", "口崩片", "干混悬剂", "混悬液",
    "口服溶液", "口服液", "软胶囊", "滴眼液", "滴鼻液", "喷雾剂",
    "吸入剂", "凝胶剂", "乳膏剂", "注射液", "颗粒剂", "颗粒",
    "胶囊", "片剂", "糖浆", "滴丸", "软膏", "乳膏", "凝胶",
    "片", "丸", "散", "栓",
)


def fee_clean(fee_name: str) -> str:
    """剥 fee 名开头的括号标记 (基)(集)(国谈) 等, 返回剩余文本."""
    s = (fee_name or "").strip()
    while True:
        m = _PREFIX_MARKER.match(s)
        if not m:
            break
        s = s[m.end():].strip()
    return s


def kb_stem(generic_name: str) -> str:
    """KB 通用名剥剂型前后缀得 stem (反复剥, stem 不低于 2 字)."""
    s = (generic_name or "").strip()
    for p in _KB_PREFIX_TOKENS:
        if s.startswith(p) and len(s) - len(p) >= 2:
            s = s[len(p):]
    changed = True
    while changed:
        changed = False
        for suf in _KB_SUFFIX_TOKENS:  # 已按长度从长到短排
            if s.endswith(suf) and len(s) - len(suf) >= 2:
                s = s[: -len(suf)]
                changed = True
                break
    return s


def stem_match(generic_name: str, fee_name: str) -> bool:
    """KB 通用名 stem (≥2 字) 是否为 (剥前缀后) fee 名子串."""
    stem = kb_stem(generic_name)
    if len(stem) < 2:
        return False
    return stem in fee_clean(fee_name)


def code_match(fee_codes: set[str], kb_code_set: set[str]) -> bool:
    """国家医保药品码精确交集 (fix-drug-code-match D1/D4 单点真值).

    患者某药 fee 行的国家码集合 ∩ KB 知识点码集合 非空 → 命中.
    与子串法不同, 码精确 → 相似药名 (奥美拉唑/艾司奥美拉唑) 因码不同零串味.
    drug_audit_lookup 与 hit_resolver 共用此函数, 保证审计命中与工作台编码一致.
    """
    return bool(fee_codes & kb_code_set)


# ─── drugs[通用名] 值形态兼容 (新版 {entries,codes} / 旧版 list) ───
def kb_entries(drug_val: Any) -> list[dict]:
    """取某通用名的 entries 列表. 新版值为 {entries,codes} dict; 旧版值即 list."""
    if isinstance(drug_val, dict):
        return drug_val.get("entries", []) or []
    return drug_val or []


def kb_codes(drug_val: Any) -> list[str]:
    """取某通用名的国家药品码列表 (旧版无 codes → 空)."""
    if isinstance(drug_val, dict):
        return drug_val.get("codes", []) or []
    return []


# ────────────────────────── KB 加载 (lazy + 进程内缓存) ──────────────────────────

_kb_cache: dict[str, dict] = {}
_kb_stems_cache: dict[str, list[tuple[str, str, list[dict], list[str]]]] = {}


def _load_kb(kb_path: Path) -> dict:
    key = str(kb_path)
    if key in _kb_cache:
        return _kb_cache[key]
    if not kb_path.exists():
        logger.warning("drug_audit_kb.json 不存在: %s", kb_path)
        _kb_cache[key] = {"version": "?", "drugs": {}}
        return _kb_cache[key]
    with open(kb_path, encoding="utf-8") as f:
        _kb_cache[key] = json.load(f)
    logger.info(
        "药品监管 KB v%s: %d 通用名",
        _kb_cache[key].get("version", "?"),
        len(_kb_cache[key].get("drugs", {})),
    )
    return _kb_cache[key]


def _kb_stems(kb_path: Path) -> list[tuple[str, str, list[dict], list[str]]]:
    """预计算 [(通用名, stem, entries, codes), ...], stem 长度 ≥2 才入表."""
    key = str(kb_path)
    if key in _kb_stems_cache:
        return _kb_stems_cache[key]
    kb = _load_kb(kb_path)
    out: list[tuple[str, str, list[dict], list[str]]] = []
    for generic, drug_val in kb.get("drugs", {}).items():
        stem = kb_stem(generic)
        if len(stem) >= 2:
            out.append((generic, stem, kb_entries(drug_val), kb_codes(drug_val)))
    _kb_stems_cache[key] = out
    return out


# ────────────────────────── bulk / single 主逻辑 ──────────────────────────

def lookup_patient_drugs(
    patient_id: str,
    loader: DataLoader,
    kb_path: Path,
    zd_path: Path,
    rule_type: str | None = None,
) -> dict[str, Any]:
    """bulk 模式: 患者用药 ∩ KB → 命中药 (+ 各 rule_type 依据) + 病案首页诊断."""
    fees_df = loader.get_fees(patient_id)
    # 收集药品 fee 行 (名, 国家码) — 码为主路, 名为兜底 (设计 D1)
    coded_rows: list[tuple[str, str]] = []   # (name, code) code 非空
    nocode_names: list[str] = []             # med_list_codg 空的 fee 名
    distinct_fees: list[str] = []            # 全部去重 fee 名 (计数 + 旧 KB 名兜底)
    _seen_names: set[str] = set()
    # fix-fee-refund-netting: 完全充退 (净≤0) 的药不算患者用过 → 整组排除 (drug-audit spec)
    full_refunded = fully_refunded_keys(fees_df)
    if not fees_df.empty and "medins_list_name" in fees_df.columns:
        ct_col = "medins_chrgitm_type"
        has_code = "med_list_codg" in fees_df.columns
        for _, row in fees_df.iterrows():
            name = str(row.get("medins_list_name") or "").strip()
            if not name or name == "nan":
                continue
            ct = str(row.get(ct_col) or "").strip() if ct_col in fees_df.columns else ""
            if ct and ct not in DRUG_CHRGITM_TYPES:
                continue
            code = ""
            if has_code:
                raw = str(row.get("med_list_codg") or "").strip()
                code = "" if raw.lower() in ("", "nan", "none", "null") else raw
            if fee_group_key(code, name) in full_refunded:
                continue  # 净≤0 完全充退药, 不计入用药集
            if code:
                coded_rows.append((name, code))
            else:
                nocode_names.append(name)
            if name not in _seen_names:
                _seen_names.add(name)
                distinct_fees.append(name)
    fee_code_set = {c for _, c in coded_rows}

    # 命中: 按 (通用名, rule_type) 聚合, 记录命中的原始 fee 名
    # 码主路: 患者 fee 行国家码 ∈ 该知识点 code set; 无码 fee 行退 stem 子串 (needs_review).
    matches: dict[tuple[str, str], dict[str, Any]] = {}
    for generic, stem, entries, codes in _kb_stems(kb_path):
        code_set = set(codes)
        # 码命中: 该知识点码集合 ∩ 患者已编码 fee 行
        matched_codes = fee_code_set & code_set if code_set else set()
        code_fees = [n for n, c in coded_rows if c in matched_codes]
        # 名兜底: 无码 fee 行 (med_list_codg 空) 按 stem 子串
        name_fees = [n for n in nocode_names if stem in fee_clean(n)] if len(stem) >= 2 else []
        # 知识点无码 (旧 KB / 未 join) → 退回全量 fee 名子串, 向后兼容
        if not code_set and len(stem) >= 2:
            name_fees = [n for n in distinct_fees if stem in fee_clean(n)]
        # 去重保序: 码命中在前
        hit_fees: list[str] = []
        for n in code_fees + name_fees:
            if n not in hit_fees:
                hit_fees.append(n)
        if not hit_fees:
            continue
        needs_review = not code_fees  # 仅靠名兜底命中 → 需人工复核剂型/复方歧义
        for entry in entries:
            rt = entry.get("rule_type", "")
            if rule_type and rt != rule_type:
                continue
            mkey = (generic, rt)
            if mkey not in matches:
                matches[mkey] = {
                    "generic_name": generic,
                    "rule_type": rt,
                    "basis": entry.get("basis", ""),
                    "detect_logic": entry.get("detect_logic", ""),
                    "fee_names": [],
                    "needs_review": needs_review,
                }
            for hf in hit_fees:
                if hf not in matches[mkey]["fee_names"]:
                    matches[mkey]["fee_names"].append(hf)

    match_list = sorted(
        matches.values(), key=lambda m: (m["rule_type"], m["generic_name"])
    )

    # 病案首页诊断 (shi_zd ground truth)
    diagnoses: list[dict[str, str]] = []
    zd_idx = _load_zd_index(str(zd_path))
    for zd in zd_idx.get(patient_id, []):
        name = zd.get("diag_name") or zd.get("inhosp_diag_name") or ""
        if not name:
            continue
        diagnoses.append({
            "name": name,
            "code": zd.get("diag_code", ""),
            "is_main": str(zd.get("maindiag_flag") or "").strip() in ("1", "1.0"),
        })

    return {
        "mode": "bulk",
        "patient_id": patient_id,
        "rule_type_filter": rule_type or "",
        "total_drug_fees": len(distinct_fees),
        "matches": match_list,
        "diagnoses": diagnoses,
        "zd_available": bool(zd_idx.get(patient_id)),
    }


def lookup_single_drug(drug_name: str, kb_path: Path) -> dict[str, Any]:
    """single 模式: 返回该药全部 rule_type 条目 / 显式 not-found (无联网回退)."""
    kb = _load_kb(kb_path)
    drugs = kb.get("drugs", {})
    # 先精确名, 再 stem 互为子串的宽松匹配 (single 模式按通用名查 KB 事实, 不涉患者 fee)
    entries = kb_entries(drugs[drug_name]) if drug_name in drugs else None
    matched_generic = drug_name
    if entries is None:
        target_stem = kb_stem(drug_name)
        for generic, drug_val in drugs.items():
            if stem_match(generic, drug_name) or (
                len(target_stem) >= 2 and target_stem in generic
            ):
                entries = kb_entries(drug_val)
                matched_generic = generic
                break
    if not entries:
        return {
            "mode": "single",
            "drug_name": drug_name,
            "found": False,
            "entries": [],
            "note": "未在药品监管知识库 (限适应症/超说明书/限二线/禁忌症) 命中, Javert 无联网回退.",
        }
    return {
        "mode": "single",
        "drug_name": drug_name,
        "matched_generic": matched_generic,
        "found": True,
        "entries": list(entries),
    }


# ────────────────────────── format_for_agent ──────────────────────────

def format_for_agent(result: dict[str, Any]) -> str:
    if result.get("mode") == "single":
        return _format_single(result)
    return _format_bulk(result)


def _format_single(result: dict[str, Any]) -> str:
    if not result.get("found"):
        return f"药品「{result['drug_name']}」{result.get('note', '未命中知识库.')}"
    lines = [f"药品「{result['drug_name']}」知识库事实 (命中通用名: {result.get('matched_generic')}):"]
    for e in result["entries"]:
        lines.append(f"  [{e.get('rule_type', '?')}] 依据: {e.get('basis', '')}")
        if e.get("detect_logic"):
            lines.append(f"      检出逻辑: {e['detect_logic']}")
    return "\n".join(lines)


def _format_bulk(result: dict[str, Any]) -> str:
    pid = result["patient_id"]
    rt_filter = result.get("rule_type_filter") or ""
    filter_desc = f" (rule_type 过滤: {rt_filter})" if rt_filter else ""
    matches = result.get("matches", [])
    if not matches:
        return (
            f"患者 {pid} 的 {result.get('total_drug_fees', 0)} 种用药里, "
            f"无任何药品命中监管知识库{filter_desc} → 本规则不适用 (建议 CLEAN)."
        )
    # 必留头部: 汇总行 + 病案首页诊断 (ground truth). 命中药明细一多就会撑爆截断上限,
    # 把判"有无适应症"的唯一硬证据放头部必留段 (fix-drug-audit-precision D1), 截断只砍明细.
    lines = [
        f"患者 {pid} 用药 ∩ 药品监管知识库 命中 {len(matches)} 条{filter_desc} "
        f"(共扫 {result.get('total_drug_fees', 0)} 种用药):",
        "",
        "【患者病案首页诊断 (shi_zd ground truth — 判定指征请优先用此)】",
    ]
    diags = result.get("diagnoses", [])
    if diags:
        for d in diags:
            tag = "[主诊] " if d.get("is_main") else ""
            code = f" ({d['code']})" if d.get("code") else ""
            lines.append(f"  - {tag}{d['name']}{code}")
    else:
        lines.append("  (shi_zd 未收录该患者诊断, 请改用 note_diagnosis 工具取文书诊断)")
    lines.append(RETAIN_HEAD_MARKER)
    lines.append("")
    lines.append("【命中药品 (命中知识库 ≠ 违规, 须结合诊断判定)】")
    for i, m in enumerate(matches, 1):
        review_tag = " (名兜底, 需复核)" if m.get("needs_review") else ""
        lines.append(f"{i}. 通用名「{m['generic_name']}」 [{m['rule_type']}]{review_tag}")
        lines.append(f"   原始 fee 名: {' / '.join(m['fee_names'])}")
        lines.append(f"   限定/说明书依据: {m['basis']}")
        if m.get("detect_logic"):
            lines.append(f"   检出逻辑: {m['detect_logic']}")
    return "\n".join(lines)


# ────────────────────────── executor 工厂 ──────────────────────────

def create_executor(
    loader: DataLoader, kb_path: Path, zd_path: Path
) -> Callable[..., str]:
    """绑定 loader + KB 路径 + shi_zd 路径, 返回 drug_audit_lookup(...) 函数.

    模式判定: 传了非空 drug_name → single; 否则按 patient_id 走 bulk.
    (patient_id 由 ToolExecutor 在 LLM 漏传时自动注入).
    """

    def execute(
        patient_id: str | None = None,
        rule_type: str | None = None,
        drug_name: str | None = None,
        **_kwargs,
    ) -> str:
        if drug_name and str(drug_name).strip():
            return format_for_agent(lookup_single_drug(str(drug_name).strip(), kb_path))
        if not patient_id or not str(patient_id).strip():
            return "drug_audit_lookup 需要 patient_id (bulk) 或 drug_name (single)."
        rt = str(rule_type).strip() if rule_type else None
        result = lookup_patient_drugs(
            str(patient_id).strip(), loader, kb_path, zd_path, rule_type=rt
        )
        return format_for_agent(result)

    return execute
