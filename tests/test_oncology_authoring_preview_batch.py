from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from javert.audit.result import AuditResult
from javert.oncology.authoring.preview import (
    evaluate_authoring_preview_rule,
    load_authoring_preview,
)
from javert.oncology.contracts import AuditDisposition
from scripts.run_oncology_authoring_preview_batch import (
    _CohortCandidate,
    _aggregate_patient_preview,
    _build_preview_audit_result,
    _choose_cohort_candidates,
    _diagnosis_profile,
    _prepare_preview_evaluation,
    _sy_homepage_patient_ids,
)


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "docs/oncology/authoring/oncology_authoring_candidates.json"
PATHOLOGY = ROOT / "configs/pathology_biomarker_kb.json"


def test_diagnosis_profile_requires_malignancy_and_identifies_lung_cancer() -> None:
    assert _diagnosis_profile([{"name": "肺腺癌", "code": ""}]) == (True, True)
    assert _diagnosis_profile([{"name": "肺部占位", "code": "C34.90"}]) == (
        True,
        True,
    )
    assert _diagnosis_profile([{"name": "乳腺恶性肿瘤", "code": "C50.9"}]) == (
        True,
        False,
    )
    assert _diagnosis_profile([{"name": "肺部结节", "code": "R91.1"}]) == (
        False,
        False,
    )
    assert _diagnosis_profile([{"name": "宫颈癌前病变", "code": "N87.9"}]) == (
        False,
        False,
    )


def test_cohort_selection_prefers_lung_cancer_then_drug_diversity() -> None:
    candidates = [
        _CohortCandidate("lung-a", frozenset({"drug-a"}), True),
        _CohortCandidate("lung-a2", frozenset({"drug-a"}), True),
        _CohortCandidate("lung-b", frozenset({"drug-b"}), True),
        _CohortCandidate("breast-c", frozenset({"drug-c"}), False),
    ]

    selected = _choose_cohort_candidates(candidates, limit=3)

    assert [item.patient_id for item in selected] == [
        "lung-a",
        "lung-b",
        "lung-a2",
    ]


def test_cohort_selection_fills_with_other_oncology_patients() -> None:
    candidates = [
        _CohortCandidate("breast-a", frozenset({"drug-a"}), False),
        _CohortCandidate("lung-b", frozenset({"drug-b"}), True),
        _CohortCandidate("lymphoma-c", frozenset({"drug-c"}), False),
    ]

    selected = _choose_cohort_candidates(candidates, limit=3)

    assert selected[0].patient_id == "lung-b"
    assert {item.patient_id for item in selected} == {
        "breast-a",
        "lung-b",
        "lymphoma-c",
    }


def test_sy_homepage_filter_requires_nonempty_main_diagnosis_query() -> None:
    class _Cursor:
        sql = ""
        params: tuple[str, ...] = ()

        def execute(self, sql: str, params: tuple[str, ...]):
            self.sql = sql
            self.params = params
            return self

        def fetchall(self):
            return [(" sy-lung ",), ("",)]

    class _Connection:
        def __init__(self):
            self.cursor_instance = _Cursor()

        def cursor(self):
            return self.cursor_instance

    connection = _Connection()

    selected = _sy_homepage_patient_ids(connection, ["sy-lung", "ih-only"])

    assert selected == {"sy-lung"}
    assert "TB_BA_SYJBK" in connection.cursor_instance.sql
    assert "ZYZD" in connection.cursor_instance.sql
    assert connection.cursor_instance.params == ("sy-lung", "ih-only")


def test_patient_preview_aggregates_to_one_persistable_draft_result() -> None:
    preview = load_authoring_preview(CANDIDATES)
    rule = next(
        item
        for item in preview.rules
        if item.raw_restriction.startswith("华氏巨球蛋白血症患者")
    )
    service_date = date(2026, 7, 1)
    branch = evaluate_authoring_preview_rule(
        rule,
        diagnoses=[{"name": "华氏巨球蛋白血症", "code": "C88.0"}],
        records=[],
        regimens=[],
        service_date=service_date,
        pathology_path=PATHOLOGY,
        cancer_context="华氏巨球蛋白血症",
    )
    prepared = _prepare_preview_evaluation(
        branch,
        snapshot_checksum=preview.snapshot_checksum,
        service_date=service_date,
    )
    concept_id = str(prepared.drug_concept_id)
    aggregate = _aggregate_patient_preview(
        {(concept_id, "INSURANCE_PAYMENT"): [prepared]}
    )
    result = _build_preview_audit_result(
        patient_id="synthetic-oncology-patient",
        evaluation=aggregate,
        drug_names_by_concept={concept_id: "合成肿瘤药"},
        started_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        duration_ms=12,
    )

    assert result.rule_id == "RD04"
    assert result.verdict == "INCONCLUSIVE"
    assert aggregate.audit_disposition == AuditDisposition.REVIEW_REQUIRED
    assert len(aggregate.scope_evaluations) == 1
    assert aggregate.scope_evaluations[0].release_id.startswith("draft-preview-")
    assert "草稿知识预览" in result.reasoning
    assert any(item.source == "drug_audit_lookup" for item in result.evidence)
    assert all(
        "NO_APPROVED_ELIGIBILITY_RULE" not in item.data_quality_flags
        for item in aggregate.scope_evaluations
    )
    assert AuditResult.model_validate(result.model_dump(mode="json")) == result
