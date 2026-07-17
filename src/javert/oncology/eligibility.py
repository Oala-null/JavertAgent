# -*- coding: utf-8 -*-
"""版本化肿瘤医保条件树加载与确定性四态求值."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .contracts import (
    AuditDisposition,
    CriterionAssessment,
    CriterionState,
    EligibilityEvaluation,
    EligibilityStatus,
    ProofNode,
)
from .knowledge import (
    KnowledgeEntryMetadata,
    KnowledgeMetadata,
    ReviewStatus,
    asset_payload_checksum,
)

CriterionType = Literal[
    "diagnosis",
    "stage",
    "biomarker",
    "prior_therapy",
    "line_of_therapy",
    "treatment_status",
    "clinician_assessment",
]


class ConditionNode(BaseModel):
    """统一 AST 节点；kind 决定 all/any/leaf 形态."""

    node_id: str = Field(min_length=1)
    kind: Literal["all", "any", "leaf"]
    source_text: str = ""
    children: list["ConditionNode"] = Field(default_factory=list)
    criterion_id: str = ""
    criterion_type: CriterionType | str = ""
    expected: dict[str, Any] = Field(default_factory=dict)
    evidence_policy: dict[str, Any] = Field(default_factory=dict)
    documentation_template: str = ""

    @model_validator(mode="after")
    def _validate_shape(self) -> "ConditionNode":
        if self.kind == "leaf":
            if self.children:
                raise ValueError("leaf condition 不能包含 children")
            if not self.criterion_id or not self.criterion_type:
                raise ValueError("leaf condition 必须包含 criterion_id/criterion_type")
        elif not self.children:
            raise ValueError("all/any condition 至少包含一个 child")
        return self


class EligibilityRule(BaseModel):
    rule_id: str = Field(min_length=1)
    drug_concept_id: str = Field(min_length=1)
    indication_branch_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    raw_restriction: str = Field(min_length=1)
    metadata: KnowledgeEntryMetadata
    condition_tree: ConditionNode


class EligibilityRulesAsset(BaseModel):
    asset_type: Literal["eligibility"] = "eligibility"
    metadata: KnowledgeMetadata
    entries: list[EligibilityRule]


class RuleSelection(BaseModel):
    rules: list[EligibilityRule] = Field(default_factory=list)
    blocked_rule_ids: list[str] = Field(default_factory=list)
    data_quality_flags: list[str] = Field(default_factory=list)


def load_eligibility_rules(path: Path) -> EligibilityRulesAsset:
    """校验 schema、checksum 后加载；不接受被修改的知识资产."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    asset = EligibilityRulesAsset.model_validate(raw)
    expected = asset_payload_checksum(raw)
    if asset.metadata.checksum != expected:
        raise ValueError(
            f"oncology eligibility checksum 不一致: "
            f"declared={asset.metadata.checksum}, expected={expected}"
        )
    return asset


def _effective(metadata: KnowledgeEntryMetadata, service_date: date) -> bool:
    return metadata.effective_from <= service_date and (
        metadata.effective_to is None or service_date <= metadata.effective_to
    )


def select_effective_rules(
    asset: EligibilityRulesAsset,
    *,
    drug_concept_id: str,
    service_date: date,
) -> RuleSelection:
    """按药品与服务日期选择唯一生效、已审核版本，重叠版本显式阻断."""
    candidates = [
        entry
        for entry in asset.entries
        if entry.drug_concept_id == drug_concept_id
        and _effective(entry.metadata, service_date)
    ]
    blocked = [
        entry.rule_id
        for entry in candidates
        if entry.metadata.review_status != ReviewStatus.APPROVED
    ]
    approved = [
        entry
        for entry in candidates
        if entry.metadata.review_status == ReviewStatus.APPROVED
    ]

    flags: list[str] = []
    if blocked:
        flags.append("UNAPPROVED_RULE_VERSION")
    by_branch: dict[str, list[EligibilityRule]] = {}
    for entry in approved:
        by_branch.setdefault(entry.indication_branch_id, []).append(entry)
    for branch_id, versions in by_branch.items():
        if len(versions) > 1:
            flags.append(f"AMBIGUOUS_EFFECTIVE_VERSION:{branch_id}")
    if any(flag.startswith("AMBIGUOUS_EFFECTIVE_VERSION") for flag in flags):
        approved = []
    return RuleSelection(
        rules=sorted(approved, key=lambda item: (item.indication_branch_id, item.version)),
        blocked_rule_ids=sorted(blocked),
        data_quality_flags=flags,
    )


