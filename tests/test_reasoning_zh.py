# -*- coding: utf-8 -*-
"""reasoning 对外自然语言化 + 行为认定名称映射 测试 (behavior-naming)."""

from __future__ import annotations

from javert.web.reasoning_zh import humanize_reasoning


def test_tool_names_replaced():
    s = humanize_reasoning("search_fees 显示收费 2 次, search_lab_results 无记录")
    assert "search_fees" not in s and "search_lab_results" not in s
    assert "费用明细检索" in s and "检验报告检索" in s


def test_verdicts_and_rule_codes_replaced():
    s = humanize_reasoning("按 R191 规则应判 VIOLATION, RD20 为 CLEAN, 否则 INCONCLUSIVE")
    assert "R191" not in s and "RD20" not in s
    assert "VIOLATION" not in s and "CLEAN" not in s and "INCONCLUSIVE" not in s
    assert "违规" in s and "合规" in s and "证据不足" in s


def test_jargon_replaced():
    s = humanize_reasoning("gate count==1 放过; ETL_GAP: 麻醉记录; conf 0.5")
    assert "gate" not in s and "count==1" not in s and "ETL_GAP" not in s
    assert "计数为1次" in s and "资料未数字化" in s and "置信度" in s


def test_plain_chinese_untouched_and_idempotent():
    plain = "费用明细中出现 ¥300 麻醉后复苏监护, 病历文书无相应执行记录, 证据不足。"
    assert humanize_reasoning(plain) == plain
    once = humanize_reasoning("search_fees → VIOLATION")
    assert humanize_reasoning(once) == once
    assert humanize_reasoning("") == ""
    assert humanize_reasoning(None) == ""


def test_behavior_name_mapping():
    from javert.web.rule_meta import behavior_name, reset_cache

    reset_cache()
    assert behavior_name("重复收费") == "重复收费"
    assert behavior_name("分解收费") == "分解项目收费"
    assert behavior_name("超医保限定支付适应症用药") == "超范围支付"
    assert behavior_name("串换项目") == "串换药品、医用耗材、诊疗项目和服务设施"
    assert behavior_name("没登记的细类") == "没登记的细类"  # 回退原词
    assert behavior_name("") == "未分类"
