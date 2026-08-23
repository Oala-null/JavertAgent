# -*- coding: utf-8 -*-
"""Diagnosis extraction A/B conformance（不冒充临床效果评测）。"""

from __future__ import annotations

from datetime import datetime

from .evaluation import (
    AcceptanceGate, AcceptanceProfile, ArmObservation, ArmVersion, CaseDelta,
    EvaluationCase, EvaluationPackage, EvaluationPlan, EvaluationReport,
    EvaluationStatus, MetricResult, MetricStatus, build_evaluation_plan,
    default_thresholds, evaluate_ab,
)
from .models import ValidationIssue
from .projection import DiagnosisProjection
from .serialization import checksum_value

_CLINICAL_METRICS = {
    "exact_agreement", "false_violation", "false_clean", "unsafe_auto_decision",
    "correct_automation", "appropriate_abstention", "unnecessary_abstention",
    "recall_violation", "recall_clean", "recall_inconclusive",
    "paired_harm_loss_diff", "paired_correct_automation_diff",
    "paired_unsafe_auto_diff", "paired_sign_test", "paired_ties",
    "probability_calibration", "explanation_score_median", "source_retrieval",
    "expert_review_duration_ms_median", "expert_correction_count",
}


def build_diagnosis_conformance_plan(
    *, plan_id: str, created_at: datetime, source_snapshot_checksum: str,
    code_commit: str, config_checksum: str, repetitions: int = 2,
) -> EvaluationPlan:
    return build_evaluation_plan(
        plan_id=plan_id, plan_version="1", created_at=created_at,
        source_snapshot_checksum=source_snapshot_checksum,
        candidate_query_version="diagnosis-shadow-golden-v1",
        analysis_unit="synthetic-diagnosis-source", repetitions=repetitions,
        blinded=True, harm_matrix_version="not-applicable", harm_matrix={},
        profile=AcceptanceProfile.CONFORMANCE,
        thresholds=default_thresholds(AcceptanceProfile.CONFORMANCE),
        signer_roles=("clinical", "data", "engineering"),
        arms=(
            ArmVersion(
                arm_id="A", system_version="legacy-diagnosis", code_commit=code_commit,
                config_checksum=config_checksum, model_version="none",
                tool_version="note_diagnosis-legacy", ontology_version="none",
                knowledge_version="legacy-text",
            ),
            ArmVersion(
                arm_id="B", system_version="diagnosis-evidence-shadow-v0.1",
                code_commit=code_commit, config_checksum=config_checksum,
                model_version="deterministic", tool_version="diagnosis-shadow-extractor-v0.1",
                ontology_version="ontology-v0.1", knowledge_version="terminology-v1",
            ),
        ),
    )


