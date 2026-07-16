# -*- coding: utf-8 -*-
"""reasoning 对外自然语言化 — 确定性替换内部术语 (behavior-naming).

对外出参 (2C results) 的 reasoning 必须全中文自然语言: 工具英文名 / 规则代号 /
英文判定词 / 内部机制词 全部映射成中文. 纯字符串替换, 不调 LLM, 不改库内原文
(工作台/追溯仍看原始 reasoning). 提示词侧 (base.txt) 同步减少术语产出, 本层兜底.
"""

from __future__ import annotations

import re

# 工具名 → 中文 (长名在前, 防子串误替换)
_TOOL_ZH: list[tuple[str, str]] = [
    ("scan_progress_indications", "病程记录排查"),
    ("search_lab_results", "检验报告检索"),
    ("search_examinations", "检查报告检索"),
    ("drug_audit_lookup", "药品医保限定核查"),
    ("drug_indication", "药品适应症核查"),
    ("note_diagnosis", "诊断记录核查"),
    ("search_anesthesia", "麻醉记录检索"),
    ("search_pathology", "病理报告检索"),
    ("search_notes", "病历文书检索"),
    ("search_fees", "费用明细检索"),
]

# 英文判定词 → 中文 (仅全大写形态, 避免误伤普通英文)
_VERDICT_ZH: list[tuple[str, str]] = [
    ("INCONCLUSIVE", "证据不足、需人工复核"),
    ("VIOLATION", "违规"),
    ("CLEAN", "合规"),
]

# 内部机制词 → 中文 (词边界匹配; ETL_GAP 先于 ETL)
_JARGON_ZH: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ETL_GAP[:：]?\s*"), "资料未数字化: "),
    (re.compile(r"\bETL\b"), "数据接入"),
    (re.compile(r"\betl_warning\b"), "资料未数字化提示"),
    (re.compile(r"\bJavert\b"), "审计系统"),
    (re.compile(r"\bverdict_gate\b|\bgate\b", re.IGNORECASE), "确定性复核"),
    (re.compile(r"\bprecheck\b", re.IGNORECASE), "初步核查"),
    (re.compile(r"预检"), "初步核查"),
    (re.compile(r"待专家裁定"), "待人工复核"),
    (re.compile(r"\bverdict\b"), "判定结果"),
    (re.compile(r"\bevidence\b"), "证据"),
    (re.compile(r"\bconf(?:idence)?\b"), "置信度"),
    (re.compile(r"\bcount\s*==?\s*(\d+)\b"), r"计数为\1次"),
]

# 规则代号 (R191 / RD20) → 本规则
_RULE_CODE = re.compile(r"\bRD?\d{2,3}\b")

# 内部注记整块剥离 (对外无可读性, 不予显示; 工作台/库内原文保留追溯):
#   [漂移防护(历史曾判V): 历史最新 (run=aud_xx) 判 ... (只升 I 不复活 V)]
#   [gate: <闸原因> | 原 conf=0.90]  /  [Gate] ... / [单次闸重筛回升(原C)] 等
_INTERNAL_BLOCK = re.compile(r"\n?\s*\[(?:漂移防护|[Gg]ate|单次|重筛|预检)[^\]]*\]")

# run id (aud_xxx) → 中文指代 (注记块外偶发引用)
_RUN_ID = re.compile(r"(?:\(\s*run=)?aud_[A-Za-z0-9_-]{12}\)?")

# 上下文单字母判定 (判V/曾判V/落I/升I/降C 等) → 中文; 只在判/落/升/降后替换,
# 不做全局单字母替换 (防误伤 维生素C/IV/CT 等)
_VIC_CONTEXT = re.compile(r"((?:曾|原|改|重)?[判落升降])\s*([VIC])(?![A-Za-z0-9])")
_VIC_ZH = {"V": "违规", "I": "证据不足", "C": "合规"}


def humanize_reasoning(text: str | None) -> str:
    """内部 reasoning → 对外全中文自然语言. 幂等, 空入空出."""
    s = text or ""
    if not s:
        return s
    s = _INTERNAL_BLOCK.sub("", s)
    s = _RUN_ID.sub("既往审计记录", s)
    for en, zh in _TOOL_ZH:
        s = s.replace(en, zh)
    for en, zh in _VERDICT_ZH:
        s = s.replace(en, zh)
    for pat, zh in _JARGON_ZH:
        s = pat.sub(zh, s)
    s = _RULE_CODE.sub("本规则", s)
    s = _VIC_CONTEXT.sub(lambda m: m.group(1) + _VIC_ZH[m.group(2)], s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()
