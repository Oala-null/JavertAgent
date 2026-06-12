# -*- coding: utf-8 -*-
"""vars dict 校验: 用 Template.fields 检查类型 / 枚举 / required / default."""

from __future__ import annotations

from typing import Any

from .template_model import Template, TemplateField


class VarsValidationError(ValueError):
    """vars dict 与 template.fields 不匹配 (缺 required / 类型错 / enum 非法)."""


def _type_check(field: TemplateField, value: Any) -> bool:
    if field.type == "str":
        return isinstance(value, str)
    if field.type == "list[str]":
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    if field.type == "enum":
        return isinstance(value, str) and (field.options is None or value in field.options)
    if field.type == "bool":
        return isinstance(value, bool)
    if field.type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    return False


def validate_vars(template: Template, vars_dict: dict[str, Any]) -> dict[str, Any]:
    """校验 vars; 用 default 兜底缺省; 返回应渲染时使用的最终 dict.

    Raises:
        VarsValidationError: required 缺失 / 类型不匹配 / enum 值不在 options
    """
    declared = {f.name: f for f in template.fields}
    out: dict[str, Any] = {}
    unknown = set(vars_dict.keys()) - set(declared.keys())
    if unknown:
        raise VarsValidationError(
            f"vars 含未声明字段 {sorted(unknown)}; 模板 {template.template_id} 仅接受 {sorted(declared.keys())}"
        )
    for name, field in declared.items():
        if name in vars_dict:
            value = vars_dict[name]
            if not _type_check(field, value):
                if field.type == "enum":
                    raise VarsValidationError(
                        f"field '{name}' 值 {value!r} 不在 enum options={field.options}"
                    )
                raise VarsValidationError(
                    f"field '{name}' 期望 {field.type}, 实际 {type(value).__name__}={value!r}"
                )
            out[name] = value
            continue
        # 字段缺省
        if field.default is not None:
            out[name] = field.default
            continue
        if field.required:
            raise VarsValidationError(
                f"field '{name}' 缺失且 required=true (desc: {field.desc or '(no desc)'})"
            )
        # required=false 且 无 default: 给类型零值
        out[name] = _zero_value(field)
    return out


def _zero_value(field: TemplateField) -> Any:
    if field.type == "str" or field.type == "enum":
        return ""
    if field.type == "list[str]":
        return []
    if field.type == "bool":
        return False
    if field.type == "int":
        return 0
    return None
