# -*- coding: utf-8 -*-
"""boost-llm-efficiency: 静态段前置布局单测.

段序: base → experience → hospital → tools → 规则个性化段.
验证: (1) 任意两规则 assembled prompt 公共前缀覆盖到 tools 段末尾 (sglang prefix cache 命中面);
      (2) 段集合与各段文本逐字保留, 仅顺序变化 (挪位不改内容).
"""

from __future__ import annotations

import os

from javert.audit.prompt_assembler import (
    assemble_system_prompt,
    format_hospital_config_block,
)
from javert.audit.rule import Rule

_BASE = "基础 prompt 正文"
_TOOLS = "- **search_fees**: 检索患者全量费用\n- **search_notes**: 检索文书"
_EXPERIENCE = "# Javert 审计经验库\n共识条目若干"
_HOSPITAL = {"hospital_id": "demo", "hospital_name": "测试医院", "departments": {"PACU": True}}


def _make_rule(rule_id: str, question: str) -> Rule:
    return Rule(
        rule_id=rule_id,
        domain="测试域",
        violation_type="重复收费",
        question=question,
        example=f"{rule_id} 的违规示例",
        status="ready",
        priority="P0",
        prompt_addon=f"{rule_id} 的规则特定指引",
        trigger_keywords=[f"{rule_id}关键词"],
    )


def _assemble(rule: Rule) -> str:
    return assemble_system_prompt(
        rule,
        base_prompt=_BASE,
        tools_prompt=_TOOLS,
        hospital_config=_HOSPITAL,
        experience_doc=_EXPERIENCE,
    )


def test_two_rules_common_prefix_covers_tools_section():
    """两条不同规则的最长公共前缀 ≥ 从开头到 tools 段末尾."""
    s1 = _assemble(_make_rule("R191", "问题 A: 重复收费情形一"))
    s2 = _assemble(_make_rule("R151", "问题 B: 过度检查情形二"))
    tools_end = s1.find(_TOOLS) + len(_TOOLS)
    assert tools_end > len(_TOOLS)  # tools 段确实在
    common = os.path.commonprefix([s1, s2])
    assert len(common) >= tools_end


def test_section_order_static_before_rule():
    """段序: base < experience < hospital < tools < 规则段."""
    s = _assemble(_make_rule("R191", "问题 A"))
    positions = [
        s.find(_BASE),
        s.find("Javert 审计经验库"),
        s.find("# 医院科室配置"),
        s.find("# 可用工具"),
        s.find("# 当前审计规则: R191"),
    ]
    assert all(p >= 0 for p in positions)
    assert positions == sorted(positions)


def test_sections_text_verbatim_preserved():
    """挪位不改内容: 各段文本逐字仍在 (含规则个性化各子段)."""
    rule = _make_rule("R191", "问题 A: 重复收费情形一")
    s = _assemble(rule)
    for fragment in (
        _BASE,
        _EXPERIENCE.strip(),
        format_hospital_config_block(_HOSPITAL),
        f"# 可用工具\n\n{_TOOLS}",
        "## 规则原文 (问题描述)\n问题 A: 重复收费情形一",
        "## 违规示例 (清单参考)\nR191 的违规示例",
        "## 规则特定指引\nR191 的规则特定指引",
        "## 建议关注的关键词\nR191关键词",
    ):
        assert fragment in s, f"段文本丢失: {fragment[:30]}..."
    # 工具段只出现一次 (没有旧位置残留)
    assert s.count("# 可用工具") == 1
