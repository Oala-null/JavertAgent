# -*- coding: utf-8 -*-
"""条件树 loader、四态真值表、proof 与去标识金标."""

from __future__ import annotations

import itertools
import json
from datetime import date
from pathlib import Path

import pytest

from javert.oncology.contracts import CriterionAssessment, CriterionState
from javert.oncology.eligibility import (
    ConditionNode,
    aggregate_state,
    evaluate_condition_tree,
    evaluate_rule,
    load_eligibility_rules,
    select_effective_rules,
)
from javert.oncology.knowledge import canonical_json_bytes
from javert.oncology.pathology import (
    BiomarkerMethod,
    PathologyInput,
    evaluate_biomarker_criterion,
    load_pathology_kb,
)
from scripts.build_oncology_eligibility_assets import build_assets, write_assets


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "oncology"


def _assessment(
    criterion_id: str,
    criterion_type: str,
    state: CriterionState,
) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id=criterion_id,
        criterion_type=criterion_type,
        state=state,
    )


def test_and_or_truth_tables_exhaustive():
    states = list(CriterionState)
    for combo in itertools.product(states, repeat=3):
        expected_all = (
            CriterionState.NOT_SATISFIED
            if CriterionState.NOT_SATISFIED in combo
            else CriterionState.CONFLICT
            if CriterionState.CONFLICT in combo
            else CriterionState.UNKNOWN
            if CriterionState.UNKNOWN in combo
            else CriterionState.SATISFIED
        )
        expected_any = (
            CriterionState.SATISFIED
            if CriterionState.SATISFIED in combo
            else CriterionState.NOT_SATISFIED
            if all(state == CriterionState.NOT_SATISFIED for state in combo)
            else CriterionState.CONFLICT
            if CriterionState.CONFLICT in combo
            else CriterionState.UNKNOWN
        )
        assert aggregate_state("all", list(combo)) == expected_all
        assert aggregate_state("any", list(combo)) == expected_any


def test_proof_tree_is_isomorphic_and_keeps_non_decisive_children():
    node = ConditionNode(
        node_id="root",
        kind="all",
        children=[
            ConditionNode(
                node_id="a",
                kind="leaf",
                criterion_id="a",
                criterion_type="diagnosis",
            ),
            ConditionNode(
                node_id="b",
                kind="leaf",
                criterion_id="b",
                criterion_type="prior_therapy",
            ),
            ConditionNode(
                node_id="c",
                kind="leaf",
                criterion_id="c",
                criterion_type="biomarker",
            ),
        ],
    )
    assessments = {
        "a": _assessment("a", "diagnosis", CriterionState.SATISFIED),
        "b": _assessment("b", "prior_therapy", CriterionState.UNKNOWN),
        "c": _assessment("c", "biomarker", CriterionState.NOT_SATISFIED),
    }
    proof = evaluate_condition_tree(
        node,
        lambda leaf: assessments[leaf.criterion_id],
        source_version="1",
    )
    assert proof.state == CriterionState.NOT_SATISFIED
    assert [child.node_id for child in proof.children] == ["a", "b", "c"]
    assert proof.decisive_child_ids == ["c"]
    assert proof.children[1].state == CriterionState.UNKNOWN


def test_repeated_tree_evaluation_is_byte_equivalent():
    node = ConditionNode(
        node_id="root",
        kind="any",
        children=[
            ConditionNode(
                node_id="known",
                kind="leaf",
                criterion_id="known",
                criterion_type="diagnosis",
            ),
            ConditionNode(
                node_id="missing",
                kind="leaf",
                criterion_id="missing",
                criterion_type="prior_therapy",
            ),
        ],
    )
    assessments = {
        "known": _assessment(
            "known", "diagnosis", CriterionState.SATISFIED
        ),
        "missing": _assessment(
            "missing", "prior_therapy", CriterionState.UNKNOWN
        ),
    }
    evaluate = lambda: evaluate_condition_tree(
        node,
        lambda leaf: assessments[leaf.criterion_id],
        source_version="1",
    )
    first = evaluate()
    second = evaluate()
    assert canonical_json_bytes(first.model_dump(mode="json")) == (
        canonical_json_bytes(second.model_dump(mode="json"))
    )
    assert first.decisive_child_ids == ["known"]


