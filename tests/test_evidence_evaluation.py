# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from javert.evidence.evaluation import (
    AcceptanceProfile, ArmObservation, ArmVersion, EvaluationCase, EvaluationPackage,
    EvaluationStatus, ExpertAdjudication, build_blinded_packets, build_evaluation_plan,
    default_thresholds, evaluate_ab, evaluation_plan_checksum, persist_evaluation_package,
)
from javert.evidence.serialization import checksum_value

NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)
SNAPSHOT = checksum_value({"snapshot": "synthetic-oncology-v1"})
CONFIG = checksum_value({"config": "synthetic"})


def _plan(profile: AcceptanceProfile = AcceptanceProfile.CONFORMANCE, repetitions: int = 2):
    return build_evaluation_plan(
        plan_id=f"plan:{profile.value.lower()}:1", plan_version="1", created_at=NOW,
        source_snapshot_checksum=SNAPSHOT, candidate_query_version="synthetic-v1",
        analysis_unit="patient", repetitions=repetitions, blinded=True,
        harm_matrix_version="synthetic-harm-v1",
        harm_matrix={
            "VIOLATION|CLEAN": 1.0, "VIOLATION|INCONCLUSIVE": 0.75,
            "CLEAN|VIOLATION": 1.0, "CLEAN|INCONCLUSIVE": 0.75,
            "INCONCLUSIVE|VIOLATION": 0.25, "INCONCLUSIVE|CLEAN": 0.25,
        },
        profile=profile, thresholds=default_thresholds(profile),
        signer_roles=("clinical", "insurance", "engineering"),
        arms=(
            ArmVersion(
                arm_id="A", system_version="legacy", code_commit="abcdef1",
                config_checksum=CONFIG, model_version="legacy-model", tool_version="legacy-tools",
                ontology_version="none", knowledge_version="legacy-kb",
            ),
            ArmVersion(
                arm_id="B", system_version="evidence-v0.1", code_commit="abcdef1",
                config_checksum=CONFIG, model_version="deterministic", tool_version="structured-tools",
                ontology_version="ontology-1", knowledge_version="kb-1",
            ),
        ),
    )


def _case(case_id: str = "case:synthetic:1") -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id, source_snapshot_checksum=SNAPSHOT,
        analysis_unit_ref=case_id, stratum="synthetic-oncology",
    )


def _observations(*, a="VIOLATION", b="INCONCLUSIVE", repetitions: int = 2):
    rows = []
    for repetition in range(1, repetitions + 1):
        for arm, outcome in (("A", a), ("B", b)):
            rows.append(ArmObservation(
                observation_id=f"obs:case:synthetic:1:{arm}:{repetition}",
                case_id="case:synthetic:1", arm_id=arm, repetition=repetition,
                source_snapshot_checksum=SNAPSHOT, outcome=outcome,
                canonical_result_checksum=checksum_value({"arm": arm, "outcome": outcome}),
                decisive_count=1, grounded_decisive_count=1,
                resolvable_locator_count=1, provenance_complete=True,
                proof_complete=arm == "B",
            ))
    return tuple(rows)


def _reviews(reference="INCONCLUSIVE"):
    return tuple(
        ExpertAdjudication(
            review_id=f"review:{kind.lower()}:{reviewer}", case_id="case:synthetic:1",
            reviewer_ref=reviewer, kind=kind, outcome=reference, blinded=True,
            recorded_at=NOW,
        )
        for kind, reviewer in (("INDEPENDENT", "expert-1"), ("INDEPENDENT", "expert-2"), ("FINAL", "adjudicator"))
    )


def test_conformance_report_is_deterministic_and_has_no_scalar_score():
    plan = _plan()
    kwargs = dict(
        evaluation_id="evaluation:synthetic:1", plan=plan, cases=(_case(),),
        observations=_observations(), adjudications=_reviews(),
    )
    first = evaluate_ab(**kwargs)
    second = evaluate_ab(**kwargs)
    assert first == second
    assert first.status == EvaluationStatus.PASS
    assert first.deployment_authorized is False
    assert "score" not in first.model_dump()
    assert any(metric.metric_id == "paired_harm_loss_diff" for metric in first.metrics)


