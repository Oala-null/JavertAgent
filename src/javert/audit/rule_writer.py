# -*- coding: utf-8 -*-
"""Rule yaml 写入器 — 用 ruamel.yaml 保字段顺序与注释 round-trip."""

from __future__ import annotations

import io
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .rule import Rule


_FIELD_ORDER = [
    "rule_id",
    "domain",
    "violation_type",
    "question",
    "example",
    "status",
    "priority",
    "prompt_addon",
    "trigger_keywords",
    "suggested_tools",
    "expected_signal",
    "notes",
    "derived_from_template",
    "drug_rule_type",
]


def _make_yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def write_rule(rule: Rule, path: Path) -> None:
    """新建或重写 rule yaml (清空既有注释). 想保留现有注释请用 update_status."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cm = CommentedMap()
    data = rule.model_dump()
    for key in _FIELD_ORDER:
        cm[key] = data.get(key)
    yaml = _make_yaml()
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cm, f)


def update_status(path: Path, new_status: str) -> None:
    """原地更新 status 字段, 保留其他字段的注释 / 顺序 / 内容."""
    if not path.exists():
        raise FileNotFoundError(path)
    yaml = _make_yaml()
    with open(path, encoding="utf-8") as f:
        data = yaml.load(f)
    if data is None:
        raise ValueError(f"yaml 内容为空: {path}")
    data["status"] = new_status
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def update_priority(path: Path, new_priority: str) -> str | None:
    """原地更新 priority 字段, 保留其他字段的注释 / 顺序 / 内容.

    若 yaml 缺 priority 字段, 自动插入到 status 字段之后.

    Returns:
        若字段已是 new_priority, 返回 None (no-op);
        否则返回旧值字符串 ('' 表示新插入).
    """
    if not path.exists():
        raise FileNotFoundError(path)
    yaml = _make_yaml()
    with open(path, encoding="utf-8") as f:
        data = yaml.load(f)
    if data is None:
        raise ValueError(f"yaml 内容为空: {path}")
    old = data.get("priority", "")
    if old == new_priority:
        return None
    if "priority" in data:
        data["priority"] = new_priority
    else:
        # 插入到 status 之后 (若没 status 则放最前)
        keys = list(data.keys())
        idx = keys.index("status") + 1 if "status" in keys else 0
        data.insert(idx, "priority", new_priority)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
    return str(old) if old else ""


def dump_rule_to_string(rule: Rule) -> str:
    """供测试用: 把 Rule 转 yaml 字符串."""
    cm = CommentedMap()
    data = rule.model_dump()
    for key in _FIELD_ORDER:
        cm[key] = data.get(key)
    yaml = _make_yaml()
    buf = io.StringIO()
    yaml.dump(cm, buf)
    return buf.getvalue()


def update_from_template_render(
    path: Path,
    *,
    prompt_addon: str,
    template_id: str,
    trigger_keywords: list[str] | None = None,
    suggested_tools: list[str] | None = None,
    expected_signal: str | None = None,
) -> None:
    """把 prompt_fit 渲染结果原地写回 rule yaml, 保留其他字段顺序与注释.

    更新 prompt_addon (必) + derived_from_template (必, 标 template_id) +
    三个 aux 字段 (各自传 None 表示不动).

    自动用 ruamel.yaml 的 LiteralScalarString 给 prompt_addon / expected_signal
    带 `|` 多行块, 让 yaml 形态与人工填写一致.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    yaml = _make_yaml()
    with open(path, encoding="utf-8") as f:
        data = yaml.load(f)
    if data is None:
        raise ValueError(f"yaml 内容为空: {path}")

    from ruamel.yaml.scalarstring import LiteralScalarString

    def _block(s: str) -> "LiteralScalarString | str":
        if not isinstance(s, str):
            return s
        if "\n" in s:
            tail = s if s.endswith("\n") else s + "\n"
            return LiteralScalarString(tail)
        return s

    data["prompt_addon"] = _block(prompt_addon)
    if trigger_keywords is not None:
        data["trigger_keywords"] = list(trigger_keywords)
    if suggested_tools is not None:
        data["suggested_tools"] = list(suggested_tools)
    if expected_signal is not None:
        data["expected_signal"] = _block(expected_signal)

    if "derived_from_template" in data:
        data["derived_from_template"] = template_id
    else:
        # 插入到 notes 之后 (或末尾)
        keys = list(data.keys())
        if "notes" in keys:
            idx = keys.index("notes") + 1
            data.insert(idx, "derived_from_template", template_id)
        else:
            data["derived_from_template"] = template_id

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