def test_eligibility_and_pathology_builder_bytes_are_deterministic(tmp_path: Path):
    outputs = {
        "eligibility_out": tmp_path / "eligibility.json",
        "pathology_out": tmp_path / "pathology.json",
        "review_out": tmp_path / "review.json",
        "pathology_review_out": tmp_path / "pathology-review.json",
    }
    first_paths = write_assets(**outputs)
    first = {path.name: path.read_bytes() for path in first_paths}
    second_paths = write_assets(**outputs)
    assert {path.name: path.read_bytes() for path in second_paths} == first


def test_assets_load_checksum_effective_date_and_approval(tmp_path: Path):
    eligibility_path = tmp_path / "eligibility.json"
    pathology_path = tmp_path / "pathology.json"
    write_assets(
        eligibility_out=eligibility_path,
        pathology_out=pathology_path,
        review_out=tmp_path / "review.json",
        pathology_review_out=tmp_path / "pathology-review.json",
    )
    asset = load_eligibility_rules(eligibility_path)
    before_effective = select_effective_rules(
        asset,
        drug_concept_id="disitamab-vedotin",
        service_date=date(2025, 6, 18),
    )
    assert before_effective.rules == []

    selection = select_effective_rules(
        asset,
        drug_concept_id="disitamab-vedotin",
        service_date=date(2026, 6, 18),
    )
    assert [rule.indication_branch_id for rule in selection.rules] == [
        "urothelial-prior-platinum-her2"
    ]
    assert selection.rules[0].metadata.effective_from == date(2026, 1, 1)
    assert selection.rules[0].metadata.effective_to == date(2027, 12, 31)
    assert selection.data_quality_flags == []

    pola = select_effective_rules(
        asset,
        drug_concept_id="polatuzumab-vedotin",
        service_date=date(2025, 6, 18),
    )
    assert len(pola.rules) == 2
    assert {
        (rule.metadata.effective_from, rule.metadata.effective_to)
        for rule in pola.rules
    } == {(date(2025, 1, 1), date(2026, 12, 31))}

    raw = json.loads(eligibility_path.read_text(encoding="utf-8"))
    raw["entries"][0]["raw_restriction"] += "tampered"
    eligibility_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        load_eligibility_rules(eligibility_path)


def test_unapproved_rule_is_blocked_from_auto_decision(tmp_path: Path):
    eligibility_path = tmp_path / "eligibility.json"
    paths = write_assets(
        eligibility_out=eligibility_path,
        pathology_out=tmp_path / "pathology.json",
        review_out=tmp_path / "review.json",
        pathology_review_out=tmp_path / "pathology-review.json",
    )
    raw = json.loads(paths[0].read_text(encoding="utf-8"))
    raw["entries"][0]["metadata"]["review_status"] = "needs_review"
    from javert.oncology.knowledge import asset_payload_checksum

    raw["metadata"]["checksum"] = asset_payload_checksum(raw)
    paths[0].write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    asset = load_eligibility_rules(paths[0])
    selection = select_effective_rules(
        asset,
        drug_concept_id="disitamab-vedotin",
        service_date=date(2026, 6, 18),
    )
    assert selection.rules == []
    assert selection.blocked_rule_ids == ["elig-disitamab-urothelial-2026"]
    assert "UNAPPROVED_RULE_VERSION" in selection.data_quality_flags


