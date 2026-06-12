# -*- coding: utf-8 -*-
"""AuditResult / Evidence / ToolCall pydantic 模型."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Verdict = Literal["VIOLATION", "CLEAN", "INCONCLUSIVE"]


class Evidence(BaseModel):
    """单条证据 — 引用 patient 文书或费用记录."""

    source: str = Field(..., description="证据来源 (note / fee / drug_indication ...)")
    locator: str = Field(default="", description="行号 / section name / 项目名等定位字符串")
    text: str = Field(default="", description="证据原文摘录 (可截断)")
    # v0.9 (evidence-anchoring, 前向): 工具回传的精确锚点 {tab,subsection,query,char_start,char_end};
    # 默认 None — 老数据无此字段, hit_resolver 走匹配阶梯兜底; 新审计工具填后被直接采用.
    anchor: dict | None = Field(
        default=None, description="可选精确锚点 (search 工具增强后填; 老数据为 None)"
    )


class ToolCall(BaseModel):
    """单次工具调用记录."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str = Field(default="", description="工具返回文本 (≤2000 char)")
    duration_ms: int = Field(default=0, ge=0)
    cached: bool = Field(default=False)


class AuditResult(BaseModel):
    """单次 (rule, patient) 审计的全量结果."""

    run_id: str = Field(..., pattern=r"^aud_[A-Za-z0-9_-]{12}$")
    rule_id: str = Field(..., pattern=r"^(R\d{3}|RD\d{2,3})$")
    patient_id: str
    verdict: Verdict
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = Field(default="")
    evidence: list[Evidence] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)
    model: str = Field(default="")
    started_at: datetime
    # add-verdict-gate-layer: 确定性 gate 降级标签 ∈ {"", 缺文书, 单次放过, 低置信降级}.
    # 空 = 未被 gate 降级 (verdict 即 LLM 原判).
    gate_tag: str = Field(default="")
