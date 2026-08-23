# -*- coding: utf-8 -*-
from __future__ import annotations

from javert.oncology.contracts import (
    AuditDisposition, CriterionAssessment, CriterionState, EligibilityEvaluation,
    EligibilityStatus, EvidenceAnchor, NormalizedFact, ProofNode,
)
from javert.oncology.evidence_adapter import build_oncology_conformance_links


def _evaluation(state=CriterionState.UNKNOWN) -> EligibilityEvaluation:
    assessment = CriterionAssessment(
        criterion_id="criterion-1", criterion_type="diagnosis", state=state,
        normalized_facts=[NormalizedFact(
            fact_type="diagnosis", value="synthetic", normalized_value="SYNTH-DX",
            source="synthetic", anchor=EvidenceAnchor(source="synthetic", locator="row:0"),
        )],
        evidence_anchors=[EvidenceAnchor(source="synthetic", locator="row:0")],
        missing_items=["diagnosis"] if state == CriterionState.UNKNOWN else [],
        evaluator_version="1.0.0", source_version="synthetic-v1",
    )
    return EligibilityEvaluation(
        audit_disposition=AuditDisposition.REVIEW_REQUIRED,
        eligibility_status=EligibilityStatus.DOCUMENTATION_GAP,
        rule_id="SYNTH-RULE", rule_version="1", source_versions=["synthetic-v1"],
        criterion_assessments=[assessment],
        proof_tree=ProofNode(
            node_id="root", operator="leaf", state=state,
            criterion_id=assessment.criterion_id, criterion_type="diagnosis",
            assessment=assessment,
        ),
    )


def test_oncology_adapter_is_one_way_and_does_not_mutate_authority():
    evaluation = _evaluation()
    before = evaluation.model_dump(mode="json")
    links = build_oncology_conformance_links(
        evaluation,
        evidence_refs={("criterion-1", 0): "evidence:public:1"},
        fact_refs={("criterion-1", 0): "fact:public:1"},
    )
    assert evaluation.model_dump(mode="json") == before
    assert evaluation.legacy_verdict == "INCONCLUSIVE"
    assert links.authoritative_legacy_verdict == "INCONCLUSIVE"
    assert links.criterion_links[0].state == "UNKNOWN"
    assert links.criterion_links[0].proof_pointers == ("/proof_tree",)
