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
OncologyPolicyScope = Literal["INSURANCE_PAYMENT", "GUIDELINE_INDICATION"]
TemporalApplicability = Literal[
    "BEFORE_EFFECTIVE_WINDOW",
    "IN_WINDOW",
    "AFTER_EFFECTIVE_WINDOW",
]

_POLICY_SCOPE_DISPLAY_LABELS: dict[OncologyPolicyScope, str] = {
    "INSURANCE_PAYMENT": "医保支付限定",
    "GUIDELINE_INDICATION": "指南适应证",
}

_LEGACY_PROJECTION: dict[AuditDisposition, LegacyVerdict] = {
    AuditDisposition.NO_VIOLATION_FOUND: "CLEAN",
    AuditDisposition.VIOLATION_FOUND: "VIOLATION",
    AuditDisposition.REVIEW_REQUIRED: "INCONCLUSIVE",
}

_DISPOSITION_SEVERITY: dict[AuditDisposition, int] = {
    AuditDisposition.NO_VIOLATION_FOUND: 0,
    AuditDisposition.REVIEW_REQUIRED: 1,
    AuditDisposition.VIOLATION_FOUND: 2,
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


class EligibilityScopeEvaluation(BaseModel):
    """单个 patient/drug/policy scope 的完整资格快照。

    它刻意不包含 ``scope_evaluations``，避免递归 payload；顶层
    ``EligibilityEvaluation`` 仍承担旧三态兼容投影。
    """

    audit_disposition: AuditDisposition
    eligibility_status: EligibilityStatus
    legacy_verdict: LegacyVerdict | None = None
    rule_id: str = ""
    rule_version: str = ""
    indication_branch_id: str = ""
    source_versions: list[str] = Field(default_factory=list)
    release_id: str = Field(min_length=1)
    # scope 整体因时间窗口或来源门禁 fail-closed 时可能未选中单一 revision。
    rule_revision_id: str | None = None
    drug_concept_id: str = Field(min_length=1)
    policy_scope: OncologyPolicyScope
    source_type: OncologyPolicyScope
    policy_scope_display_label: str = Field(min_length=1)
    source_document_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)
    criterion_assessments: list[CriterionAssessment] = Field(default_factory=list)
    proof_tree: ProofNode
    data_quality_flags: list[str] = Field(default_factory=list)
    documentation_suggestions: list[DocumentationSuggestion] = Field(default_factory=list)
    rule_effective_from: date | None = None
    rule_effective_to: date | None = None
    evaluated_service_date: date | None = None
    effective_date_enforced: bool = True
    temporal_applicability: TemporalApplicability | None = None
    temporal_warning: str = ""

    @model_validator(mode="after")
    def _validate_scope_snapshot(self) -> "EligibilityScopeEvaluation":
        expected = project_legacy_verdict(self.audit_disposition)
        if self.legacy_verdict is None:
            self.legacy_verdict = expected
        elif self.legacy_verdict != expected:
            raise ValueError("单 scope legacy_verdict 与 audit_disposition 投影不一致")
        if self.source_type != self.policy_scope:
            raise ValueError("policy_scope 与 source_type 必须一致，来源不得互相冒充")
        expected_label = _POLICY_SCOPE_DISPLAY_LABELS[self.policy_scope]
        if self.policy_scope_display_label != expected_label:
            raise ValueError(
                "policy_scope_display_label 与 policy_scope 不一致，"
                "指南适应证不得显示成法定说明书"
            )
        return self


class EligibilityEvaluation(BaseModel):
    """条件求值、双轴结果和旧 verdict 投影的完整结构化结果."""

    audit_disposition: AuditDisposition
    eligibility_status: EligibilityStatus
    legacy_verdict: LegacyVerdict | None = None
    rule_id: str = ""
    rule_version: str = ""
    indication_branch_id: str = ""
    source_versions: list[str] = Field(default_factory=list)
    # add-oncology-kb-authoring: 以下均为只加可选 provenance；旧 JSON 不要求回填。
    release_id: str | None = None
    rule_revision_id: str | None = None
    drug_concept_id: str | None = None
    policy_scope: OncologyPolicyScope | None = None
    source_type: OncologyPolicyScope | None = None
    policy_scope_display_label: str | None = None
    source_document_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)
    criterion_assessments: list[CriterionAssessment] = Field(default_factory=list)
    proof_tree: ProofNode
    data_quality_flags: list[str] = Field(default_factory=list)
    documentation_suggestions: list[DocumentationSuggestion] = Field(default_factory=list)
    # 生效期透明化: 声明生效窗口 + 本次就诊日 + 是否按生效期过滤 (旧行读为默认值, 前端据此提示核查).
    rule_effective_from: date | None = None
    rule_effective_to: date | None = None
    evaluated_service_date: date | None = None
    effective_date_enforced: bool = True
    temporal_applicability: TemporalApplicability | None = None
    temporal_warning: str = ""
    # add-oncology-kb-authoring: 保留每个 patient/drug 的双 scope 状态和证据。
    # 旧 singular eligibility_json 缺少本字段时按空列表读取。
    scope_evaluations: list[EligibilityScopeEvaluation] = Field(default_factory=list)

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
        if (
            self.policy_scope is not None
            and self.source_type is not None
            and self.policy_scope != self.source_type
        ):
            raise ValueError("policy_scope 与 source_type 必须一致，来源不得互相冒充")
        if self.policy_scope is not None and self.policy_scope_display_label:
            expected_label = _POLICY_SCOPE_DISPLAY_LABELS[self.policy_scope]
            if self.policy_scope_display_label != expected_label:
                raise ValueError(
                    "policy_scope_display_label 与 policy_scope 不一致，"
                    "指南适应证不得显示成法定说明书"
                )
        seen: set[tuple[str, OncologyPolicyScope]] = set()
        for item in self.scope_evaluations:
            key = (item.drug_concept_id, item.policy_scope)
            if key in seen:
                raise ValueError(
                    "同一 patient/drug/policy_scope 只能保留一个资格状态"
                )
            seen.add(key)
            if self.release_id is not None and item.release_id != self.release_id:
                raise ValueError("同一结果的 scope_evaluations 不得混用 release")
        if self.scope_evaluations:
            worst = max(
                self.scope_evaluations,
                key=lambda item: _DISPOSITION_SEVERITY[item.audit_disposition],
            )
            if self.audit_disposition != worst.audit_disposition:
                raise ValueError(
                    "顶层 audit_disposition 必须是 scope_evaluations 的最严重确定性投影"
                )
        return self

    def as_scope_evaluation(self) -> EligibilityScopeEvaluation:
        """从顶层求值构造非递归 scope 快照。"""

        return EligibilityScopeEvaluation.model_validate(
            self.model_dump(mode="json", exclude={"scope_evaluations"})
        )