def test_all_abstain_exposes_unnecessary_abstention_not_fake_safety():
    report = evaluate_ab(
        evaluation_id="evaluation:abstain:1", plan=_plan(), cases=(_case(),),
        observations=_observations(a="INCONCLUSIVE", b="INCONCLUSIVE"),
        adjudications=_reviews(reference="CLEAN"),
    )
    metric = next(item for item in report.metrics if item.metric_id == "unnecessary_abstention" and item.arm_id == "B")
    assert metric.value == 1.0


def test_shadow_profile_with_small_sample_is_insufficient_not_pass():
    plan = _plan(AcceptanceProfile.SHADOW, repetitions=2)
    report = evaluate_ab(
        evaluation_id="evaluation:small:1", plan=plan, cases=(_case(),),
        observations=_observations(), adjudications=_reviews(),
    )
    assert report.status == EvaluationStatus.INSUFFICIENT_EVIDENCE


def test_missing_arm_or_snapshot_drift_is_invalid():
    plan = _plan()
    only_a = tuple(item for item in _observations() if item.arm_id == "A")
    report = evaluate_ab(
        evaluation_id="evaluation:invalid:1", plan=plan, cases=(_case(),),
        observations=only_a,
    )
    assert report.status == EvaluationStatus.INVALID
    assert any(issue.code == "EVALUATION_ARM_OR_REPETITION_MISSING" for issue in report.issues)


def test_arm_version_drift_without_new_plan_checksum_is_invalid():
    plan = _plan()
    changed_b = plan.arms[1].model_copy(update={"tool_version": "changed-tool"})
    drifted = plan.model_copy(update={"arms": (plan.arms[0], changed_b)})
    report = evaluate_ab(
        evaluation_id="evaluation:version-drift:1", plan=drifted,
        cases=(_case(),), observations=_observations(), adjudications=_reviews(),
    )
    assert report.status == EvaluationStatus.INVALID
    assert any(issue.code == "EVALUATION_PLAN_CHECKSUM_MISMATCH" for issue in report.issues)


def test_false_clean_regression_is_not_offset_by_other_metrics():
    report = evaluate_ab(
        evaluation_id="evaluation:false-clean:1", plan=_plan(), cases=(_case(),),
        observations=_observations(a="VIOLATION", b="CLEAN"),
        adjudications=_reviews(reference="VIOLATION"),
    )
    assert report.status == EvaluationStatus.FAIL
    assert next(
        gate for gate in report.gates if gate.gate_id == "clinical_safety_and_utility"
    ).status == "FAIL"


def test_repeat_drift_is_visible_and_missing_class_recall_is_not_estimable():
    observations = list(_observations(a="VIOLATION", b="INCONCLUSIVE"))
    observations[1] = observations[1].model_copy(update={
        "canonical_result_checksum": checksum_value({"drift": True}),
    })
    report = evaluate_ab(
        evaluation_id="evaluation:drift:1", plan=_plan(), cases=(_case(),),
        observations=tuple(observations), adjudications=_reviews(reference="INCONCLUSIVE"),
    )
    assert report.status == EvaluationStatus.FAIL
    b_stability = next(
        metric for metric in report.metrics
        if metric.metric_id == "canonical_repeat_stability" and metric.arm_id == "B"
    )
    assert b_stability.value == 0.0
    missing_recall = next(
        metric for metric in report.metrics
        if metric.metric_id == "recall_violation" and metric.arm_id == "B"
    )
    assert missing_recall.status.value == "not_estimable"


def test_legacy_repeat_drift_is_reported_without_hiding_modal_uncertainty():
    observations = list(_observations(a="VIOLATION", b="INCONCLUSIVE"))
    observations[2] = observations[2].model_copy(update={
        "outcome": "CLEAN", "canonical_result_checksum": checksum_value({"legacy": "drift"}),
    })
    report = evaluate_ab(
        evaluation_id="evaluation:legacy-drift:1", plan=_plan(), cases=(_case(),),
        observations=tuple(observations), adjudications=_reviews(reference="INCONCLUSIVE"),
    )
    a_stability = next(
        metric for metric in report.metrics
        if metric.metric_id == "canonical_repeat_stability" and metric.arm_id == "A"
    )
    assert a_stability.value == 0.0
    assert report.confusion_matrices["A"] == {"INCONCLUSIVE->INCONCLUSIVE": 1}


