# -*- coding: utf-8 -*-
"""M8 药品适应症/限定审计模板: validate ready + 4 模式渲染分支."""

from __future__ import annotations

from pathlib import Path

import pytest

from javert.audit.rule_loader import load_rule
from javert.templating import load_template, render_template
from scripts.init_drug_rules import CURATED, CURATED_STATUS

M8_PATH = Path(__file__).resolve().parents[1] / "configs" / "templates" / "M8.yaml"
RULES_PATH = M8_PATH.parents[1] / "rules"


@pytest.fixture(scope="module")
def m8():
    return load_template(M8_PATH)


def _render(m8, drug_rule_type, **extra):
    vars_dict = {"drug_rule_type": drug_rule_type, "target_desc": "测试药 (类型级)"}
    vars_dict.update(extra)
    return render_template(m8, vars_dict)


def test_m8_validates_ready(m8):
    assert m8.template_id == "M8"
    assert m8.status == "ready"


def test_contraindication_renders_inverted_logic(m8):
    out = _render(m8, "禁忌症")["prompt_addon"]
    # 反向: 诊断命中禁忌 → VIOLATION
    assert "命中" in out and "禁忌" in out
    assert "反向" in out
    assert "诊断不在禁忌内 → CLEAN" in out


def test_second_line_renders_search_notes_step(m8):
    rendered = _render(m8, "限二线")
    out = rendered["prompt_addon"]
    assert "一线" in out and ("失败" in out or "不耐受" in out)
    assert "search_notes" in out
    # 限二线 suggested_tools 必含 search_notes
    assert "search_notes" in (rendered.get("suggested_tools") or [])


def test_limited_indication_renders_within_basis_logic(m8):
    out = _render(m8, "限适应症")["prompt_addon"]
    assert "落在" in out and "VIOLATION" in out
    # on-label 闸措辞
    assert "命中知识库" in out and "不代表违规" in out
    # shi_zd 优先
    assert "病案首页诊断" in out and "note_diagnosis" in out


def test_all_types_include_search_notes_for_selfpay_gate(m8):
    # med_rst: 自费门 (search_notes 查自费同意书) 对所有 M8 类型通用
    for rt in ("限适应症", "超说明书", "限二线", "禁忌症"):
        tools = _render(m8, rt).get("suggested_tools") or []
        assert "drug_audit_lookup" in tools and "note_diagnosis" in tools
        assert "search_notes" in tools


def test_curated_trigger_keywords_render(m8):
    rendered = _render(m8, "限适应症", drug_focus="人血白蛋白", trigger_kw=["人血白蛋白"])
    assert rendered.get("trigger_keywords") == ["人血白蛋白"]
    assert "人血白蛋白" in rendered["prompt_addon"]


def test_type_level_empty_trigger_keywords(m8):
    # 类型级: trigger_kw 留空 → trigger_keywords 为空 (router always-on)
    rendered = _render(m8, "禁忌症")
    assert (rendered.get("trigger_keywords") or []) == []


def test_curated_rules_remain_abandoned_after_render():
    """精选规则只供显式单跑，不能被生成器重新加入默认 router。"""
    assert CURATED_STATUS == "abandoned"
    for rule_id, *_ in CURATED:
        assert load_rule(RULES_PATH / f"{rule_id}.yaml").status == CURATED_STATUS
