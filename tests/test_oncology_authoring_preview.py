from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from javert.oncology.authoring.preview import (
    evaluate_authoring_preview_rule,
    load_authoring_preview,
)
from javert.oncology.contracts import (
    AuditDisposition,
    EligibilityEvaluation,
    EligibilityStatus,
)


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "docs/oncology/authoring/oncology_authoring_candidates.json"
PATHOLOGY = ROOT / "configs/pathology_biomarker_kb.json"


def test_full_authoring_preview_compiles_without_approving_candidates() -> None:
    preview = load_authoring_preview(CANDIDATES)
    expected_branch_count = len(json.loads(CANDIDATES.read_text())["branches"])
    assert len(preview.rules) == expected_branch_count
    assert len({rule.metadata.rule_revision_id for rule in preview.rules}) == 221
    assert all(rule.metadata.review_status.value == "needs_review" for rule in preview.rules)
    assert any(
        leaf.criterion_type != "unsupported"
        for rule in preview.rules
        for leaf in _leaves(rule.condition_tree)
    )
    assert any(
        leaf.criterion_type == "unsupported"
        for rule in preview.rules
        for leaf in _leaves(rule.condition_tree)
    )


def _leaves(node):
    if node.kind == "leaf":
        return [node]
    return [leaf for child in node.children for leaf in _leaves(child)]


def test_preview_evaluates_real_tree_but_never_auto_adjudicates() -> None:
    preview = load_authoring_preview(CANDIDATES)
    rule = next(
        item
        for item in preview.rules
        if item.raw_restriction.startswith("华氏巨球蛋白血症患者")
    )
    result = evaluate_authoring_preview_rule(
        rule,
        diagnoses=[{"name": "华氏巨球蛋白血症", "code": "C88.0"}],
        records=[],
        regimens=[],
        service_date=date(2026, 7, 1),
        pathology_path=PATHOLOGY,
        cancer_context="华氏巨球蛋白血症",
    )
    assert result.eligibility_status == EligibilityStatus.SATISFIED
    assert result.audit_disposition == AuditDisposition.REVIEW_REQUIRED
    assert result.legacy_verdict == "INCONCLUSIVE"
    assert EligibilityEvaluation.model_validate(result.model_dump()) == result
    assert "DRAFT_RULE_PREVIEW_ONLY" in result.data_quality_flags
    assert "NO_APPROVED_ELIGIBILITY_RULE" not in result.data_quality_flags


def test_preview_resolves_oncology_drug_by_name_without_ambiguous_guess() -> None:
    preview = load_authoring_preview(CANDIDATES)
    concept_ids = preview.concept_ids_for_match(
        generic_name="替雷利珠单抗注射液"
    )
    assert len(concept_ids) == 1
    rules = preview.rules_for_concept(
        concept_ids[0], policy_scope="INSURANCE_PAYMENT"
    )
    assert len(rules) >= 10


def test_every_authoring_branch_runs_fail_closed_without_patient_evidence() -> None:
    preview = load_authoring_preview(CANDIDATES)
    expected_branch_count = len(json.loads(CANDIDATES.read_text())["branches"])
    results = [
        evaluate_authoring_preview_rule(
            rule,
            diagnoses=[],
            records=[],
            regimens=[],
            service_date=date(2026, 7, 1),
            pathology_path=PATHOLOGY,
            cancer_context="",
        )
        for rule in preview.rules
    ]
    assert len(results) == expected_branch_count
    assert all(
        item.audit_disposition == AuditDisposition.REVIEW_REQUIRED
        for item in results
    )
    assert all("DRAFT_RULE_PREVIEW_ONLY" in item.data_quality_flags for item in results)
    assert all("NO_APPROVED_ELIGIBILITY_RULE" not in item.data_quality_flags for item in results)