def test_non_deidentified_case_invalidates_evaluation():
    report = evaluate_ab(
        evaluation_id="evaluation:phi:1", plan=_plan(),
        cases=(_case().model_copy(update={"deidentified": False}),),
        observations=_observations(), adjudications=_reviews(),
    )
    assert report.status == EvaluationStatus.INVALID
    assert any(issue.code == "EVALUATION_CASE_NOT_DEIDENTIFIED" for issue in report.issues)


def test_blinded_packets_hide_arm_identity_and_package_is_append_only(tmp_path):
    plan = _plan()
    observations = _observations()
    report = evaluate_ab(
        evaluation_id="evaluation:persist:1", plan=plan, cases=(_case(),),
        observations=observations, adjudications=_reviews(),
    )
    packets = build_blinded_packets(plan, (_case(),), observations)
    assert {slot.slot_id for slot in packets[0].slots} == {"X", "Y"}
    assert "arm_id" not in packets[0].model_dump_json()
    package = EvaluationPackage(
        plan=plan, cases=(_case(),), observations=observations,
        adjudications=_reviews(), report=report,
    )
    output = tmp_path / "evaluation"
    manifest = persist_evaluation_package(package, output)
    assert set(manifest.artifact_digests) == {
        "evaluation_plan.json", "evaluation_cases.json", "arm_observations.json",
        "expert_adjudications.json", "evaluation_report.json",
    }
    assert (output.stat().st_mode & 0o777) == 0o700
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in output.iterdir())
    try:
        persist_evaluation_package(package, output)
    except FileExistsError:
        pass
    else:
        raise AssertionError("必须拒绝覆盖历史 evaluation package")


def test_promotion_profile_passes_only_with_stratified_blinded_evidence():
    base_plan = _plan(AcceptanceProfile.PROMOTION, repetitions=1)
    plan = base_plan.model_copy(update={"bootstrap_repetitions": 100})
    plan = plan.model_copy(update={"content_checksum": evaluation_plan_checksum(plan)})
    references = (["VIOLATION"] * 34) + (["CLEAN"] * 33) + (["INCONCLUSIVE"] * 33)
    cases = []
    observations = []
    reviews = []
    for index, reference in enumerate(references):
        case_id = f"case:promotion:{index:03d}"
        cases.append(EvaluationCase(
            case_id=case_id, source_snapshot_checksum=SNAPSHOT,
            analysis_unit_ref=case_id, stratum=f"reference:{reference}",
        ))
        for arm in ("A", "B"):
            observations.append(ArmObservation(
                observation_id=f"obs:{case_id}:{arm}:1", case_id=case_id,
                arm_id=arm, repetition=1, source_snapshot_checksum=SNAPSHOT,
                outcome=reference,
                canonical_result_checksum=checksum_value({"case": case_id, "arm": arm, "outcome": reference}),
                decisive_count=1, grounded_decisive_count=1, resolvable_locator_count=1,
                provenance_complete=True, proof_complete=arm == "B",
            ))
        for reviewer in ("expert-1", "expert-2"):
            reviews.append(ExpertAdjudication(
                review_id=f"review:{case_id}:{reviewer}", case_id=case_id,
                reviewer_ref=reviewer, kind="INDEPENDENT", outcome=reference,
                blinded=True, recorded_at=NOW,
            ))
        reviews.extend([
            ExpertAdjudication(
                review_id=f"review:{case_id}:final", case_id=case_id,
                reviewer_ref="adjudicator", kind="FINAL", outcome=reference,
                blinded=True, recorded_at=NOW,
            ),
            ExpertAdjudication(
                review_id=f"review:{case_id}:usability", case_id=case_id,
                reviewer_ref=f"usability-{index}", kind="INDEPENDENT", outcome=reference,
                blinded=True, arm_id="B", source_retrieval_success=True,
                explanation_score=5, review_duration_ms=1000, correction_count=0,
                recorded_at=NOW,
            ),
        ])
    report = evaluate_ab(
        evaluation_id="evaluation:promotion:1", plan=plan, cases=tuple(cases),
        observations=tuple(observations), adjudications=tuple(reviews),
    )
    assert report.status == EvaluationStatus.PASS
    assert all(gate.status == "PASS" for gate in report.gates)
    assert report.deployment_authorized is False