LeafEvaluator = Callable[[ConditionNode], CriterionAssessment]


def _unknown_assessment(node: ConditionNode, reason: str) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id=node.criterion_id,
        criterion_type=node.criterion_type or "unsupported",
        state=CriterionState.UNKNOWN,
        expected_condition=node.expected,
        reason=reason,
        missing_items=[node.criterion_id],
        evaluator_version="1.0.0",
    )


def assessment_evaluator(
    assessments: Mapping[str, CriterionAssessment],
) -> LeafEvaluator:
    """把已归一的叶子求值映射成 evaluator；缺失永远 UNKNOWN."""

    def evaluate(node: ConditionNode) -> CriterionAssessment:
        assessment = assessments.get(node.criterion_id)
        if assessment is None:
            return _unknown_assessment(node, "未找到满足证据策略的结构化事实")
        if assessment.criterion_id != node.criterion_id:
            raise ValueError("criterion assessment 与 condition node ID 不一致")
        return assessment

    return evaluate


def aggregate_state(
    kind: Literal["all", "any"],
    child_states: list[CriterionState],
) -> CriterionState:
    """严格实现规格中的 AND/OR 四态真值表."""
    if not child_states:
        raise ValueError("聚合节点至少需要一个 child state")
    if kind == "all":
        if CriterionState.NOT_SATISFIED in child_states:
            return CriterionState.NOT_SATISFIED
        if CriterionState.CONFLICT in child_states:
            return CriterionState.CONFLICT
        if CriterionState.UNKNOWN in child_states:
            return CriterionState.UNKNOWN
        return CriterionState.SATISFIED
    if CriterionState.SATISFIED in child_states:
        return CriterionState.SATISFIED
    if all(state == CriterionState.NOT_SATISFIED for state in child_states):
        return CriterionState.NOT_SATISFIED
    if CriterionState.CONFLICT in child_states:
        return CriterionState.CONFLICT
    return CriterionState.UNKNOWN


def _decisive_ids(
    kind: Literal["all", "any"],
    state: CriterionState,
    children: list[ProofNode],
) -> list[str]:
    if kind == "all":
        target = {
            CriterionState.NOT_SATISFIED: CriterionState.NOT_SATISFIED,
            CriterionState.CONFLICT: CriterionState.CONFLICT,
            CriterionState.UNKNOWN: CriterionState.UNKNOWN,
        }.get(state)
        if target is None:
            return [child.node_id for child in children]
        return [child.node_id for child in children if child.state == target]
    target = {
        CriterionState.SATISFIED: CriterionState.SATISFIED,
        CriterionState.CONFLICT: CriterionState.CONFLICT,
        CriterionState.UNKNOWN: CriterionState.UNKNOWN,
    }.get(state)
    if target is None:
        return [child.node_id for child in children]
    return [child.node_id for child in children if child.state == target]


def evaluate_condition_tree(
    node: ConditionNode,
    leaf_evaluator: LeafEvaluator,
    *,
    source_version: str,
) -> ProofNode:
    """递归求值并返回与 AST 同构、保留全部子节点的 proof tree."""
    if node.kind == "leaf":
        try:
            assessment = leaf_evaluator(node)
        except KeyError:
            assessment = _unknown_assessment(node, "不支持的 criterion type")
        return ProofNode(
            node_id=node.node_id,
            operator="leaf",
            state=assessment.state,
            criterion_type=node.criterion_type,
            criterion_id=node.criterion_id,
            assessment=assessment,
            evaluator_version="1.0.0",
            source_version=source_version,
            reason=assessment.reason,
        )

    children = [
        evaluate_condition_tree(
            child,
            leaf_evaluator,
            source_version=source_version,
        )
        for child in node.children
    ]
    state = aggregate_state(node.kind, [child.state for child in children])
    return ProofNode(
        node_id=node.node_id,
        operator=node.kind,
        state=state,
        decisive_child_ids=_decisive_ids(node.kind, state, children),
        children=children,
        evaluator_version="1.0.0",
        source_version=source_version,
    )


