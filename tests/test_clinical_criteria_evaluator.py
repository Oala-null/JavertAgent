# -*- coding: utf-8 -*-
"""通用临床条件树的四态真值表、proof 同构和边界测试."""

from __future__ import annotations

import itertools

import pytest
from pydantic import ValidationError

from javert.clinical_criteria.contracts import (
    ConditionNode,
    CriterionAssessment,
    CriterionOperator,
    CriterionState,
    EvidenceAnchor,
    Observation,
    ProofNode,
)
from javert.clinical_criteria.evaluator import (
    aggregate_state,
    assessment_evaluator,
    evaluate_condition_tree,
)


def _assessment(criterion_id: str, state: CriterionState) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id=criterion_id,
        criterion_type="synthetic",
        state=state,
        reason=f"{criterion_id}:{state}",
    )


def _expected_and(states: tuple[CriterionState, ...]) -> CriterionState:
    if CriterionState.NOT_SATISFIED in states:
        return CriterionState.NOT_SATISFIED
    if CriterionState.CONFLICT in states:
        return CriterionState.CONFLICT
    if CriterionState.UNKNOWN in states:
        return CriterionState.UNKNOWN
    return CriterionState.SATISFIED


def _expected_or(states: tuple[CriterionState, ...]) -> CriterionState:
    if CriterionState.SATISFIED in states:
        return CriterionState.SATISFIED
    if CriterionState.CONFLICT in states:
        return CriterionState.CONFLICT
    if CriterionState.UNKNOWN in states:
        return CriterionState.UNKNOWN
    return CriterionState.NOT_SATISFIED


def _expected_at_least_n(
    states: tuple[CriterionState, ...], threshold: int
) -> CriterionState:
    lower = states.count(CriterionState.SATISFIED)
    upper = lower + states.count(CriterionState.UNKNOWN) + states.count(
        CriterionState.CONFLICT
    )
    if lower >= threshold:
        return CriterionState.SATISFIED
    if upper < threshold:
        return CriterionState.NOT_SATISFIED
    if CriterionState.CONFLICT in states:
        return CriterionState.CONFLICT
    return CriterionState.UNKNOWN


def test_and_or_truth_tables_are_exhaustive() -> None:
    states = tuple(CriterionState)
    for combination in itertools.product(states, repeat=3):
        assert aggregate_state("AND", combination) == _expected_and(combination)
        assert aggregate_state("OR", combination) == _expected_or(combination)


def test_at_least_n_truth_table_is_exhaustive_for_all_thresholds() -> None:
    states = tuple(CriterionState)
    for child_count in range(1, 5):
        for combination in itertools.product(states, repeat=child_count):
            for threshold in range(1, child_count + 1):
                assert aggregate_state(
                    "AT_LEAST_N", combination, threshold
                ) == _expected_at_least_n(combination, threshold)


@pytest.mark.parametrize(
    ("operator", "states", "threshold"),
    [
        ("AND", [], None),
        ("OR", [], None),
        ("AT_LEAST_N", [], 1),
        ("AT_LEAST_N", [CriterionState.UNKNOWN], None),
        ("AT_LEAST_N", [CriterionState.UNKNOWN], 0),
        ("AT_LEAST_N", [CriterionState.UNKNOWN], 2),
        ("AT_LEAST_N", [CriterionState.UNKNOWN], True),
        ("AND", [CriterionState.SATISFIED], 1),
        ("OR", [CriterionState.SATISFIED], 1),
    ],
)
def test_aggregate_rejects_invalid_boundaries(operator, states, threshold) -> None:
    with pytest.raises(ValueError):
        aggregate_state(operator, states, threshold)


