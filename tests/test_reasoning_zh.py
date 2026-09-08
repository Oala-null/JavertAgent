# -*- coding: utf-8 -*-
"""reasoning 对外自然语言化 + 行为认定名称映射 测试 (behavior-naming)."""

from __future__ import annotations

from javert.web.reasoning_zh import humanize_reasoning


def test_tool_names_replaced():
    s = humanize_reasoning("search_fees 显示收费 2 次, search_lab_results 无记录")
    assert "search_fees" not in s and "search_lab_results" not in s
    assert "费用明细检索" in s and "检验报告检索" in s


def test_new_clinical_tool_names_replaced():
    s = humanize_reasoning("search_orders 与 catalog_lookup 提供证据")
    assert "search_orders" not in s and "catalog_lookup" not in s
    assert "医嘱检索" in s and "诊疗目录查询" in s


def test_verdicts_and_rule_codes_replaced():
    s = humanize_reasoning("按 R191 规则应判 VIOLATION, RD20 与 CD01 为 CLEAN, 否则 INCONCLUSIVE")
    assert "R191" not in s and "RD20" not in s and "CD01" not in s
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


def test_internal_annotation_blocks_stripped():
    """漂移防护/gate 注记整块不予显示 (对外无可读性)."""
    s = humanize_reasoning(
        "费用明细存在重复收取。\n"
        "[漂移防护(历史曾判V): 历史最新 (run=aud_GeAaQbOU3IqF) 判 VIOLATION, "
        "本次重跑判 CLEAN, 落 INCONCLUSIVE 待专家裁定 (只升 I 不复活 V)]"
    )
    assert s == "费用明细存在重复收取。"
    s2 = humanize_reasoning("推理正文。\n[gate: 缺文书 | 原 conf=0.90]")
    assert s2 == "推理正文。"


def test_contextual_vic_letters_and_run_ids():
    s = humanize_reasoning("历史曾判V, 本次改判C, 落I; 参见 aud_GeAaQbOU3IqF")
    assert "判违规" in s and "判合规" in s and "落证据不足" in s
    assert "aud_" not in s
    # 不误伤医学缩写里的单字母
    keep = humanize_reasoning("维生素C 与 CT 检查无异常")
    assert "维生素C" in keep and "CT" in keep


def test_cjk_adjacent_and_leaked_terms():
    """中英相邻 (\\b 失效场景) + 62 实测漏网: sy_ 文件名/专家代号/版本号/替换残渣."""
    s = humanize_reasoning(
        "影像报告单（sy_patient_examination）缺失（ETL未数字化）, "
        "根据规则 R155 的 v1.5 硬规则及专家共识（wangxin 标准）, 按R191规则处理"
    )
    assert "sy_patient_examination" not in s and "检查报告库" in s
    assert "ETL" not in s and "数据接入" in s
    assert "wangxin" not in s and "专家" in s
    assert "v1.5" not in s
    assert "R155" not in s and "R191" not in s
    assert "规则 本规则" not in s  # 残渣 "根据规则 R155" → "根据本规则"


def test_precheck_wording_humanized():
    s = humanize_reasoning("预检: 未见 A 类 (主项) 费用命中, 规则不适用 → CLEAN")
    assert "预检" not in s
    assert s.startswith("初步核查") and "合规" in s


def test_behavior_name_mapping():
    from javert.web.rule_meta import behavior_name, reset_cache

    reset_cache()
    assert behavior_name("重复收费") == "重复收费"
    assert behavior_name("分解收费") == "分解项目收费"
    assert behavior_name("超医保限定支付适应症用药") == "超范围支付"
    assert behavior_name("串换项目") == "串换药品、医用耗材、诊疗项目和服务设施"
    assert behavior_name("没登记的细类") == "未分类"  # 公开类别禁止回退内部类型
    assert behavior_name("") == "未分类"