def _flatten_assessments(proof: ProofNode) -> list[CriterionAssessment]:
    if proof.assessment is not None:
        return [proof.assessment]
    out: list[CriterionAssessment] = []
    for child in proof.children:
        out.extend(_flatten_assessments(child))
    return out


def _decisive_unknown_assessments(proof: ProofNode) -> list[CriterionAssessment]:
    """只取导致当前根节点 UNKNOWN 的叶子；已满足 OR 臂内的未知兄弟不阻断资格."""
    if proof.state != CriterionState.UNKNOWN:
        return []
    if proof.assessment is not None:
        return [proof.assessment]
    decisive = set(proof.decisive_child_ids)
    return [
        assessment
        for child in proof.children
        if child.node_id in decisive
        for assessment in _decisive_unknown_assessments(child)
    ]


def _result_axes(
    proof: ProofNode,
) -> tuple[AuditDisposition, EligibilityStatus]:
    if proof.state == CriterionState.SATISFIED:
        return AuditDisposition.NO_VIOLATION_FOUND, EligibilityStatus.SATISFIED
    if proof.state == CriterionState.NOT_SATISFIED:
        return AuditDisposition.VIOLATION_FOUND, EligibilityStatus.NOT_SATISFIED
    if proof.state == CriterionState.CONFLICT:
        return AuditDisposition.REVIEW_REQUIRED, EligibilityStatus.CONFLICT

    unknown = _decisive_unknown_assessments(proof)
    attestation_only = bool(unknown) and all(
        item.criterion_type == "clinician_assessment" for item in unknown
    )
    disposition = (
        AuditDisposition.NO_VIOLATION_FOUND
        if attestation_only
        else AuditDisposition.REVIEW_REQUIRED
    )
    return disposition, EligibilityStatus.DOCUMENTATION_GAP


def evaluate_rule(
    rule: EligibilityRule,
    assessments: Mapping[str, CriterionAssessment],
) -> EligibilityEvaluation:
    """对单个 indication branch 求值，并保留精确规则/来源版本."""
    source_versions = sorted(
        {
            f"{source.source_id}@"
            f"{source.version or source.effective_date or source.publication_date}"
            for source in rule.metadata.source_refs
        }
    )
    if rule.metadata.review_status != ReviewStatus.APPROVED:
        blocked = _unknown_assessment(
            ConditionNode(
                node_id="unapproved-rule",
                kind="leaf",
                criterion_id="unapproved-rule",
                criterion_type="unsupported",
                expected={},
            ),
            "规则版本尚未审核，不允许自动裁决",
        )
        proof = ProofNode(
            node_id="unapproved-rule",
            operator="leaf",
            state=CriterionState.UNKNOWN,
            criterion_id="unapproved-rule",
            criterion_type="unsupported",
            assessment=blocked,
            source_version=rule.version,
            reason=blocked.reason,
        )
        return EligibilityEvaluation(
            audit_disposition=AuditDisposition.REVIEW_REQUIRED,
            eligibility_status=EligibilityStatus.DOCUMENTATION_GAP,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            indication_branch_id=rule.indication_branch_id,
            source_versions=source_versions,
            criterion_assessments=[blocked],
            proof_tree=proof,
            data_quality_flags=["UNAPPROVED_RULE_VERSION"],
        )

    proof = evaluate_condition_tree(
        rule.condition_tree,
        assessment_evaluator(assessments),
        source_version=rule.version,
    )
    disposition, eligibility_status = _result_axes(proof)
    return EligibilityEvaluation(
        audit_disposition=disposition,
        eligibility_status=eligibility_status,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        indication_branch_id=rule.indication_branch_id,
        source_versions=source_versions,
        criterion_assessments=_flatten_assessments(proof),
        proof_tree=proof,
    )
