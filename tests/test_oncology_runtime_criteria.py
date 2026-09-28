from __future__ import annotations

from datetime import date
from pathlib import Path

from javert.oncology.contracts import CriterionState
from javert.oncology.eligibility import ConditionNode, EligibilityRule
from javert.oncology.knowledge import KnowledgeEntryMetadata, ReviewStatus, SourceReference
from javert.oncology.runtime import _evaluate_rule_leaves


ROOT = Path(__file__).resolve().parents[1]
PATHOLOGY = ROOT / "configs" / "pathology_biomarker_kb.json"


def _leaf(criterion_type: str, expected: dict) -> ConditionNode:
    return ConditionNode(
        node_id=f"node-{criterion_type}",
        kind="leaf",
        criterion_id=f"criterion-{criterion_type}",
        criterion_type=criterion_type,
        expected=expected,
    )


def test_every_first_phase_runtime_criterion_has_deterministic_dispatch() -> None:
    leaves = [
        _leaf("diagnosis", {"includes": ["肺腺癌"]}),
        _leaf("histology", {"includes": ["腺癌"]}),
        _leaf("stage", {"equals": "locally_advanced"}),
        _leaf("disease_status", {"equals": "progressive_phenotype"}),
        _leaf("resectability", {"equals": "unresectable"}),
        _leaf("age", {"gte": 18}),
        _leaf("sex", {"equals": "female"}),
        _leaf("menopausal_status", {"equals": "postmenopausal"}),
        _leaf("prior_therapy", {"exists": True}),
        _leaf("therapy_count", {"gte": 2}),
        _leaf("line_of_therapy", {"equals": 2}),
        _leaf("treatment_status", {"equals": "progressed"}),
        _leaf(
            "combination_requirement",
            {
                "target_kind": "CONCEPT",
                "target_id": "synthetic-companion",
                "display_name": "合成联合药",
                "requirement": "OPTIONAL",
            },
        ),
        _leaf("surgery_status", {"exists": True}),
        _leaf("radiotherapy_status", {"exists": True}),
        _leaf("transplant_eligibility", {"not_equals": "eligible"}),
        _leaf("time_window", {"within_days": 365}),
    ]
    rule = EligibilityRule(
        rule_id="synthetic-all-criteria",
        drug_concept_id="synthetic-drug",
        indication_branch_id="synthetic-branch",
        version="1.0.0",
        raw_restriction="合成全类型条件",
        metadata=KnowledgeEntryMetadata(
            content_version="1.0.0",
            effective_from=date(2026, 1, 1),
            effective_to=date(2027, 12, 31),
            source_refs=[
                SourceReference(
                    source_id="synthetic-source",
                    title="合成来源",
                    version="1",
                    retrieval_date=date(2026, 7, 21),
                    checksum="sha256:" + "1" * 64,
                )
            ],
            review_status=ReviewStatus.APPROVED,
        ),
        condition_tree=ConditionNode(
            node_id="root",
            kind="all",
            children=leaves,
        ),
    )
    records = [
        {
            "text": (
                "女性，62岁，绝经后，肺腺癌，局部晚期不可切除，具有进行性表型。"
                "既往接受过3种系统性治疗，二线治疗后疾病进展。"
                "经手术切除后接受过放疗，因评估不适合移植。"
            ),
            "section": "病程记录",
            "date": date(2026, 6, 1),
            "locator": "note:1",
        }
    ]
    assessments = _evaluate_rule_leaves(
        rule,
        diagnoses=[{"name": "肺腺癌", "code": "C34"}],
        records=records,
        regimens=[],
        service_date=date(2026, 6, 30),
        pathology_path=PATHOLOGY,
        cancer_context="肺腺癌",
    )

    assert set(assessments) == {leaf.criterion_id for leaf in leaves}
    assert all("不支持的确定性条件类型" not in item.reason for item in assessments.values())
    non_satisfied = sorted(
        f"{key}:{item.state.value}:{item.reason}"
        for key, item in assessments.items()
        if item.state != CriterionState.SATISFIED
    )
    assert not non_satisfied