def test_urothelial_golden_pathology_and_mandatory_branch(tmp_path: Path):
    fixture = json.loads(
        (FIXTURE_DIR / "urothelial_her2_low.json").read_text(encoding="utf-8")
    )
    eligibility_path = tmp_path / "eligibility.json"
    pathology_path = tmp_path / "pathology.json"
    write_assets(
        eligibility_out=eligibility_path,
        pathology_out=pathology_path,
        review_out=tmp_path / "review.json",
        pathology_review_out=tmp_path / "pathology-review.json",
    )
    eligibility_asset = load_eligibility_rules(eligibility_path)
    pathology_asset = load_pathology_kb(pathology_path)
    rule = select_effective_rules(
        eligibility_asset,
        drug_concept_id="disitamab-vedotin",
        service_date=date.fromisoformat(fixture["service_date"]),
    ).rules[0]
    raw_pathology = fixture["pathology_observations"][0]
    her2 = evaluate_biomarker_criterion(
        criterion_id="urothelial-her2-overexpression",
        marker_id="HER2",
        cancer_context="尿路上皮癌",
        policy_context="disitamab-urothelial-insurance",
        expected_method=BiomarkerMethod.IHC,
        inputs=[
            PathologyInput(
                text=raw_pathology["text"],
                locator=raw_pathology["anchor"],
                specimen_site=raw_pathology["specimen_site"],
                specimen_date=date.fromisoformat(raw_pathology["specimen_date"]),
                report_date=date.fromisoformat(raw_pathology["report_date"]),
            )
        ],
        service_date=date.fromisoformat(fixture["service_date"]),
        asset=pathology_asset,
    )
    assert her2.state == CriterionState.NOT_SATISFIED
    normalized = her2.normalized_facts[0].normalized_value
    assert normalized["marker_id"] == "HER2"
    assert normalized["method"] == "IHC"
    assert normalized["score"] == "1+"

    assessments = {
        "urothelial-diagnosis": _assessment(
            "urothelial-diagnosis", "diagnosis", CriterionState.SATISFIED
        ),
        "urothelial-locally-advanced": _assessment(
            "urothelial-locally-advanced", "stage", CriterionState.NOT_SATISFIED
        ),
        "urothelial-metastatic": _assessment(
            "urothelial-metastatic", "stage", CriterionState.SATISFIED
        ),
        "urothelial-prior-platinum": _assessment(
            "urothelial-prior-platinum", "prior_therapy", CriterionState.UNKNOWN
        ),
        "urothelial-her2-overexpression": her2,
    }
    result = evaluate_rule(rule, assessments)
    assert result.eligibility_status.value == "NOT_SATISFIED"
    assert result.audit_disposition.value == "VIOLATION_FOUND"
    assert result.legacy_verdict == "VIOLATION"
    states = {item.criterion_id: item.state for item in result.criterion_assessments}
    assert states["urothelial-prior-platinum"] == CriterionState.UNKNOWN
    assert result.proof_tree.state == CriterionState.NOT_SATISFIED


def test_builder_covers_every_active_insurance_restriction():
    _, pathology, review, pathology_review = build_assets()
    assert review["source_restriction_count"] == (
        review["fully_approved_source_restriction_count"]
        + review["partially_approved_source_restriction_count"]
        + review["needs_review_only_source_restriction_count"]
    )
    assert review["covered_source_restriction_count"] == review[
        "source_restriction_count"
    ]
    assert review["branch_count"] == 158
    assert review["approved_branch_count"] == 3
    assert review["needs_review_branch_count"] == 155
    assert review["partially_approved_source_restriction_count"] == 1
    assert all(review["invariants"].values())
    assert {
        item["generic_name"]: item["status"]
        for item in review["manual_source_validations"]
    } == {
        "注射用维迪西妥单抗": "matched",
        "注射用维泊妥珠单抗": "matched",
    }
    assert all(
        item["review_status"] == "needs_review"
        for item in review["needs_review_branches"]
    )
    gastric = [
        item
        for item in review["needs_review_branches"]
        if item["branch_id"] == "gastric-prior-two-systemic-her2"
    ]
    assert len(gastric) == 1
    assert "至少2个系统化疗" in gastric[0]["reasons"][0]

    approved_ids = {
        entry["entry_id"]
        for entry in pathology["entries"]
        if entry["metadata"]["review_status"] == "approved"
    }
    assert approved_ids == {"her2-urothelial-disitamab-ihc"}
    assert pathology_review["needs_review_entry_count"] == len(
        pathology_review["needs_review_entries"]
    )
    assert all(pathology_review["invariants"].values())
    gastric_her2 = next(
        entry
        for entry in pathology["entries"]
        if entry["entry_id"] == "her2-gastric-disitamab-ihc-needs-review"
    )
    assert gastric_her2["metadata"]["review_status"] == "needs_review"
    assert gastric_her2["metadata"]["effective_from"] == "2026-01-01"
    assert gastric_her2["metadata"]["effective_to"] == "2027-12-31"
    assert gastric_her2["policy_context"] == "disitamab-gastric-insurance"


def test_builder_rejects_unmanifested_polatuzumab_branch(tmp_path: Path):
    source = json.loads(
        (ROOT / "configs" / "oncology_drug_kb.json").read_text(
            encoding="utf-8"
        )
    )
    drug = source["drugs"]["注射用维泊妥珠单抗"]
    entry = next(
        item
        for item in drug["entries"]
        if item.get("source_type") == "insurance"
        and item.get("rule_type") == "限适应症"
    )
    entry["basis"] = entry["basis"].rstrip("。") + ";3.新增未审核适应症。"
    source_path = tmp_path / "oncology_drug_kb.json"
    source_path.write_text(
        json.dumps(source, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="branch manifest 不一致.*拒绝生成 approved",
    ):
        build_assets(source_path)
