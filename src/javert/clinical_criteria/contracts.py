# -*- coding: utf-8 -*-
"""领域中立的临床条件树合同.

这里只描述观测、四态结果和证明树，不包含任何病种或支付政策语义。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class CriterionState(StrEnum):
    """临床证据唯一允许的四种真值."""

    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class CriterionOperator(StrEnum):
    """条件树逻辑算子；值保持既有 proof JSON 的小写形状."""

    AND = "all"
    OR = "any"
    AT_LEAST_N = "at_least_n"
    LEAF = "leaf"


_OPERATOR_ALIASES = {
    "AND": CriterionOperator.AND,
    "ALL": CriterionOperator.AND,
    "OR": CriterionOperator.OR,
    "ANY": CriterionOperator.OR,
    "AT_LEAST_N": CriterionOperator.AT_LEAST_N,
    "LEAF": CriterionOperator.LEAF,
}


def normalize_operator(value: CriterionOperator | str) -> CriterionOperator:
    """接受知识资产的大写算子和既有 proof 的小写算子."""
    if isinstance(value, CriterionOperator):
        return value
    try:
        return CriterionOperator(value)
    except ValueError:
        try:
            return _OPERATOR_ALIASES[value.upper()]
        except (AttributeError, KeyError) as exc:
            raise ValueError(f"不支持的临床条件算子: {value!r}") from exc


class EvidenceAnchor(BaseModel):
    """可回到患者原始记录的最小证据定位，不复制整份原文."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    locator: str = ""
    text: str = ""
    anchor: dict[str, Any] | None = None


class Observation(BaseModel):
    """确定性 normalizer 输出的类型化临床观测."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    fact_type: str = Field(min_length=1)
    concept_id: str = ""
    raw_value: Any = Field(default=None, validation_alias=AliasChoices("raw_value", "value"))
    normalized_value: Any = None
    raw_unit: str = ""
    canonical_unit: str = ""
    conversion_id: str = ""
    assertion: str = ""
    polarity: str = ""
    certainty: str = ""
    observation_time: datetime | date | None = Field(
        default=None,
        validation_alias=AliasChoices("observation_time", "observation_date"),
    )
    service_time: datetime | date | None = Field(
        default=None,
        validation_alias=AliasChoices("service_time", "service_date"),
    )
    date_basis: str = ""
    source_domain: str = Field(
        min_length=1,
        validation_alias=AliasChoices("source_domain", "source"),
    )
    source_row_key: str = ""
    evidence_anchor: EvidenceAnchor | None = Field(
        default=None,
        validation_alias=AliasChoices("evidence_anchor", "anchor"),
    )
    extraction_method: str = Field(
        default="",
        validation_alias=AliasChoices("extraction_method", "method"),
    )
    normalizer_version: str = ""
    deduplication_key: str = ""
    uncertainty_reason: str = ""
    context: dict[str, Any] = Field(default_factory=dict)

    @property
    def value(self) -> Any:
        """兼容既有 ``NormalizedFact.value`` 读取."""
        return self.raw_value

    @property
    def source(self) -> str:
        """兼容既有 ``NormalizedFact.source`` 读取."""
        return self.source_domain

    @property
    def anchor(self) -> EvidenceAnchor | None:
        """兼容既有 ``NormalizedFact.anchor`` 读取."""
        return self.evidence_anchor


# 通用层保留既有领域使用的名称；二者表示同一种规范化观测。
NormalizedFact = Observation


class CriterionAssessment(BaseModel):
    """单个叶条件的确定性四态求值结果."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    criterion_id: str = Field(min_length=1)
    criterion_type: str = Field(min_length=1)
    state: CriterionState
    expected_condition: dict[str, Any] = Field(default_factory=dict)
    normalized_facts: list[Observation] = Field(
        default_factory=list,
        validation_alias=AliasChoices("normalized_facts", "observations"),
    )
    evidence_anchors: list[EvidenceAnchor] = Field(default_factory=list)
    comparison_result: dict[str, Any] = Field(default_factory=dict)
    unit_conversion: dict[str, Any] = Field(default_factory=dict)
    repetition_calculation: dict[str, Any] = Field(default_factory=dict)
    temporal_calculation: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    missing_items: list[str] = Field(default_factory=list)
    conflict_items: list[str] = Field(default_factory=list)
    evaluator_version: str = "1.0.0"
    normalizer_version: str = ""
    criteria_revision: str = ""
    source_version: str = ""

    @property
    def observations(self) -> list[Observation]:
        return self.normalized_facts


