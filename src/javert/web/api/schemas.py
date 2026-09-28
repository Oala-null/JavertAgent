# -*- coding: utf-8 -*-
"""Web API Pydantic 响应模型."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# =========================================================
# Rule
# =========================================================
class RuleHistory(BaseModel):
    """历史 verdict 分布 (来自 SQLite audit_runs)."""

    V: int = 0
    C: int = 0
    I: int = 0
    total: int = 0
    mean_confidence: float = 0.0
    mean_duration_ms: float = 0.0


class RuleSummary(BaseModel):
    """规则列表项."""

    rule_id: str
    domain: str
    violation_type: str
    question: str
    status: str
    has_prompt_addon: bool
    trigger_keywords_count: int
    suggested_tools: list[str]
    history: RuleHistory | None = None


class RuleDetail(BaseModel):
    """规则详情 (含完整 yaml 字段 + 最近 N 条 audit)."""

    rule_id: str
    domain: str
    violation_type: str
    question: str
    example: str
    status: str
    prompt_addon: str
    trigger_keywords: list[str]
    suggested_tools: list[str]
    expected_signal: str
    notes: str
    history: RuleHistory | None = None
    recent_runs: list["AuditRunSummary"] = Field(default_factory=list)


# =========================================================
# Patient
# =========================================================
class PatientSampleResponse(BaseModel):
    """随机抽患者结果."""

    patient_ids: list[str]
    pool: str  # "pilot" | "full"
    pool_size: int


class PatientSummary(BaseModel):
    """患者元数据 (notes/fees 数 + 总费用)."""

    patient_id: str
    notes_count: int
    fees_count: int
    fee_sum: float | None = None


# =========================================================
# Audit
# =========================================================
class AuditRunSummary(BaseModel):
    """audit_runs 列表项."""

    run_id: str
    rule_id: str
    patient_id: str
    verdict: str
    confidence: float
    headline: str = ""
    duration_ms: int
    model: str
    started_at: datetime
    audit_disposition: str | None = None
    eligibility_status: str | None = None


class AuditRunDetail(AuditRunSummary):
    """audit_runs 详情 (展开 evidence/tool_calls)."""

    reasoning: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    eligibility_evaluation: dict[str, Any] | None = None


# 解决前向引用
RuleDetail.model_rebuild()
