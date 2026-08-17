# -*- coding: utf-8 -*-
"""AuditResult / Evidence / ToolCall pydantic 模型."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from javert.oncology.contracts import EligibilityEvaluation
from javert.promises.models import PromiseTrace

Verdict = Literal["VIOLATION", "CLEAN", "INCONCLUSIVE"]
TOOL_FAILURE_GATE_TAG = "技术故障隔离"


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
    structured_output: dict[str, Any] | None = Field(
        default=None,
        description="可选确定性工具结果；shadow 比较随 tool_calls_json 持久化，不改变旧 verdict",
    )
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
    # add-verdict-gate-layer: 确定性 gate 标签包含缺文书、单次放过、低置信降级、技术故障隔离等.
    # 空 = 未被 gate 降级 (verdict 即 LLM 原判).
    gate_tag: str = Field(default="")
    # pilot-deterministic-precheck: 确定性预检标签 ∈ {"", 无A项, 无B项, A∩B并存待核反证}.
    # 空 = 未走预检 (原 LLM 路径); 前两者 = 预检短路 CLEAN; 后者 = 预检事实成立后进 LLM.
    precheck_tag: str = Field(default="")
    # strengthen-oncology-drug-eligibility: 旧规则保持 None；结构化肿瘤审核带完整双轴结果.
    eligibility_evaluation: EligibilityEvaluation | None = None
    # add-evolving-promise-harness: 仅命中终局 Promise 时写最小、去标识 trace。
    promise_trace: PromiseTrace | None = None
    # 内部持久化缓存：由本次审计实际收费切片确定性解析；不进入对外 API payload。
    anchors_json: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _validate_eligibility_projection(self) -> "AuditResult":
        if (
            self.eligibility_evaluation is not None
            and self.verdict != self.eligibility_evaluation.legacy_verdict
        ):
            raise ValueError(
                f"verdict={self.verdict} 与 eligibility_evaluation 的兼容投影 "
                f"{self.eligibility_evaluation.legacy_verdict} 不一致"
            )
        return self
