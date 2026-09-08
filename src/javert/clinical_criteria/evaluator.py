# -*- coding: utf-8 -*-
"""临床条件树的确定性四态聚合与 proof 构建."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from .contracts import (
    ConditionNode,
    CriterionAssessment,
    CriterionOperator,
    CriterionState,
    ProofNode,
    normalize_operator,
)

LeafEvaluator = Callable[[ConditionNode], CriterionAssessment]


def _validated_states(
    child_states: Sequence[CriterionState | str],
) -> list[CriterionState]:
    states = [CriterionState(state) for state in child_states]
    if not states:
        raise ValueError("聚合节点至少需要一个 child state")
    return states


def _validate_threshold(threshold: int | None, child_count: int) -> int:
    if (
        isinstance(threshold, bool)
        or threshold is None
        or not 1 <= threshold <= child_count
    ):
        raise ValueError("AT_LEAST_N threshold 必须在 1..child state 数量之间")
    return threshold


def aggregate_state(
    operator: CriterionOperator | str,
    child_states: Sequence[CriterionState | str],
    threshold: int | None = None,
) -> CriterionState:
    """按固定真值表聚合直接子节点，缺失或冲突绝不自由解释."""
    normalized_operator = normalize_operator(operator)
    states = _validated_states(child_states)
    if normalized_operator == CriterionOperator.LEAF:
        raise ValueError("leaf 不能聚合 child states")

    if normalized_operator == CriterionOperator.AND:
        if threshold is not None:
            raise ValueError("AND 不接受 threshold")
        if CriterionState.NOT_SATISFIED in states:
            return CriterionState.NOT_SATISFIED
        if CriterionState.CONFLICT in states:
            return CriterionState.CONFLICT
        if CriterionState.UNKNOWN in states:
            return CriterionState.UNKNOWN
        return CriterionState.SATISFIED

    if normalized_operator == CriterionOperator.OR:
        if threshold is not None:
            raise ValueError("OR 不接受 threshold")
        if CriterionState.SATISFIED in states:
            return CriterionState.SATISFIED
        if CriterionState.CONFLICT in states:
            return CriterionState.CONFLICT
        if CriterionState.UNKNOWN in states:
            return CriterionState.UNKNOWN
        return CriterionState.NOT_SATISFIED

    required = _validate_threshold(threshold, len(states))
    lower_bound = states.count(CriterionState.SATISFIED)
    upper_bound = lower_bound + states.count(CriterionState.UNKNOWN) + states.count(
        CriterionState.CONFLICT
    )
    if lower_bound >= required:
        return CriterionState.SATISFIED
    if upper_bound < required:
        return CriterionState.NOT_SATISFIED
    if CriterionState.CONFLICT in states:
        return CriterionState.CONFLICT
    return CriterionState.UNKNOWN


def _decisive_child_ids(
    operator: CriterionOperator,
    state: CriterionState,
    children: Sequence[ProofNode],
) -> list[str]:
    target = {
        (CriterionOperator.AND, CriterionState.NOT_SATISFIED): CriterionState.NOT_SATISFIED,
        (CriterionOperator.AND, CriterionState.CONFLICT): CriterionState.CONFLICT,
        (CriterionOperator.AND, CriterionState.UNKNOWN): CriterionState.UNKNOWN,
        (CriterionOperator.OR, CriterionState.SATISFIED): CriterionState.SATISFIED,
        (CriterionOperator.OR, CriterionState.CONFLICT): CriterionState.CONFLICT,
        (CriterionOperator.OR, CriterionState.UNKNOWN): CriterionState.UNKNOWN,
        (
            CriterionOperator.AT_LEAST_N,
            CriterionState.SATISFIED,
        ): CriterionState.SATISFIED,
        (
            CriterionOperator.AT_LEAST_N,
            CriterionState.NOT_SATISFIED,
        ): CriterionState.NOT_SATISFIED,
        (
            CriterionOperator.AT_LEAST_N,
            CriterionState.CONFLICT,
        ): CriterionState.CONFLICT,
        (
            CriterionOperator.AT_LEAST_N,
            CriterionState.UNKNOWN,
        ): CriterionState.UNKNOWN,
    }.get((operator, state))
    if target is None:
        return [child.node_id for child in children]
    return [child.node_id for child in children if child.state == target]


def _unknown_assessment(node: ConditionNode, reason: str) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id=node.criterion_id,
        criterion_type=node.criterion_type or "unsupported",
        state=CriterionState.UNKNOWN,
        expected_condition=node.expected_condition,
        reason=reason,
        missing_items=[node.criterion_id],
    )


def assessment_evaluator(
    assessments: Mapping[str, CriterionAssessment],
) -> LeafEvaluator:
    """把已完成的叶评估映射到树；未提供叶子固定为 UNKNOWN."""

    def evaluate(node: ConditionNode) -> CriterionAssessment:
        assessment = assessments.get(node.criterion_id)
        if assessment is None:
            return _unknown_assessment(node, "未找到满足证据策略的结构化事实")
        if assessment.criterion_id != node.criterion_id:
            raise ValueError("criterion assessment 与 condition node ID 不一致")
        return assessment

    return evaluate


def evaluate_condition_tree(
    node: ConditionNode,
    leaf_evaluator: LeafEvaluator,
    *,
    criteria_revision: str = "",
    evaluator_version: str = "1.0.0",
    source_version: str = "",
) -> ProofNode:
    """递归求值，并保留与输入树同构的全部节点和决定性路径."""
    if node.operator == CriterionOperator.LEAF:
        try:
            assessment = leaf_evaluator(node)
        except KeyError:
            assessment = _unknown_assessment(node, "未找到叶子评估")
        if assessment.criterion_id != node.criterion_id:
            raise ValueError("criterion assessment 与 condition node ID 不一致")
        return ProofNode(
            node_id=node.node_id,
            operator=CriterionOperator.LEAF,
            state=assessment.state,
            criterion_type=node.criterion_type,
            criterion_id=node.criterion_id,
            assessment=assessment,
            evaluator_version=evaluator_version,
            criteria_revision=criteria_revision,
            source_version=source_version,
            reason=assessment.reason,
        )

    children = [
        evaluate_condition_tree(
            child,
            leaf_evaluator,
            criteria_revision=criteria_revision,
            evaluator_version=evaluator_version,
            source_version=source_version,
        )
        for child in node.children
    ]
    states = [child.state for child in children]
    state = aggregate_state(node.operator, states, node.threshold)
    lower_bound = upper_bound = None
    if node.operator == CriterionOperator.AT_LEAST_N:
        lower_bound = states.count(CriterionState.SATISFIED)
        upper_bound = lower_bound + states.count(CriterionState.UNKNOWN) + states.count(
            CriterionState.CONFLICT
        )
    return ProofNode(
        node_id=node.node_id,
        operator=node.operator,
        state=state,
        decisive_child_ids=_decisive_child_ids(node.operator, state, children),
        children=children,
        threshold=node.threshold,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        evaluator_version=evaluator_version,
        criteria_revision=criteria_revision,
        source_version=source_version,
        reason=(
            f"lower_bound={lower_bound}, upper_bound={upper_bound}, "
            f"threshold={node.threshold}"
            if node.operator == CriterionOperator.AT_LEAST_N
            else ""
        ),
    )