def test_proof_tree_is_isomorphic_and_retains_count_bounds() -> None:
    tree = ConditionNode(
        node_id="root",
        operator="AND",
        children=[
            ConditionNode(
                node_id="diagnosis",
                operator="LEAF",
                criterion_id="diagnosis",
                criterion_type="diagnosis",
            ),
            ConditionNode(
                node_id="alternatives",
                operator="OR",
                children=[
                    ConditionNode(
                        node_id="pathology",
                        operator="LEAF",
                        criterion_id="pathology",
                        criterion_type="pathology",
                    ),
                    ConditionNode(
                        node_id="symptom-count",
                        operator="AT_LEAST_N",
                        threshold=2,
                        children=[
                            ConditionNode(
                                node_id=criterion_id,
                                operator="LEAF",
                                criterion_id=criterion_id,
                                criterion_type="symptom",
                            )
                            for criterion_id in ("s1", "s2", "s3")
                        ],
                    ),
                ],
            ),
        ],
    )
    assessments = {
        "diagnosis": _assessment("diagnosis", CriterionState.SATISFIED),
        "pathology": _assessment("pathology", CriterionState.NOT_SATISFIED),
        "s1": _assessment("s1", CriterionState.SATISFIED),
        "s2": _assessment("s2", CriterionState.UNKNOWN),
        "s3": _assessment("s3", CriterionState.CONFLICT),
    }

    proof = evaluate_condition_tree(
        tree,
        assessment_evaluator(assessments),
        criteria_revision="CD-test@1",
    )

    assert proof.state == CriterionState.CONFLICT
    assert proof.decisive_child_ids == ["alternatives"]
    assert [child.node_id for child in proof.children] == ["diagnosis", "alternatives"]
    alternatives = proof.children[1]
    assert [child.node_id for child in alternatives.children] == [
        "pathology",
        "symptom-count",
    ]
    count = alternatives.children[1]
    assert count.operator == CriterionOperator.AT_LEAST_N
    assert count.state == CriterionState.CONFLICT
    assert count.decisive_child_ids == ["s3"]
    assert (count.threshold, count.lower_bound, count.upper_bound) == (2, 1, 3)
    assert [child.node_id for child in count.children] == ["s1", "s2", "s3"]
    assert all(node.criteria_revision == "CD-test@1" for node in count.children)


@pytest.mark.parametrize(
    ("operator", "states", "expected_state", "expected_decisive"),
    [
        (
            "AND",
            [CriterionState.SATISFIED, CriterionState.UNKNOWN, CriterionState.NOT_SATISFIED],
            CriterionState.NOT_SATISFIED,
            ["c2"],
        ),
        (
            "OR",
            [CriterionState.NOT_SATISFIED, CriterionState.CONFLICT, CriterionState.SATISFIED],
            CriterionState.SATISFIED,
            ["c2"],
        ),
        (
            "AT_LEAST_N",
            [CriterionState.SATISFIED, CriterionState.NOT_SATISFIED, CriterionState.NOT_SATISFIED],
            CriterionState.NOT_SATISFIED,
            ["c1", "c2"],
        ),
    ],
)
def test_proof_marks_only_state_decisive_children(
    operator, states, expected_state, expected_decisive
) -> None:
    children = [
        ConditionNode(
            node_id=f"c{index}",
            operator="LEAF",
            criterion_id=f"c{index}",
            criterion_type="synthetic",
        )
        for index in range(len(states))
    ]
    tree = ConditionNode(
        node_id="root",
        operator=operator,
        threshold=2 if operator == "AT_LEAST_N" else None,
        children=children,
    )
    proof = evaluate_condition_tree(
        tree,
        assessment_evaluator(
            {
                child.criterion_id: _assessment(child.criterion_id, state)
                for child, state in zip(children, states, strict=True)
            }
        ),
    )
    assert proof.state == expected_state
    assert proof.decisive_child_ids == expected_decisive
    assert len(proof.children) == len(children)


def test_missing_leaf_assessment_is_unknown_not_negative() -> None:
    tree = ConditionNode(
        node_id="missing",
        operator="LEAF",
        criterion_id="missing",
        criterion_type="examination",
    )
    proof = evaluate_condition_tree(tree, assessment_evaluator({}))
    assert proof.state == CriterionState.UNKNOWN
    assert proof.assessment is not None
    assert proof.assessment.missing_items == ["missing"]


def test_contracts_reject_invalid_tree_and_proof_shapes() -> None:
    with pytest.raises(ValidationError, match="threshold"):
        ConditionNode(node_id="broken", operator="AT_LEAST_N", children=[], threshold=1)

    assessment = _assessment("leaf", CriterionState.SATISFIED)
    leaf = ProofNode(
        node_id="leaf",
        operator="leaf",
        state=CriterionState.SATISFIED,
        criterion_id="leaf",
        criterion_type="synthetic",
        assessment=assessment,
    )
    with pytest.raises(ValidationError, match="上下界"):
        ProofNode(
            node_id="count",
            operator="at_least_n",
            state=CriterionState.SATISFIED,
            threshold=1,
            lower_bound=0,
            upper_bound=1,
            children=[leaf],
        )


def test_observation_accepts_legacy_names_without_losing_anchor() -> None:
    observation = Observation(
        fact_type="laboratory",
        value="7.0",
        normalized_value="7.0",
        source="lab",
        observation_date="2026-01-02",
        anchor=EvidenceAnchor(source="lab", locator="row:synthetic"),
        method="structured",
    )
    assert observation.raw_value == "7.0"
    assert observation.source_domain == "lab"
    assert observation.evidence_anchor is not None
    assert observation.extraction_method == "structured"
