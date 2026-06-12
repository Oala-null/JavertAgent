# -*- coding: utf-8 -*-
"""Rule yaml 加载器."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from .rule import Rule, RuleValidationError

logger = logging.getLogger("javert.audit.rule_loader")


def load_rule(path: Path) -> Rule:
    """加载单个 yaml. 字段缺失或类型错误抛 RuleValidationError."""
    if not path.exists():
        raise RuleValidationError(f"rule 文件不存在: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise RuleValidationError(f"yaml 解析失败 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuleValidationError(f"yaml 顶层必须是 mapping: {path}")
    try:
        return Rule(**data)
    except ValidationError as exc:
        # 提取首条错误说清楚是哪个字段
        first = exc.errors()[0] if exc.errors() else None
        loc = ".".join(str(x) for x in (first.get("loc") if first else ())) or "?"
        msg = first.get("msg") if first else str(exc)
        raise RuleValidationError(f"{path.name} 字段 '{loc}' 校验失败: {msg}") from exc


def load_all(rules_dir: Path) -> dict[str, Rule]:
    """加载目录下所有 *.yaml. 返回 {rule_id: Rule} dict.

    缺字段或非法值时, 抛 RuleValidationError 并指明文件名 (early-fail).
    """
    if not rules_dir.exists():
        raise RuleValidationError(f"rules 目录不存在: {rules_dir}")
    out: dict[str, Rule] = {}
    for yaml_path in sorted(rules_dir.glob("R*.yaml")):
        rule = load_rule(yaml_path)
        if rule.rule_id in out:
            raise RuleValidationError(f"重复 rule_id: {rule.rule_id} (in {yaml_path})")
        out[rule.rule_id] = rule
    logger.info("加载 %d 条规则 from %s", len(out), rules_dir)
    return out
