# -*- coding: utf-8 -*-
"""hospital_config 加载 + prompt_assembler 注入 单测.

v0.3 add-hospital-config: 让 R212 (PACU) / R291 (精神病专科病区) 等规则能据科室配置硬判.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from javert.audit.prompt_assembler import (
    assemble_system_prompt,
    format_hospital_config_block,
    load_hospital_config,
)
from javert.audit.rule import Rule


def _make_rule(rule_id: str = "R212") -> Rule:
    return Rule(
        rule_id=rule_id,
        domain="麻醉",
        violation_type="超标准收费",
        question="医院无麻醉恢复室仍收 PACU 监护费.",
        example="",
        status="ready",
        priority="P0",
        prompt_addon="审计步骤: 1. fee 查 PACU; 2. 看医院科室配置.",
    )


# ---------- load_hospital_config ----------


def test_load_hospital_config_real_file_demo(tmp_path: Path):
    """正常 yaml 文件 → 解析为 dict."""
    cfg_path = tmp_path / "hospital.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "hospital_id": "demo",
                "hospital_name": "测试医院",
                "departments": {"PACU": True, "CCU": False},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    cfg = load_hospital_config(cfg_path)
    assert cfg is not None
    assert cfg["hospital_id"] == "demo"
    assert cfg["departments"]["PACU"] is True
    assert cfg["departments"]["CCU"] is False


def test_load_hospital_config_missing_file_returns_none(tmp_path: Path):
    """文件不存在 → 返回 None, runner 走旧行为."""
    cfg = load_hospital_config(tmp_path / "does_not_exist.yaml")
    assert cfg is None


def test_load_hospital_config_malformed_yaml_returns_none(tmp_path: Path):
    """损坏 yaml → 返回 None, 不抛."""
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("hospital_id: demo\n  bad indent:\nfoo: : :", encoding="utf-8")
    cfg = load_hospital_config(cfg_path)
    assert cfg is None


def test_load_hospital_config_non_dict_top_level_returns_none(tmp_path: Path):
    """yaml 顶层不是 dict → 返回 None."""
    cfg_path = tmp_path / "list.yaml"
    cfg_path.write_text("- a\n- b\n", encoding="utf-8")
    cfg = load_hospital_config(cfg_path)
    assert cfg is None


# ---------- format_hospital_config_block ----------


def test_format_hospital_config_block_renders_true_false():
    config = {
        "hospital_id": "demo",
        "hospital_name": "测试医院",
        "departments": {"PACU": True, "CCU": False},
    }
    block = format_hospital_config_block(config)
    assert "医院科室配置" in block
    assert "测试医院" in block
    assert "✅ PACU" in block
    assert "❌ CCU" in block
    assert "VIOLATION" in block  # 教学段必含


def test_format_hospital_config_block_empty_departments():
    config = {"hospital_id": "x", "hospital_name": "x", "departments": {}}
    block = format_hospital_config_block(config)
    assert "无科室配置" in block
    assert "INCONCLUSIVE" in block


def test_format_hospital_config_block_missing_fields_defaults():
    """缺 hospital_id / hospital_name → 用占位符不抛."""
    block = format_hospital_config_block({"departments": {"PACU": True}})
    assert "(未知)" in block
    assert "(未填)" in block
    assert "✅ PACU" in block


# ---------- assemble_system_prompt 注入 ----------


def test_assemble_with_hospital_config_includes_block():
    rule = _make_rule("R212")
    system = assemble_system_prompt(
        rule,
        base_prompt="基础 prompt",
        tools_prompt="(tools)",
        hospital_config={
            "hospital_id": "demo",
            "hospital_name": "测试医院",
            "departments": {"PACU": True},
        },
    )
    assert "医院科室配置" in system
    assert "✅ PACU" in system
    # 规则段仍在
    assert "R212" in system
    # 工具段仍在
    assert "可用工具" in system


def test_assemble_without_hospital_config_omits_block():
    """默认 hospital_config=None 时不注入, 向后兼容."""
    rule = _make_rule("R212")
    system = assemble_system_prompt(rule, base_prompt="基础", tools_prompt="(tools)")
    # 用 block header 精确匹配, 避免与 prompt_addon 里的字面词冲突
    assert "# 医院科室配置 (供" not in system
    assert "R212" in system


def test_assemble_hospital_block_before_rule_block():
    """医院配置应在规则信息前面."""
    rule = _make_rule("R212")
    system = assemble_system_prompt(
        rule,
        base_prompt="基础",
        tools_prompt="(tools)",
        hospital_config={"hospital_id": "x", "hospital_name": "x", "departments": {"PACU": True}},
    )
    pos_hosp = system.find("医院科室配置")
    pos_rule = system.find("# 当前审计规则:")
    assert pos_hosp >= 0 and pos_rule >= 0
    assert pos_hosp < pos_rule


# ---------- real configs/hospital_config.yaml 集成 ----------


def test_real_hospital_config_yaml_loads_and_has_pacu():
    """实际 configs/hospital_config.yaml 应可加载且 PACU 字段就位."""
    project_root = Path(__file__).resolve().parent.parent
    cfg = load_hospital_config(project_root / "configs" / "hospital_config.yaml")
    assert cfg is not None
    deps = cfg.get("departments", {})
    assert "PACU" in deps
    assert "封闭式精神病专科病区" in deps  # R291 关键字段
