# -*- coding: utf-8 -*-
"""Template yaml 加载器."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from .template_model import Template

logger = logging.getLogger("javert.templating.loader")


class TemplateValidationError(ValueError):
    """加载 template yaml 失败 (字段缺失 / 类型错误 / enum 缺 options)."""


def load_template(path: Path) -> Template:
    """加载单个 template yaml. 错误抛 TemplateValidationError."""
    if not path.exists():
        raise TemplateValidationError(f"template 文件不存在: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise TemplateValidationError(f"yaml 解析失败 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateValidationError(f"yaml 顶层必须是 mapping: {path}")
    for required in ("template_id", "name"):
        if required not in data:
            raise TemplateValidationError(
                f"{path.name} 缺失必需字段 '{required}'"
            )
    try:
        return Template(**data)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else None
        loc = ".".join(str(x) for x in (first.get("loc") if first else ())) or "?"
        msg = first.get("msg") if first else str(exc)
        raise TemplateValidationError(
            f"{path.name} 字段 '{loc}' 校验失败: {msg}"
        ) from exc


def load_all_templates(templates_dir: Path) -> dict[str, Template]:
    """加载目录下所有 M*.yaml. 返回 {template_id: Template} dict.

    任一 yaml 解析失败抛 TemplateValidationError 终止.
    """
    if not templates_dir.exists():
        raise TemplateValidationError(f"templates 目录不存在: {templates_dir}")
    out: dict[str, Template] = {}
    for yaml_path in sorted(templates_dir.glob("M*.yaml")):
        tpl = load_template(yaml_path)
        if tpl.template_id in out:
            raise TemplateValidationError(
                f"重复 template_id: {tpl.template_id} (in {yaml_path})"
            )
        out[tpl.template_id] = tpl
    logger.info("加载 %d 个模板 from %s", len(out), templates_dir)
    return out