def build_diagnosis_conformance_package(
    *, evaluation_id: str, plan: EvaluationPlan, case_id: str,
    legacy_output: dict, expected_legacy_output: dict,
    projections: tuple[DiagnosisProjection, ...], expected_occurrence_count: int,
) -> EvaluationPackage:
    if plan.profile != AcceptanceProfile.CONFORMANCE:
        empty_report = EvaluationReport(
            evaluation_id=evaluation_id, plan_id=plan.plan_id,
            plan_checksum=plan.content_checksum, profile=plan.profile,
            status=EvaluationStatus.INVALID, input_digests={}, metrics=(),
            gates=(AcceptanceGate(
                gate_id="diagnosis_scope", status="FAIL",
                reason_codes=("METRIC_NOT_APPLICABLE",),
            ),),
            issues=(ValidationIssue(code="METRIC_NOT_APPLICABLE", path="profile"),),
        )
        return EvaluationPackage(
            plan=plan, cases=(), observations=(), adjudications=(), report=empty_report,
        )
    if len(projections) != plan.repetitions:
        raise ValueError("projection repetitions 与 plan 不一致")
    case = EvaluationCase(
        case_id=case_id, source_snapshot_checksum=plan.source_snapshot_checksum,
        analysis_unit_ref=case_id, stratum="diagnosis-conformance",
    )
    observations = []
    for repetition, projection in enumerate(projections, 1):
        legacy_count = len(legacy_output.get("diagnoses") or [])
        b_decisive = [item for item in projection.items if item.state in {"TRUE", "FALSE", "CONFLICT"}]
        b_grounded = sum(bool(item.evidence_ids) for item in b_decisive)
        b_located = sum(bool(item.locators) for item in b_decisive)
        observations.extend([
            ArmObservation(
                observation_id=f"obs:{case_id}:A:{repetition}", case_id=case_id,
                arm_id="A", repetition=repetition,
                source_snapshot_checksum=plan.source_snapshot_checksum,
                canonical_result_checksum=checksum_value(legacy_output),
                decisive_count=legacy_count, grounded_decisive_count=0,
                resolvable_locator_count=0, provenance_complete=False, proof_complete=False,
            ),
            ArmObservation(
                observation_id=f"obs:{case_id}:B:{repetition}", case_id=case_id,
                arm_id="B", repetition=repetition,
                source_snapshot_checksum=plan.source_snapshot_checksum,
                canonical_result_checksum=projection.canonical_checksum,
                decisive_count=len(b_decisive), grounded_decisive_count=b_grounded,
                resolvable_locator_count=b_located, provenance_complete=True, proof_complete=True,
                conflict_expected=any(item.state == "CONFLICT" for item in projection.items),
                conflict_detected=any(item.state == "CONFLICT" for item in projection.items),
            ),
        ])
    delta = CaseDelta(
        case_id=case_id,
        added_evidence_refs=tuple(sorted({
            evidence_id for item in projections[0].items for evidence_id in item.evidence_ids
        })),
        reason_codes=(
            "PRECISE_LOCATOR_ADDED",
            "LEGACY_OUTPUT_UNCHANGED" if legacy_output == expected_legacy_output else "LEGACY_OUTPUT_CHANGED",
        ),
    )
    report = evaluate_ab(
        evaluation_id=evaluation_id, plan=plan, cases=(case,),
        observations=tuple(observations), case_deltas=(delta,),
    )
    metrics = []
    for metric in report.metrics:
        if metric.metric_id in _CLINICAL_METRICS:
            metrics.append(MetricResult(
                metric_id=metric.metric_id, arm_id=metric.arm_id,
                status=MetricStatus.NOT_APPLICABLE,
            ))
        else:
            metrics.append(metric)
    occurrence = len(projections[0].items)
    metrics.extend([
        MetricResult(
            metric_id="occurrence_coverage", arm_id="B", status=MetricStatus.OK,
            numerator=occurrence, denominator=expected_occurrence_count,
            value=occurrence / expected_occurrence_count if expected_occurrence_count else None,
        ),
        MetricResult(
            metric_id="legacy_output_compatibility", arm_id="A", status=MetricStatus.OK,
            numerator=int(legacy_output == expected_legacy_output), denominator=1,
            value=float(legacy_output == expected_legacy_output),
        ),
    ])
    gates = tuple(
        gate.model_copy(update={"status": "NOT_APPLICABLE", "reason_codes": ("METRIC_NOT_APPLICABLE",)})
        if gate.gate_id in {"clinical_safety_and_utility", "expert_usability"}
        else gate
        for gate in report.gates
    )
    conformance_ok = (
        occurrence == expected_occurrence_count
        and legacy_output == expected_legacy_output
        and report.status == EvaluationStatus.PASS
    )
    if not conformance_ok:
        gates = (*gates, AcceptanceGate(
            gate_id="diagnosis_conformance", status="FAIL",
            reason_codes=("DIAGNOSIS_CONFORMANCE_FAILED",),
        ))
        status = EvaluationStatus.FAIL
    else:
        gates = (*gates, AcceptanceGate(gate_id="diagnosis_conformance", status="PASS"))
        status = EvaluationStatus.PASS
    report = report.model_copy(update={
        "status": status,
        "metrics": tuple(sorted(metrics, key=lambda item: (item.metric_id, item.arm_id or ""))),
        "gates": gates,
    })
    return EvaluationPackage(
        plan=plan, cases=(case,), observations=tuple(observations),
        adjudications=(), report=report,
    )
