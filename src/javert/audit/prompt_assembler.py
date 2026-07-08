# -*- coding: utf-8 -*-
"""prompt 拼装 — base + 医院科室配置 + rule.prompt_addon + question + example + trigger_keywords."""

from __future__ import annotations

from pathlib import Path

import yaml

from .rule import Rule


def load_base_prompt(prompts_dir: Path) -> str:
    base_path = prompts_dir / "base.txt"
    if not base_path.exists():
        raise FileNotFoundError(f"base prompt 不存在: {base_path}")
    return base_path.read_text(encoding="utf-8")


def load_experience_doc(experience_path: Path) -> str | None:
    """v0.7+ — 加载专家共识知识库 (configs/experience.md).

    若文件不存在 → 返回 None, LLM 仍按 base prompt 走 (向后兼容).
    """
    if not experience_path.exists():
        return None
    try:
        return experience_path.read_text(encoding="utf-8")
    except Exception:
        return None


def load_hospital_config(hospital_config_path: Path) -> dict | None:
    """读取医院科室配置. 文件不存在或解析失败 → 返回 None (LLM 走旧行为)."""
    if not hospital_config_path.exists():
        return None
    try:
        with open(hospital_config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def format_hospital_config_block(config: dict) -> str:
    """把医院科室配置序列化成 LLM 易读的 markdown 段."""
    hospital_id = config.get("hospital_id", "(未知)")
    hospital_name = config.get("hospital_name", "(未填)")
    departments = config.get("departments") or {}

    lines = [
        "# 医院科室配置 (供 PACU / 精神监护 / 透析等规则判定)",
        "",
        f"**医院**: {hospital_name} (id={hospital_id})",
        "",
        "**科室存在性** (true=有 / false=无):",
        "",
    ]
    if not departments:
        lines.append("(无科室配置, 涉及科室的规则应输出 INCONCLUSIVE 建议线下核实)")
    else:
        for name, present in departments.items():
            mark = "✅" if present else "❌"
            lines.append(f"  - {mark} {name}: {bool(present)}")
    lines.append("")
    lines.append(
        "**使用方式**: 若规则需判断医院是否有某科室 (例如 R212 PACU / R291 封闭式精神病专科病区), "
        "查上表 — 若该科室明确为 false 且 fee 收了对应费用 → VIOLATION (硬证据); "
        "若该科室 true → CLEAN (有科室开展, 不构成本规则违规); "
        "若该科室未列出 → INCONCLUSIVE conf 0.5 建议线下核实."
    )
    return "\n".join(lines)


def assemble_system_prompt(
    rule: Rule,
    base_prompt: str,
    tools_prompt: str,
    hospital_config: dict | None = None,
    experience_doc: str | None = None,
) -> str:
    """组装 system prompt: base + experience.md + (可选)医院科室配置 + 工具列表 + 规则信息.

    v0.7+: experience_doc (configs/experience.md) 注入全局专家共识 +
    QKV trigger 机制提示, 让 LLM 跟专家裁决靠拢.
    """
    sections: list[str] = [base_prompt.strip(), ""]

    if experience_doc is not None:
        sections.append("=" * 20)
        sections.append(experience_doc.strip())
        sections.append("")

    if hospital_config is not None:
        sections.append("=" * 20)
        sections.append(format_hospital_config_block(hospital_config))
        sections.append("")

    # boost-llm-efficiency: 静态工具段前置 — 公共前缀 = base+experience+hospital+tools,
    # 规则个性化段之后才分叉, sglang prefix cache 跨规则命中 (段内容逐字不动, 只挪位置)
    sections.append("=" * 20)
    sections.append("# 可用工具")
    sections.append("")
    sections.append(tools_prompt)
    sections.append("")

    sections.append("=" * 20)
    sections.append(f"# 当前审计规则: {rule.rule_id}")
    sections.append("")
    sections.append(f"**所属领域**: {rule.domain}")
    sections.append(f"**违规类型**: {rule.violation_type}")
    sections.append("")
    sections.append("## 规则原文 (问题描述)")
    sections.append(rule.question)
    if rule.example:
        sections.append("")
        sections.append("## 违规示例 (清单参考)")
        sections.append(rule.example)
    if rule.prompt_addon:
        sections.append("")
        sections.append("## 规则特定指引")
        sections.append(rule.prompt_addon)
    if rule.trigger_keywords:
        sections.append("")
        sections.append("## 建议关注的关键词")
        sections.append(", ".join(rule.trigger_keywords))
    if rule.suggested_tools:
        sections.append("")
        sections.append("## 建议优先调用工具")
        sections.append(", ".join(rule.suggested_tools))
    if rule.expected_signal:
        sections.append("")
        sections.append("## 预期信号 (操作者备注, 仅供参考, 不替代证据)")
        sections.append(rule.expected_signal)

    return "\n".join(sections)


def initial_user_message(
    rule: Rule, patient_id: str, precheck_facts: str | None = None
) -> str:
    if precheck_facts:
        # pilot-deterministic-precheck: 费用事实已确定性给定, LLM 只答窄问题 (核实反证).
        return (
            f"请审计患者 {patient_id} 是否触发规则 {rule.rule_id}.\n\n"
            f"{precheck_facts}\n\n"
            f"按上述指引调用 search_notes 核实后, 输出 fenced JSON 裁决."
        )
    return (
        f"请审计患者 {patient_id} 是否触发规则 {rule.rule_id}. "
        f"先思考你需要哪些证据, 然后用 <tool_call> 调用工具, "
        f"拿到足够证据后输出 fenced JSON 裁决."
    )
