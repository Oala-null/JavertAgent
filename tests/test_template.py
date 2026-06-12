# -*- coding: utf-8 -*-
"""Phase 1 单测: template_model / template_loader / vars_validator / renderer."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from javert.templating import (
    RenderError,
    Template,
    TemplateField,
    TemplateValidationError,
    VarsValidationError,
    load_all_templates,
    load_template,
    render_template,
    validate_vars,
)
from javert.templating.template_model import Template as _T


# ---- TemplateField 校验 ----

def test_template_field_enum_requires_options() -> None:
    with pytest.raises(ValueError, match="enum requires options"):
        TemplateField(name="f", type="enum", desc="...", options=None)


def test_template_field_enum_default_must_be_in_options() -> None:
    with pytest.raises(ValueError, match="不在 options"):
        TemplateField(name="f", type="enum", options=["a", "b"], default="c")


def test_template_field_non_enum_no_options() -> None:
    with pytest.raises(ValueError, match="不可携带 options"):
        TemplateField(name="f", type="str", options=["a"])


def test_template_field_default_type_check() -> None:
    with pytest.raises(ValueError, match="不兼容"):
        TemplateField(name="f", type="int", default="not-an-int")


def test_template_field_list_default_ok() -> None:
    f = TemplateField(name="kws", type="list[str]", default=["a", "b"])
    assert f.default == ["a", "b"]


# ---- Template 校验 ----

def test_template_duplicate_field_names_rejected() -> None:
    with pytest.raises(ValueError, match="字段名重复"):
        _T(
            template_id="M9",
            name="dup",
            master_prompt="x",
            fields=[
                TemplateField(name="a", type="str"),
                TemplateField(name="a", type="int"),
            ],
        )


def test_template_status_empty_and_ready() -> None:
    empty = _T(template_id="M9", name="e")
    assert empty.status == "empty"
    assert empty.is_empty is True
    ready = _T(
        template_id="M9", name="e", master_prompt="x",
        fields=[TemplateField(name="a", type="str")],
    )
    assert ready.status == "ready"
    partial = _T(template_id="M9", name="e", master_prompt="x")  # 缺 fields
    assert partial.status == "partial"


# ---- template_loader ----

def test_load_template_missing_id(tmp_path: Path) -> None:
    p = tmp_path / "Mbad.yaml"
    p.write_text("name: 无 id\n", encoding="utf-8")
    with pytest.raises(TemplateValidationError, match="template_id"):
        load_template(p)


def test_load_template_enum_missing_options(tmp_path: Path) -> None:
    p = tmp_path / "M7.yaml"
    p.write_text(
        "template_id: M7\nname: bad\nmaster_prompt: x\nfields:\n  - name: f\n    type: enum\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateValidationError, match="enum"):
        load_template(p)


def test_load_template_placeholder(tmp_path: Path) -> None:
    p = tmp_path / "M9.yaml"
    p.write_text(
        "template_id: M9\nname: 占位\ndescription: 占位描述\nmaster_prompt: ''\nfields: []\n",
        encoding="utf-8",
    )
    tpl = load_template(p)
    assert tpl.template_id == "M9"
    assert tpl.is_empty is True
    assert tpl.status == "empty"


def test_load_all_templates_includes_six_placeholders() -> None:
    project_root = Path(__file__).resolve().parents[1]
    tmpls = load_all_templates(project_root / "configs" / "templates")
    assert {"M1", "M2", "M3", "M4", "M5", "M6"}.issubset(tmpls.keys())


def test_load_all_templates_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(TemplateValidationError, match="不存在"):
        load_all_templates(tmp_path / "nope")


# ---- vars_validator ----

def _t_with_fields(fields: list[TemplateField]) -> Template:
    return _T(template_id="M9", name="t", master_prompt="x", fields=fields)


def test_validate_vars_required_missing() -> None:
    tpl = _t_with_fields([TemplateField(name="a", type="str", required=True)])
    with pytest.raises(VarsValidationError, match="required"):
        validate_vars(tpl, {})


def test_validate_vars_default_fills_in() -> None:
    tpl = _t_with_fields([TemplateField(name="a", type="str", required=False, default="d")])
    assert validate_vars(tpl, {}) == {"a": "d"}


def test_validate_vars_type_mismatch() -> None:
    tpl = _t_with_fields([TemplateField(name="a", type="int", required=True)])
    with pytest.raises(VarsValidationError, match="期望 int"):
        validate_vars(tpl, {"a": "string"})


def test_validate_vars_enum_invalid_value() -> None:
    tpl = _t_with_fields([TemplateField(name="a", type="enum", options=["x", "y"], required=True)])
    with pytest.raises(VarsValidationError, match="enum"):
        validate_vars(tpl, {"a": "z"})


def test_validate_vars_unknown_key_rejected() -> None:
    tpl = _t_with_fields([TemplateField(name="a", type="str", required=False, default="")])
    with pytest.raises(VarsValidationError, match="未声明字段"):
        validate_vars(tpl, {"unknown": "x"})


def test_validate_vars_optional_zero_value() -> None:
    tpl = _t_with_fields([TemplateField(name="a", type="list[str]", required=False)])
    assert validate_vars(tpl, {}) == {"a": []}


# ---- renderer ----

def test_render_master_prompt_substitution() -> None:
    tpl = _T(
        template_id="M9",
        name="t",
        master_prompt="hello {{name}}",
        fields=[TemplateField(name="name", type="str", required=True)],
    )
    out = render_template(tpl, {"name": "world"})
    assert out["prompt_addon"] == "hello world"


def test_render_missing_required_raises_vars_error() -> None:
    tpl = _T(
        template_id="M9",
        name="t",
        master_prompt="hi {{x}}",
        fields=[TemplateField(name="x", type="str", required=True)],
    )
    # 校验阶段先抛 VarsValidationError, 不进 Jinja
    with pytest.raises(VarsValidationError):
        render_template(tpl, {})


def test_render_unknown_variable_in_template_raises_render_error() -> None:
    # 模板里引用一个未在 fields 声明的变量 → vars_validator 不会拦, jinja 引擎 StrictUndefined 会
    tpl = _T(
        template_id="M9",
        name="t",
        master_prompt="hello {{ghost}}",
        fields=[],
    )
    with pytest.raises(RenderError, match="未声明变量|ghost"):
        render_template(tpl, {})


def test_render_conditional_block() -> None:
    tpl = _T(
        template_id="M9",
        name="t",
        master_prompt="{% if flag %}YES{% else %}NO{% endif %}",
        fields=[TemplateField(name="flag", type="bool", required=True)],
    )
    assert render_template(tpl, {"flag": True})["prompt_addon"] == "YES"
    assert render_template(tpl, {"flag": False})["prompt_addon"] == "NO"


def test_render_list_iteration() -> None:
    tpl = _T(
        template_id="M9",
        name="t",
        master_prompt="{% for k in kws %}- {{k}}\n{% endfor %}",
        fields=[TemplateField(name="kws", type="list[str]", required=True)],
    )
    out = render_template(tpl, {"kws": ["a", "b"]})
    assert out["prompt_addon"] == "- a\n- b\n"


def test_render_keywords_template_yaml_list() -> None:
    tpl = _T(
        template_id="M9",
        name="t",
        master_prompt="ok",
        keywords_template="{% for k in kws %}- {{k}}\n{% endfor %}",
        fields=[TemplateField(name="kws", type="list[str]", required=True)],
    )
    out = render_template(tpl, {"kws": ["alpha", "beta"]})
    assert out["trigger_keywords"] == ["alpha", "beta"]


def test_render_signal_template_string() -> None:
    tpl = _T(
        template_id="M9",
        name="t",
        master_prompt="ok",
        signal_template="信号: {{ s }}",
        fields=[TemplateField(name="s", type="str", required=True)],
    )
    out = render_template(tpl, {"s": "x"})
    assert out["expected_signal"] == "信号: x"


def test_render_tools_template_invalid_yaml_list_raises() -> None:
    tpl = _T(
        template_id="M9",
        name="t",
        master_prompt="ok",
        tools_template="not a list",
        fields=[],
    )
    with pytest.raises(RenderError, match="tools_template"):
        render_template(tpl, {})
