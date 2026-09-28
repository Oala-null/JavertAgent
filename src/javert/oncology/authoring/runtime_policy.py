"""肿瘤知识 release 的双来源作用域与非对称时间策略。

本模块只提供确定性纯逻辑，不读取 authoring 数据库，也不改写历史审计结果。
运行时接线可据此选择是否继续条件树求值，并把新增 provenance 字段附加到旧
``eligibility_json`` payload。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from javert.oncology.contracts import (
    AuditDisposition,
    LegacyVerdict,
    project_legacy_verdict,
)

from .models import (
    HistoricalApplicationPolicy,
    SourceType,
    TemporalApplicability,
)


HISTORICAL_APPLICABILITY_WARNING = "核查当期指南/医保限定是否适用"
FUTURE_RELEASE_REQUIRED_WARNING = "知识版本已超过声明有效期，请发布适用版本或人工确认"

RUNTIME_POLICY_SCOPES = frozenset(
    {SourceType.INSURANCE_PAYMENT, SourceType.GUIDELINE_INDICATION}
)

_POLICY_SCOPE_LABELS = {
    SourceType.INSURANCE_PAYMENT: "医保支付限定",
    SourceType.GUIDELINE_INDICATION: "指南适应证",
}


class TemporalSelection(BaseModel):
    """一次服务日期相对于 active release 声明窗口的选择结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service_date: date
    effective_from: date
    effective_to: date
    temporal_applicability: TemporalApplicability
    effective_date_enforced: bool
    automatic_adjudication_allowed: bool
    audit_disposition_override: AuditDisposition | None = None
    warning: str = ""


def select_temporal_policy(
    *,
    service_date: date,
    effective_from: date,
    effective_to: date,
    historical_application_policy: HistoricalApplicationPolicy = (
        HistoricalApplicationPolicy.APPLY_CURRENT_RELEASE_WITH_WARNING
    ),
) -> TemporalSelection:
    """按非对称策略选择 active release。

    起始日前允许使用当前 release 自动裁决并警告；窗口内正常裁决；结束日后
    不复用历史 fallback，直接要求人工复核。
    """

    if effective_from > effective_to:
        raise ValueError("effective_from 不能晚于 effective_to")
    if (
        historical_application_policy
        != HistoricalApplicationPolicy.APPLY_CURRENT_RELEASE_WITH_WARNING
    ):
        raise ValueError(f"不支持的历史应用策略: {historical_application_policy}")

    common = {
        "service_date": service_date,
        "effective_from": effective_from,
        "effective_to": effective_to,
    }
    if service_date < effective_from:
        return TemporalSelection(
            **common,
            temporal_applicability=TemporalApplicability.BEFORE_EFFECTIVE_WINDOW,
            effective_date_enforced=False,
            automatic_adjudication_allowed=True,
            warning=HISTORICAL_APPLICABILITY_WARNING,
        )
    if service_date > effective_to:
        return TemporalSelection(
            **common,
            temporal_applicability=TemporalApplicability.AFTER_EFFECTIVE_WINDOW,
            effective_date_enforced=True,
            automatic_adjudication_allowed=False,
            audit_disposition_override=AuditDisposition.REVIEW_REQUIRED,
            warning=FUTURE_RELEASE_REQUIRED_WARNING,
        )
    return TemporalSelection(
        **common,
        temporal_applicability=TemporalApplicability.IN_WINDOW,
        effective_date_enforced=True,
        automatic_adjudication_allowed=True,
    )


def policy_scope_display_label(policy_scope: SourceType | str) -> str:
    """返回受控展示名，杜绝把指南适应证显示成法定说明书。"""

    scope = SourceType(policy_scope)
    try:
        return _POLICY_SCOPE_LABELS[scope]
    except KeyError as exc:
        raise ValueError(f"非运行时肿瘤资格 policy_scope: {scope.value}") from exc


