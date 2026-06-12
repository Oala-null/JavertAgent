# -*- coding: utf-8 -*-
"""Template + TemplateField pydantic 模型.

模板 yaml schema (configs/templates/Mx.yaml):
    template_id: M1
    name: 重复收费
    description: 主项费用 + 附属费用并存 + 文书无反证
    master_prompt: |
      ...Jinja2 字符串 ({{var}} / {% if %} / {% for %})...
    keywords_template: |        # 可选, 渲染 trigger_keywords (yaml list 字面量)
    tools_template: |           # 可选, 渲染 suggested_tools
    signal_template: |          # 可选, 渲染 expected_signal
    fields:
      - name: a_type_display
        type: str
        desc: A 类主项显示名 (例: 肿瘤全身断层显像)
        required: true
      - name: fee_category
        type: enum
        options: [手术类, 药品类, 耗材类, 检查类, 其他类]
        desc: search_fees(category=...) 的 category 参数
        required: true
        default: 检查类
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

FieldType = Literal["str", "list[str]", "enum", "bool", "int"]


class TemplateField(BaseModel):
    """模板单字段声明."""

    name: str = Field(..., min_length=1, description="字段名 (vars dict key)")
    type: FieldType = Field(..., description="类型枚举")
    desc: str = Field(default="", description="人类可读说明 (interactive 模式作 prompt)")
    required: bool = Field(default=True, description="是否必填; required=false + 缺省时取 default 或空")
    default: Any | None = Field(default=None, description="缺省值 (类型必须与 type 兼容)")
    options: list[str] | None = Field(
        default=None, description="enum 类型的合法枚举 (其他类型必须 None)"
    )

    @model_validator(mode="after")
    def _validate_options_and_default(self) -> "TemplateField":
        if self.type == "enum":
            if not self.options:
                raise ValueError(
                    f"field '{self.name}' 类型 enum 但未提供 options (enum requires options)"
                )
            if self.default is not None and self.default not in self.options:
                raise ValueError(
                    f"field '{self.name}' default={self.default!r} 不在 options={self.options}"
                )
        else:
            if self.options is not None:
                raise ValueError(
                    f"field '{self.name}' 类型 {self.type} 不可携带 options (仅 enum 允许)"
                )
        if self.default is not None and not self._is_type_compatible(self.default):
            raise ValueError(
                f"field '{self.name}' default={self.default!r} 与 type={self.type} 不兼容"
            )
        return self

    def _is_type_compatible(self, value: Any) -> bool:
        if self.type == "str":
            return isinstance(value, str)
        if self.type == "list[str]":
            return isinstance(value, list) and all(isinstance(x, str) for x in value)
        if self.type == "enum":
            return isinstance(value, str)
        if self.type == "bool":
            return isinstance(value, bool)
        if self.type == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        return False


class Template(BaseModel):
    """单模板载体."""

    template_id: str = Field(..., pattern=r"^M\d+$", description="形如 M1")
    name: str = Field(..., min_length=1, description="中文展示名")
    description: str = Field(default="", description="一行违规模式概述")
    master_prompt: str = Field(default="", description="Jinja2 模板字符串, 渲染为 prompt_addon")
    keywords_template: str = Field(default="", description="Jinja2 模板; 渲染产 trigger_keywords 列表的 yaml 字面量")
    tools_template: str = Field(default="", description="Jinja2 模板; 渲染产 suggested_tools yaml 字面量")
    signal_template: str = Field(default="", description="Jinja2 模板; 渲染产 expected_signal 字符串")
    fields: list[TemplateField] = Field(default_factory=list, description="personalization 字段表")

    @model_validator(mode="after")
    def _validate_unique_field_names(self) -> "Template":
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            seen: set[str] = set()
            dupes: list[str] = []
            for n in names:
                if n in seen:
                    dupes.append(n)
                seen.add(n)
            raise ValueError(f"template_id={self.template_id} 字段名重复: {dupes}")
        return self

    @property
    def is_empty(self) -> bool:
        """空骨架 (master_prompt 与 fields 都未填)."""
        return not self.master_prompt.strip() and not self.fields

    @property
    def status(self) -> Literal["empty", "partial", "ready"]:
        """根据 master_prompt + fields 推断模板填写状态."""
        if self.is_empty:
            return "empty"
        if not self.master_prompt.strip() or not self.fields:
            return "partial"
        return "ready"