class ConditionNode(BaseModel):
    """供通用遍历器消费的最小条件树节点."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    node_id: str = Field(min_length=1)
    operator: CriterionOperator = Field(validation_alias=AliasChoices("operator", "kind"))
    children: list["ConditionNode"] = Field(default_factory=list)
    threshold: int | None = None
    criterion_id: str = ""
    criterion_type: str = ""
    expected_condition: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("expected_condition", "expected"),
    )
    evidence_policy: dict[str, Any] = Field(default_factory=dict)
    source_text: str = ""

    @field_validator("operator", mode="before")
    @classmethod
    def _normalize_operator(cls, value: CriterionOperator | str) -> CriterionOperator:
        return normalize_operator(value)

    @model_validator(mode="after")
    def _validate_shape(self) -> "ConditionNode":
        if self.operator == CriterionOperator.LEAF:
            if self.children:
                raise ValueError("leaf condition 不能包含 children")
            if not self.criterion_id or not self.criterion_type:
                raise ValueError("leaf condition 必须包含 criterion_id/criterion_type")
            if self.threshold is not None:
                raise ValueError("leaf condition 不能声明 threshold")
            return self

        if not self.children:
            raise ValueError("聚合 condition 至少包含一个 child")
        if len({child.node_id for child in self.children}) != len(self.children):
            raise ValueError("同一聚合节点的 child node_id 必须唯一")
        if self.operator == CriterionOperator.AT_LEAST_N:
            if (
                isinstance(self.threshold, bool)
                or self.threshold is None
                or not 1 <= self.threshold <= len(self.children)
            ):
                raise ValueError("AT_LEAST_N threshold 必须在 1..children 数量之间")
        elif self.threshold is not None:
            raise ValueError("只有 AT_LEAST_N condition 可以声明 threshold")
        return self

    @property
    def kind(self) -> str:
        """兼容既有 ``ConditionNode.kind`` 读取."""
        return self.operator.value


class ProofNode(BaseModel):
    """与输入条件树同构、包含决定性信息的证明节点."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    operator: CriterionOperator
    state: CriterionState
    criterion_type: str = ""
    criterion_id: str = ""
    decisive_child_ids: list[str] = Field(default_factory=list)
    children: list["ProofNode"] = Field(default_factory=list)
    assessment: CriterionAssessment | None = None
    threshold: int | None = None
    lower_bound: int | None = None
    upper_bound: int | None = None
    evaluator_version: str = "1.0.0"
    criteria_revision: str = ""
    source_version: str = ""
    reason: str = ""

    @field_validator("operator", mode="before")
    @classmethod
    def _normalize_operator(cls, value: CriterionOperator | str) -> CriterionOperator:
        return normalize_operator(value)

    @model_validator(mode="after")
    def _validate_shape(self) -> "ProofNode":
        if self.operator == CriterionOperator.LEAF:
            if self.children:
                raise ValueError("leaf proof node 不能包含 children")
            if self.assessment is None:
                raise ValueError("leaf proof node 必须包含 assessment")
            if self.criterion_id and self.criterion_id != self.assessment.criterion_id:
                raise ValueError("proof criterion_id 与 assessment 不一致")
            if self.state != self.assessment.state:
                raise ValueError("leaf proof state 与 assessment 不一致")
            if self.decisive_child_ids:
                raise ValueError("leaf proof node 不能包含 decisive_child_ids")
            if any(
                value is not None
                for value in (self.threshold, self.lower_bound, self.upper_bound)
            ):
                raise ValueError("leaf proof node 不能包含聚合边界")
            return self

        if not self.children:
            raise ValueError("聚合 proof node 至少包含一个 child")
        if self.assessment is not None:
            raise ValueError("聚合 proof node 不能直接包含 assessment")
        child_ids = [child.node_id for child in self.children]
        if len(set(child_ids)) != len(child_ids):
            raise ValueError("同一 proof 节点的 child node_id 必须唯一")
        if len(set(self.decisive_child_ids)) != len(self.decisive_child_ids):
            raise ValueError("decisive_child_ids 不能重复")
        if not set(self.decisive_child_ids).issubset(child_ids):
            raise ValueError("decisive_child_ids 必须指向直接 children")

        if self.operator == CriterionOperator.AT_LEAST_N:
            child_count = len(self.children)
            expected_lower = sum(
                child.state == CriterionState.SATISFIED for child in self.children
            )
            expected_upper = sum(
                child.state != CriterionState.NOT_SATISFIED for child in self.children
            )
            if (
                isinstance(self.threshold, bool)
                or self.threshold is None
                or not 1 <= self.threshold <= child_count
            ):
                raise ValueError("AT_LEAST_N proof threshold 不合法")
            if self.lower_bound != expected_lower or self.upper_bound != expected_upper:
                raise ValueError("AT_LEAST_N proof 上下界与 children 状态不一致")
        elif any(
            value is not None
            for value in (self.threshold, self.lower_bound, self.upper_bound)
        ):
            raise ValueError("只有 AT_LEAST_N proof 可以包含 threshold/上下界")
        return self