class ScopedPolicyEvaluation(BaseModel):
    """同一患者、药品概念和 policy scope 的唯一资格状态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    patient_id: str = Field(min_length=1)
    drug_concept_id: str = Field(min_length=1)
    release_id: str = Field(min_length=1)
    rule_revision_id: str = Field(min_length=1)
    policy_scope: SourceType
    source_type: SourceType
    audit_disposition: AuditDisposition
    source_document_ids: tuple[str, ...] = ()
    source_fragment_ids: tuple[str, ...] = ()
    source_versions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_scope_source_pair(self) -> "ScopedPolicyEvaluation":
        if self.policy_scope not in RUNTIME_POLICY_SCOPES:
            raise ValueError(
                f"运行时肿瘤资格不支持 policy_scope={self.policy_scope.value}"
            )
        if self.source_type != self.policy_scope:
            raise ValueError("policy_scope 与 source_type 必须一致，来源不得互相冒充")
        return self

    @property
    def display_source_label(self) -> str:
        return policy_scope_display_label(self.policy_scope)


@dataclass(frozen=True, slots=True)
class PolicyScopeKey:
    patient_id: str
    drug_concept_id: str
    policy_scope: SourceType


def index_scoped_evaluations(
    evaluations: Iterable[ScopedPolicyEvaluation],
) -> dict[PolicyScopeKey, ScopedPolicyEvaluation]:
    """建立 scope 索引，并 fail closed 阻止重复状态或跨 release 混用。"""

    indexed: dict[PolicyScopeKey, ScopedPolicyEvaluation] = {}
    release_by_patient_drug: dict[tuple[str, str], str] = {}
    for evaluation in evaluations:
        key = PolicyScopeKey(
            patient_id=evaluation.patient_id,
            drug_concept_id=evaluation.drug_concept_id,
            policy_scope=evaluation.policy_scope,
        )
        if key in indexed:
            raise ValueError(
                "同一 patient/drug/policy_scope 只能有一个资格状态: "
                f"{key.patient_id}/{key.drug_concept_id}/{key.policy_scope.value}"
            )

        patient_drug = (evaluation.patient_id, evaluation.drug_concept_id)
        existing_release = release_by_patient_drug.setdefault(
            patient_drug, evaluation.release_id
        )
        if existing_release != evaluation.release_id:
            raise ValueError(
                "同一 patient/drug 的双 policy scope 必须引用同一 knowledge release"
            )
        indexed[key] = evaluation
    return indexed


class RuntimeEligibilityPayload(BaseModel):
    """只加字段的 eligibility_json 读取合同。

    所有新增字段均可空，且保留未知旧字段，使未回填的旧行和新 payload 都能读取。
    """

    model_config = ConfigDict(extra="allow")

    release_id: str | None = None
    rule_revision_id: str | None = None
    drug_concept_id: str | None = None
    policy_scope: SourceType | None = None
    source_type: SourceType | None = None
    policy_scope_display_label: str | None = None
    source_document_ids: list[str] = Field(default_factory=list)
    source_fragment_ids: list[str] = Field(default_factory=list)
    source_versions: list[str] = Field(default_factory=list)
    rule_effective_from: date | None = None
    rule_effective_to: date | None = None
    evaluated_service_date: date | None = None
    temporal_applicability: TemporalApplicability | None = None
    effective_date_enforced: bool | None = None
    temporal_warning: str = ""

    @model_validator(mode="after")
    def _validate_optional_scope_pair(self) -> "RuntimeEligibilityPayload":
        if self.policy_scope is None and self.source_type is None:
            return self
        if self.policy_scope not in RUNTIME_POLICY_SCOPES:
            raise ValueError("新 payload 的 policy_scope 仅支持医保支付或指南适应证")
        if self.source_type is not None and self.source_type != self.policy_scope:
            raise ValueError("policy_scope 与 source_type 必须一致")
        return self


def add_runtime_provenance(
    legacy_payload: Mapping[str, Any],
    *,
    evaluation: ScopedPolicyEvaluation,
    temporal_selection: TemporalSelection,
) -> dict[str, Any]:
    """复制旧 payload 并附加 release/scope/time 字段，不原地修改调用方数据。"""

    payload = deepcopy(dict(legacy_payload))
    payload.update(
        {
            "release_id": evaluation.release_id,
            "rule_revision_id": evaluation.rule_revision_id,
            "drug_concept_id": evaluation.drug_concept_id,
            "policy_scope": evaluation.policy_scope.value,
            "source_type": evaluation.source_type.value,
            "policy_scope_display_label": evaluation.display_source_label,
            "source_document_ids": list(evaluation.source_document_ids),
            "source_fragment_ids": list(evaluation.source_fragment_ids),
            "source_versions": list(evaluation.source_versions),
            "rule_effective_from": temporal_selection.effective_from.isoformat(),
            "rule_effective_to": temporal_selection.effective_to.isoformat(),
            "evaluated_service_date": temporal_selection.service_date.isoformat(),
            "temporal_applicability": temporal_selection.temporal_applicability.value,
            "effective_date_enforced": temporal_selection.effective_date_enforced,
            "temporal_warning": temporal_selection.warning,
        }
    )
    if temporal_selection.audit_disposition_override is not None:
        override = temporal_selection.audit_disposition_override
        payload["audit_disposition"] = override.value
        payload["legacy_verdict"] = project_legacy_verdict(override)
    return payload


def temporal_legacy_verdict(selection: TemporalSelection) -> LegacyVerdict | None:
    """暴露未来 fail-closed 的旧三态投影；可自动裁决时不覆盖业务结果。"""

    if selection.audit_disposition_override is None:
        return None
    return project_legacy_verdict(selection.audit_disposition_override)
