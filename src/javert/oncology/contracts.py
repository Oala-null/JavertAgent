# -*- coding: utf-8 -*-
"""肿瘤药资格求值的共享、稳定契约.

本模块只定义跨工作线共用的数据边界，不包含病理、方案或医保条件的具体算法。
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CriterionState(StrEnum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class AuditDisposition(StrEnum):
    NO_VIOLATION_FOUND = "NO_VIOLATION_FOUND"
    VIOLATION_FOUND = "VIOLATION_FOUND"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class EligibilityStatus(StrEnum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    DOCUMENTATION_GAP = "DOCUMENTATION_GAP"
    CONFLICT = "CONFLICT"


LegacyVerdict = Literal["VIOLATION", "CLEAN", "INCONCLUSIVE"]

_LEGACY_PROJECTION: dict[AuditDisposition, LegacyVerdict] = {
    AuditDisposition.NO_VIOLATION_FOUND: "CLEAN",
    AuditDisposition.VIOLATION_FOUND: "VIOLATION",
    AuditDisposition.REVIEW_REQUIRED: "INCONCLUSIVE",
}


def project_legacy_verdict(disposition: AuditDisposition | str) -> LegacyVerdict:
    """把审核处置确定性投影到旧三态 verdict."""
    return _LEGACY_PROJECTION[AuditDisposition(disposition)]


class EvidenceAnchor(BaseModel):
    """可回到原始数据的轻量锚点；不复制整份患者原文."""

    model_config = ConfigDict(extra="allow")

    source: str = Field(min_length=1)
    locator: str = ""
    text: str = ""
    anchor: dict[str, Any] | None = None


class NormalizedFact(BaseModel):
    """由确定性 normalizer 输出的单条规范化事实."""

    fact_type: str = Field(min_length=1)
    value: Any = None
    normalized_value: Any = None
    source: str = Field(min_length=1)
    anchor: EvidenceAnchor | None = None
    observation_date: date | None = None
    service_date: date | None = None
    method: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    normalizer_version: str = Field(default="", description="事实归一器版本")
    uncertainty_reason: str = ""


class CriterionAssessment(BaseModel):
    """稳定 criterion 的四态求值结果."""

    criterion_id: str = Field(min_length=1)
    criterion_type: str = Field(min_length=1)
    state: CriterionState
    expected_condition: dict[str, Any] = Field(default_factory=dict)
    normalized_facts: list[NormalizedFact] = Field(default_factory=list)
    evidence_anchors: list[EvidenceAnchor] = Field(default_factory=list)
    reason: str = ""
    missing_items: list[str] = Field(default_factory=list)
    evaluator_version: str = Field(default="1.0.0")
    normalizer_version: str = ""
    source_version: str = ""


class ProofNode(BaseModel):
    """与可执行条件树同构的证明节点."""

    node_id: str = Field(min_length=1)
    operator: Literal["all", "any", "leaf"]
    state: CriterionState
    criterion_type: str = ""
    criterion_id: str = ""
    decisive_child_ids: list[str] = Field(default_factory=list)
    children: list["ProofNode"] = Field(default_factory=list)
    assessment: CriterionAssessment | None = None
    evaluator_version: str = Field(default="1.0.0")
    source_version: str = ""
    reason: str = ""

    @model_validator(mode="after")
    def _validate_shape(self) -> "ProofNode":
        if self.operator == "leaf":
            if self.children:
                raise ValueError("leaf proof node 不能包含 children")
            if self.assessment is None:
                raise ValueError("leaf proof node 必须包含 assessment")
            if self.criterion_id and self.criterion_id != self.assessment.criterion_id:
                raise ValueError("proof criterion_id 与 assessment 不一致")
        elif self.assessment is not None:
            raise ValueError("all/any proof node 不能直接包含 assessment")
        return self


class DocumentationSuggestion(BaseModel):
    """针对单一文书缺口的前瞻性建议，不能充当证据."""

    criterion_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    suggested_content: str = Field(min_length=1)
    supporting_context: list[str] = Field(default_factory=list)
    priority: Literal["low", "medium", "high"] = "medium"
    safety_note: str = Field(
        default="本建议仅用于完善病历记录，不代表缺失条件已被证实。"
    )


class EligibilityEvaluation(BaseModel):
    """条件求值、双轴结果和旧 verdict 投影的完整结构化结果."""

    audit_disposition: AuditDisposition
    eligibility_status: EligibilityStatus
    legacy_verdict: LegacyVerdict | None = None
    rule_id: str = ""
    rule_version: str = ""
    indication_branch_id: str = ""
    source_versions: list[str] = Field(default_factory=list)
    criterion_assessments: list[CriterionAssessment] = Field(default_factory=list)
    proof_tree: ProofNode
    data_quality_flags: list[str] = Field(default_factory=list)
    documentation_suggestions: list[DocumentationSuggestion] = Field(default_factory=list)
    # 生效期透明化: 声明生效窗口 + 本次就诊日 + 是否按生效期过滤 (旧行读为默认值, 前端据此提示核查).
    rule_effective_from: date | None = None
    rule_effective_to: date | None = None
    evaluated_service_date: date | None = None
    effective_date_enforced: bool = True

    @model_validator(mode="after")
    def _validate_legacy_projection(self) -> "EligibilityEvaluation":
        expected = project_legacy_verdict(self.audit_disposition)
        if self.legacy_verdict is None:
            self.legacy_verdict = expected
        elif self.legacy_verdict != expected:
            raise ValueError(
                f"legacy_verdict={self.legacy_verdict} 与 "
                f"audit_disposition={self.audit_disposition} 的投影 {expected} 不一致"
            )
        return self
