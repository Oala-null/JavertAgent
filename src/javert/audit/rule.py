# -*- coding: utf-8 -*-
"""Rule pydantic 模型 + Status 枚举."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["drafting", "ready", "validated", "abandoned"]
Priority = Literal["P0", "P1", "P2", "P3"]


class Rule(BaseModel):
    """单条「做不了」规则的 yaml 数据载体.

    字段顺序与 spec 一致, 由 rule_writer 控制写入顺序.
    """

    rule_id: str = Field(
        ...,
        pattern=r"^(R\d{3}|RD\d{2,3})$",
        description="形如 R191 (0325 序号规则) 或 RD01 (药品类规则, 无 0325 序号)",
    )
    domain: str = Field(..., min_length=1, description="所属领域 (肿瘤/各科室通用类/临床检验...)")
    violation_type: str = Field(..., min_length=1, description="违规类型 (从 0325 表 copy)")
    question: str = Field(..., min_length=1, description="问题描述 (从 0325 表 copy)")
    example: str = Field(default="", description="违规参考示例 (从 0325 表 copy, 可空)")
    status: Status = Field(default="drafting", description="状态")
    priority: Priority = Field(default="P3", description="审计优先级 (P0 最高/P3 最低, 由专家标注)")
    prompt_addon: str = Field(default="", description="规则特定 prompt 片段, 由操作者编写")
    trigger_keywords: list[str] = Field(default_factory=list, description="触发关键词列表")
    suggested_tools: list[str] = Field(default_factory=list, description="建议优先调用的工具名")
    expected_signal: str = Field(default="", description="预期信号 (操作者备注)")
    notes: str = Field(default="", description="设计 / 取舍说明")
    derived_from_template: str | None = Field(
        default=None,
        description="若 prompt_addon 由某模板 (例如 M1) 渲染生成则记录 template_id; 否则 None",
    )
    drug_rule_type: str | None = Field(
        default=None,
        description="药品类规则 (M8) 的类型: 限适应症/超说明书/限二线/禁忌症; 非药品规则为 None",
    )


class RuleValidationError(ValueError):
    """加载 yaml 失败 (字段缺失 / 类型错误 / status 非法)."""
