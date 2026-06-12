# -*- coding: utf-8 -*-
"""Jinja2 渲染器: 把 Template + vars dict 渲成 RuleRenderResult.

输出形态:
    {
      "prompt_addon": "渲染后的 master_prompt",
      "trigger_keywords": [...] | None,  # 若模板有 keywords_template
      "suggested_tools": [...] | None,
      "expected_signal": "..." | None,
    }
"""

from __future__ import annotations

from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined, UndefinedError
from jinja2.exceptions import TemplateError

from .template_model import Template
from .vars_validator import validate_vars


class RenderError(ValueError):
    """Jinja2 渲染失败 (缺字段 / 语法错)."""


_ENV = Environment(
    undefined=StrictUndefined,
    trim_blocks=False,
    lstrip_blocks=False,
    keep_trailing_newline=True,
    autoescape=False,
)


def _render_str(source: str, vars_dict: dict[str, Any]) -> str:
    if not source:
        return ""
    try:
        tpl = _ENV.from_string(source)
        return tpl.render(**vars_dict)
    except UndefinedError as exc:
        raise RenderError(f"模板引用未声明变量: {exc.message}") from exc
    except TemplateError as exc:
        raise RenderError(f"Jinja2 渲染失败: {exc}") from exc


def _parse_list_output(rendered: str, source_label: str) -> list[str]:
    """把渲染产物当作 yaml list 字面量解析 (支持 - item 与 [a, b] 两种风格)."""
    if not rendered.strip():
        return []
    try:
        parsed = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        raise RenderError(
            f"{source_label} 渲染产物不是合法 yaml list: {exc}"
        ) from exc
    if parsed is None:
        return []
    if not isinstance(parsed, list):
        raise RenderError(
            f"{source_label} 渲染产物必须 yaml list, 实得 {type(parsed).__name__}"
        )
    if not all(isinstance(x, str) for x in parsed):
        raise RenderError(f"{source_label} 渲染产物 list 必须全 str")
    return parsed


def render_template(template: Template, vars_dict: dict[str, Any]) -> dict[str, Any]:
    """对 template 套 vars 完整渲染. 顺序: validate_vars → 渲染 4 个子模板.

    Returns:
        dict 含 prompt_addon (必有) + 三个可选 aux 字段
        (键名: trigger_keywords / suggested_tools / expected_signal)
    """
    resolved = validate_vars(template, vars_dict)
    out: dict[str, Any] = {
        "prompt_addon": _render_str(template.master_prompt, resolved),
    }
    if template.keywords_template.strip():
        rendered = _render_str(template.keywords_template, resolved)
        out["trigger_keywords"] = _parse_list_output(rendered, "keywords_template")
    if template.tools_template.strip():
        rendered = _render_str(template.tools_template, resolved)
        out["suggested_tools"] = _parse_list_output(rendered, "tools_template")
    if template.signal_template.strip():
        out["expected_signal"] = _render_str(template.signal_template, resolved)
    return out
