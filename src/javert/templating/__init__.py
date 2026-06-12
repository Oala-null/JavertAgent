# -*- coding: utf-8 -*-
"""rule-templating capability: 模板加载 + Jinja2 渲染 + LLM 起草.

公开 API (供 commands/ 调用):
    - load_template / load_all_templates / TemplateValidationError
    - Template / TemplateField
    - render_template, RenderError
    - validate_vars, VarsValidationError
"""

from __future__ import annotations

from .renderer import RenderError, render_template
from .template_loader import (
    TemplateValidationError,
    load_all_templates,
    load_template,
)
from .template_model import Template, TemplateField
from .vars_validator import VarsValidationError, validate_vars

__all__ = [
    "Template",
    "TemplateField",
    "TemplateValidationError",
    "load_template",
    "load_all_templates",
    "RenderError",
    "render_template",
    "VarsValidationError",
    "validate_vars",
]
