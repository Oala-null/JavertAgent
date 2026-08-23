# -*- coding: utf-8 -*-
"""可复用、可持久化的 paired A/B Evaluation Contract。"""

from __future__ import annotations

import math
import os
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import FrozenStrictModel, ValidationIssue
from .privacy import privacy_issues
from .serialization import canonical_json_bytes, sha256_digest

Verdict = Literal["VIOLATION", "CLEAN", "INCONCLUSIVE"]


class AcceptanceProfile(StrEnum):
    CONFORMANCE = "CONFORMANCE"
    SHADOW = "SHADOW"
    PROMOTION = "PROMOTION"


class EvaluationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID = "INVALID"


class MetricStatus(StrEnum):
    OK = "ok"
    NOT_ESTIMABLE = "not_estimable"
    NOT_COMPARABLE = "not_comparable"
    NOT_APPLICABLE = "not_applicable"


class ArmVersion(FrozenStrictModel):
    arm_id: Literal["A", "B"]
    system_version: str = Field(min_length=1)
    code_commit: str = Field(min_length=7)
    config_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_version: str = Field(min_length=1)
    tool_version: str = Field(min_length=1)
    ontology_version: str = Field(min_length=1)
    knowledge_version: str = Field(min_length=1)


class AcceptanceThresholds(FrozenStrictModel):
    min_adjudicated_cases: int = Field(ge=0)
    min_per_reference_class: int = Field(ge=0)
    max_unsafe_upper: float | None = Field(default=None, ge=0, le=1)
    max_harm_diff_upper: float | None = None
    min_correct_automation_diff_lower: float | None = None
    min_grounding: float = Field(ge=0, le=1)
    min_locator: float = Field(ge=0, le=1)
    min_auto_grounding: float = Field(ge=0, le=1)
    min_explanation_median: float | None = Field(default=None, ge=1, le=5)
    min_source_retrieval: float | None = Field(default=None, ge=0, le=1)


def default_thresholds(profile: AcceptanceProfile) -> AcceptanceThresholds:
    if profile == AcceptanceProfile.CONFORMANCE:
        return AcceptanceThresholds(
            min_adjudicated_cases=0, min_per_reference_class=0,
            max_unsafe_upper=None, max_harm_diff_upper=0.0,
            min_correct_automation_diff_lower=None,
            min_grounding=1.0, min_locator=1.0, min_auto_grounding=1.0,
        )
    if profile == AcceptanceProfile.SHADOW:
        return AcceptanceThresholds(
            min_adjudicated_cases=30, min_per_reference_class=5,
            max_unsafe_upper=0.05, max_harm_diff_upper=0.05,
            min_correct_automation_diff_lower=-0.05,
            min_grounding=0.95, min_locator=0.95, min_auto_grounding=0.95,
            min_explanation_median=4.0, min_source_retrieval=0.95,
        )
    return AcceptanceThresholds(
        min_adjudicated_cases=100, min_per_reference_class=20,
        max_unsafe_upper=0.05, max_harm_diff_upper=0.02,
        min_correct_automation_diff_lower=-0.05,
        min_grounding=0.98, min_locator=0.98, min_auto_grounding=1.0,
        min_explanation_median=4.0, min_source_retrieval=0.95,
    )


class EvaluationPlan(FrozenStrictModel):
    plan_id: str = Field(min_length=3)
    schema_version: Literal["0.1.0"] = "0.1.0"
    plan_version: str = Field(min_length=1)
    created_at: datetime
    source_snapshot_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    candidate_query_version: str = Field(min_length=1)
    analysis_unit: str = Field(min_length=1)
    arms: tuple[ArmVersion, ArmVersion]
    repetitions: int = Field(ge=1)
    blinded: bool
    bootstrap_seed: int = 20260823
    bootstrap_repetitions: int = Field(default=2000, ge=100)
    harm_matrix_version: str = Field(min_length=1)
    harm_matrix: dict[str, float]
    profile: AcceptanceProfile
    thresholds: AcceptanceThresholds
    signer_roles: tuple[str, ...] = Field(min_length=1)
    content_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _two_arms(self) -> "EvaluationPlan":
        if {arm.arm_id for arm in self.arms} != {"A", "B"}:
            raise ValueError("EvaluationPlan 必须且只能声明 A/B 两个 arm")
        return self


def evaluation_plan_checksum(plan: EvaluationPlan | dict[str, Any]) -> str:
    raw = plan.model_dump(mode="json") if isinstance(plan, EvaluationPlan) else dict(plan)
    raw.pop("content_checksum", None)
    return sha256_digest(canonical_json_bytes(raw))


def build_evaluation_plan(**kwargs: Any) -> EvaluationPlan:
    raw = dict(kwargs)
    raw.setdefault("content_checksum", "sha256:" + "0" * 64)
    provisional = EvaluationPlan.model_validate(raw)
    raw["content_checksum"] = evaluation_plan_checksum(provisional)
    return EvaluationPlan.model_validate(raw)


class EvaluationCase(FrozenStrictModel):
    case_id: str = Field(min_length=3)
    source_snapshot_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    analysis_unit_ref: str = Field(min_length=1)
    stratum: str = Field(min_length=1)
    deidentified: bool = True


class ArmObservation(FrozenStrictModel):
    observation_id: str = Field(min_length=3)
    case_id: str = Field(min_length=3)
    arm_id: Literal["A", "B"]
    repetition: int = Field(ge=1)
    source_snapshot_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    outcome: Verdict | None = None
    canonical_result_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    technical_status: Literal["ok", "failed"] = "ok"
    reason_codes: tuple[str, ...] = ()
    duration_ms: int = Field(default=0, ge=0)
    token_count: int = Field(default=0, ge=0)
    decisive_count: int = Field(default=0, ge=0)
    grounded_decisive_count: int = Field(default=0, ge=0)
    resolvable_locator_count: int = Field(default=0, ge=0)
    provenance_complete: bool = False
    proof_complete: bool = False
    conflict_expected: bool = False
    conflict_detected: bool = False

    @model_validator(mode="after")
    def _counts(self) -> "ArmObservation":
        if self.grounded_decisive_count > self.decisive_count:
            raise ValueError("grounded_decisive_count 不得超过 decisive_count")
        if self.resolvable_locator_count > self.grounded_decisive_count:
            raise ValueError("resolvable_locator_count 不得超过 grounded_decisive_count")
        return self


class ExpertAdjudication(FrozenStrictModel):
    review_id: str = Field(min_length=3)
    case_id: str = Field(min_length=3)
    reviewer_ref: str = Field(min_length=1)
    kind: Literal["INDEPENDENT", "FINAL"]
    outcome: Verdict
    blinded: bool
    arm_id: Literal["A", "B"] | None = None
    reason_codes: tuple[str, ...] = ()
    source_retrieval_success: bool | None = None
    explanation_score: int | None = Field(default=None, ge=1, le=5)
    review_duration_ms: int | None = Field(default=None, ge=0)
    correction_count: int | None = Field(default=None, ge=0)
    recorded_at: datetime


class MetricResult(FrozenStrictModel):
    metric_id: str = Field(min_length=1)
    arm_id: Literal["A", "B"] | None = None
    status: MetricStatus
    numerator: float | None = None
    denominator: int | None = Field(default=None, ge=0)
    value: float | None = None
    lower_95: float | None = None
    upper_95: float | None = None


class AcceptanceGate(FrozenStrictModel):
    gate_id: str = Field(min_length=1)
    status: Literal["PASS", "FAIL", "INSUFFICIENT", "NOT_APPLICABLE"]
    reason_codes: tuple[str, ...] = ()


class CaseDelta(FrozenStrictModel):
    case_id: str
    a_outcome: Verdict | None = None
    b_outcome: Verdict | None = None
    reference_outcome: Verdict | None = None
    eligibility_status: str = ""
    decisive_criteria: tuple[str, ...] = ()
    documentation_gaps: tuple[str, ...] = ()
    data_quality_flags: tuple[str, ...] = ()
    added_evidence_refs: tuple[str, ...] = ()
    removed_evidence_refs: tuple[str, ...] = ()
    version_refs: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


class EvaluationReport(FrozenStrictModel):
    evaluation_id: str = Field(min_length=3)
    schema_version: Literal["0.1.0"] = "0.1.0"
    plan_id: str
    plan_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    profile: AcceptanceProfile
    status: EvaluationStatus
    deployment_authorized: Literal[False] = False
    input_digests: dict[str, str]
    metrics: tuple[MetricResult, ...]
    gates: tuple[AcceptanceGate, ...]
    confusion_matrices: dict[str, dict[str, int]] = Field(default_factory=dict)
    case_deltas: tuple[CaseDelta, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()


class EvaluationManifest(FrozenStrictModel):
    evaluation_id: str
    schema_version: Literal["0.1.0"] = "0.1.0"
    artifact_digests: dict[str, str]
    phi_policy: str = Field(min_length=1)


class EvaluationPackage(FrozenStrictModel):
    plan: EvaluationPlan
    cases: tuple[EvaluationCase, ...]
    observations: tuple[ArmObservation, ...]
    adjudications: tuple[ExpertAdjudication, ...]
    report: EvaluationReport


class BlindedArmSlot(FrozenStrictModel):
    slot_id: Literal["X", "Y"]
    outcome: Verdict | None
    evidence_summary_codes: tuple[str, ...] = ()


class BlindedReviewPacket(FrozenStrictModel):
    packet_id: str
    case_id: str
    source_snapshot_checksum: str
    slots: tuple[BlindedArmSlot, BlindedArmSlot]


def _wilson(numerator: int, denominator: int) -> tuple[float, float]:
    if denominator <= 0:
        raise ValueError("zero denominator")
    z = 1.959963984540054
    p = numerator / denominator
    center = (p + z * z / (2 * denominator)) / (1 + z * z / denominator)
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * denominator)) / denominator) / (1 + z * z / denominator)
    return max(0.0, center - half), min(1.0, center + half)


def _rate(metric_id: str, numerator: int, denominator: int, arm_id: str | None = None) -> MetricResult:
    if denominator == 0:
        return MetricResult(metric_id=metric_id, arm_id=arm_id, status=MetricStatus.NOT_ESTIMABLE, denominator=0)
    if numerator < 0 or numerator > denominator:
        raise ValueError(f"invalid rate {metric_id}: {numerator}/{denominator}")
    lower, upper = _wilson(numerator, denominator)
    return MetricResult(
        metric_id=metric_id, arm_id=arm_id, status=MetricStatus.OK,
        numerator=numerator, denominator=denominator, value=numerator / denominator,
        lower_95=lower, upper_95=upper,
    )


def _bootstrap_bound(values: list[float], *, seed: int, repetitions: int, upper: bool) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(repetitions):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    quantile = 0.95 if upper else 0.05
    return means[min(len(means) - 1, int(quantile * len(means)))]


def _sign_test(wins: int, losses: int) -> float | None:
    n = wins + losses
    if n == 0:
        return None
    tail = sum(math.comb(n, k) for k in range(0, min(wins, losses) + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def _modal(observations: list[ArmObservation]) -> Verdict | None:
    values = [item.outcome for item in observations if item.outcome]
    if not values:
        return None
    counts = Counter(values)
    top = max(counts.values())
    winners = [value for value, count in counts.items() if count == top]
    return winners[0] if len(winners) == 1 else "INCONCLUSIVE"


def _harm(plan: EvaluationPlan, predicted: Verdict | None, reference: Verdict) -> float:
    if predicted is None:
        return 1.0
    return float(plan.harm_matrix.get(f"{predicted}|{reference}", 0.0 if predicted == reference else 1.0))


def _digest_inputs(
    plan: EvaluationPlan, cases: tuple[EvaluationCase, ...],
    observations: tuple[ArmObservation, ...], adjudications: tuple[ExpertAdjudication, ...],
) -> dict[str, str]:
    return {
        "plan": sha256_digest(plan), "cases": sha256_digest(cases),
        "observations": sha256_digest(observations),
        "adjudications": sha256_digest(adjudications),
    }


def evaluate_ab(
    *, evaluation_id: str, plan: EvaluationPlan,
    cases: tuple[EvaluationCase, ...], observations: tuple[ArmObservation, ...],
    adjudications: tuple[ExpertAdjudication, ...] = (),
    case_deltas: tuple[CaseDelta, ...] = (),
) -> EvaluationReport:
    issues: list[ValidationIssue] = []
    for artifact_name, value_to_check in (
        ("plan", plan), ("cases", cases), ("observations", observations),
        ("adjudications", adjudications),
    ):
        issues.extend(
            issue.model_copy(update={"path": f"{artifact_name}{issue.path[1:]}"})
            for issue in privacy_issues(value_to_check)
        )
    if evaluation_plan_checksum(plan) != plan.content_checksum:
        issues.append(ValidationIssue(code="EVALUATION_PLAN_CHECKSUM_MISMATCH", path="plan.content_checksum"))
    case_by_id = {item.case_id: item for item in cases}
    if len(case_by_id) != len(cases):
        issues.append(ValidationIssue(code="EVALUATION_DUPLICATE_CASE"))
    for case in cases:
        if not case.deidentified:
            issues.append(ValidationIssue(code="EVALUATION_CASE_NOT_DEIDENTIFIED", record_id=case.case_id))
    grouped: dict[tuple[str, str], list[ArmObservation]] = defaultdict(list)
    for observation in observations:
        case = case_by_id.get(observation.case_id)
        if case is None:
            issues.append(ValidationIssue(code="EVALUATION_OBSERVATION_CASE_MISSING", record_id=observation.observation_id))
            continue
        if observation.source_snapshot_checksum != case.source_snapshot_checksum or case.source_snapshot_checksum != plan.source_snapshot_checksum:
            issues.append(ValidationIssue(code="EVALUATION_SOURCE_SNAPSHOT_MISMATCH", record_id=observation.observation_id))
        grouped[(observation.case_id, observation.arm_id)].append(observation)
    expected = len(cases) * 2 * plan.repetitions
    pair_complete = 0
    for case in cases:
        if all(len(grouped[(case.case_id, arm)]) == plan.repetitions for arm in ("A", "B")):
            pair_complete += 1
        else:
            issues.append(ValidationIssue(code="EVALUATION_ARM_OR_REPETITION_MISSING", record_id=case.case_id))
    if len(observations) != expected:
        issues.append(ValidationIssue(code="EVALUATION_OBSERVATION_COUNT_MISMATCH"))

    final_refs: dict[str, ExpertAdjudication] = {}
    independent_by_case: dict[str, list[ExpertAdjudication]] = defaultdict(list)
    for review in adjudications:
        if not review.blinded:
            issues.append(ValidationIssue(code="EVALUATION_REVIEW_NOT_BLINDED", record_id=review.review_id))
        if review.kind == "FINAL":
            if review.case_id in final_refs:
                issues.append(ValidationIssue(code="EVALUATION_MULTIPLE_FINAL_REFERENCE", record_id=review.case_id))
            final_refs[review.case_id] = review
        else:
            independent_by_case[review.case_id].append(review)
    for case_id, final in final_refs.items():
        if plan.profile != AcceptanceProfile.CONFORMANCE and len({item.reviewer_ref for item in independent_by_case[case_id]}) < 2:
            issues.append(ValidationIssue(code="EVALUATION_INDEPENDENT_REVIEWERS_REQUIRED", record_id=final.review_id))

    metrics: list[MetricResult] = []
    metrics.append(_rate("pair_completeness", pair_complete, len(cases)))
    metrics.append(_rate("observation_completeness", min(len(observations), expected), expected))
    confusion: dict[str, dict[str, int]] = {}
    modal: dict[tuple[str, str], Verdict | None] = {
        key: _modal(value) for key, value in grouped.items()
    }
    technical_failures = sum(item.technical_status != "ok" for item in observations)
    metrics.append(_rate("technical_failure", technical_failures, len(observations)))

    for arm in ("A", "B"):
        comparable = [(case_id, modal[(case_id, arm)], final_refs[case_id].outcome) for case_id in final_refs]
        comparable = [item for item in comparable if item[1] is not None]
        matrix: Counter[str] = Counter(f"{reference}->{predicted}" for _, predicted, reference in comparable)
        confusion[arm] = dict(sorted(matrix.items()))
        metrics.extend([
            _rate("exact_agreement", sum(pred == ref for _, pred, ref in comparable), len(comparable), arm),
            _rate("false_violation", sum(pred == "VIOLATION" and ref != "VIOLATION" for _, pred, ref in comparable), sum(pred == "VIOLATION" for _, pred, _ in comparable), arm),
            _rate("false_clean", sum(pred == "CLEAN" and ref != "CLEAN" for _, pred, ref in comparable), sum(pred == "CLEAN" for _, pred, _ in comparable), arm),
            _rate("unsafe_auto_decision", sum(pred in {"VIOLATION", "CLEAN"} and pred != ref for _, pred, ref in comparable), len(comparable), arm),
            _rate("correct_automation", sum(pred in {"VIOLATION", "CLEAN"} and pred == ref for _, pred, ref in comparable), len(comparable), arm),
            _rate("appropriate_abstention", sum(pred == ref == "INCONCLUSIVE" for _, pred, ref in comparable), sum(ref == "INCONCLUSIVE" for _, _, ref in comparable), arm),
            _rate("unnecessary_abstention", sum(pred == "INCONCLUSIVE" and ref != "INCONCLUSIVE" for _, pred, ref in comparable), sum(ref != "INCONCLUSIVE" for _, _, ref in comparable), arm),
        ])
        for reference_class in ("VIOLATION", "CLEAN", "INCONCLUSIVE"):
            metrics.append(_rate(
                f"recall_{reference_class.lower()}",
                sum(pred == ref == reference_class for _, pred, ref in comparable),
                sum(ref == reference_class for _, _, ref in comparable),
                arm,
            ))
        arm_observations = [item for item in observations if item.arm_id == arm]
        decisive = sum(item.decisive_count for item in arm_observations)
        grounded = sum(item.grounded_decisive_count for item in arm_observations)
        resolvable = sum(item.resolvable_locator_count for item in arm_observations)
        metrics.extend([
            _rate("decisive_grounding", grounded, decisive, arm),
            _rate("locator_resolvability", resolvable, grounded, arm),
            _rate("provenance_completeness", sum(item.provenance_complete for item in arm_observations), len(arm_observations), arm),
            _rate("proof_completeness", sum(item.proof_complete for item in arm_observations), len(arm_observations), arm),
            _rate("conflict_visibility", sum(item.conflict_expected and item.conflict_detected for item in arm_observations), sum(item.conflict_expected for item in arm_observations), arm),
        ])
        if arm_observations:
            metrics.append(MetricResult(
                metric_id="duration_ms_median", arm_id=arm, status=MetricStatus.OK,
                denominator=len(arm_observations),
                value=float(statistics.median(item.duration_ms for item in arm_observations)),
            ))
            metrics.append(MetricResult(
                metric_id="token_count_median", arm_id=arm, status=MetricStatus.OK,
                denominator=len(arm_observations),
                value=float(statistics.median(item.token_count for item in arm_observations)),
            ))
        auto_case_ids = {
            case_id for case_id in case_by_id
            if modal.get((case_id, arm)) in {"VIOLATION", "CLEAN"}
        }
        auto_observations = [item for item in arm_observations if item.case_id in auto_case_ids]
        auto_decisive = sum(item.decisive_count for item in auto_observations)
        auto_grounded = sum(item.grounded_decisive_count for item in auto_observations)
        metrics.append(_rate("auto_decision_grounding", auto_grounded, auto_decisive, arm))
        stable_groups = [value for (case_id, arm_id), value in grouped.items() if arm_id == arm and case_id in case_by_id]
        stable = sum(len({item.canonical_result_checksum for item in value}) == 1 for value in stable_groups)
        metrics.append(_rate("canonical_repeat_stability", stable, len(stable_groups), arm))

    paired_with_ref = [case_id for case_id in final_refs if modal.get((case_id, "A")) and modal.get((case_id, "B"))]
    harm_diffs: list[float] = []
    correct_diffs: list[float] = []
    unsafe_diffs: list[float] = []
    wins = losses = ties = 0
    for case_id in paired_with_ref:
        ref = final_refs[case_id].outcome
        a = modal[(case_id, "A")]
        b = modal[(case_id, "B")]
        a_loss = _harm(plan, a, ref)
        b_loss = _harm(plan, b, ref)
        diff = b_loss - a_loss
        harm_diffs.append(diff)
        correct_diffs.append(float(b in {"VIOLATION", "CLEAN"} and b == ref) - float(a in {"VIOLATION", "CLEAN"} and a == ref))
        unsafe_diffs.append(float(b in {"VIOLATION", "CLEAN"} and b != ref) - float(a in {"VIOLATION", "CLEAN"} and a != ref))
        wins += diff < 0
        losses += diff > 0
        ties += diff == 0
    for metric_id, values, upper in (
        ("paired_harm_loss_diff", harm_diffs, True),
        ("paired_correct_automation_diff", correct_diffs, False),
        ("paired_unsafe_auto_diff", unsafe_diffs, True),
    ):
        if not values:
            metrics.append(MetricResult(metric_id=metric_id, status=MetricStatus.NOT_ESTIMABLE, denominator=0))
        else:
            bound = _bootstrap_bound(values, seed=plan.bootstrap_seed, repetitions=plan.bootstrap_repetitions, upper=upper)
            metrics.append(MetricResult(
                metric_id=metric_id, status=MetricStatus.OK,
                numerator=sum(values), denominator=len(values), value=sum(values) / len(values),
                upper_95=bound if upper else None, lower_95=None if upper else bound,
            ))
    metrics.append(MetricResult(
        metric_id="paired_sign_test", status=MetricStatus.OK if wins + losses else MetricStatus.NOT_ESTIMABLE,
        numerator=wins, denominator=wins + losses, value=_sign_test(wins, losses),
    ))
    metrics.append(
        _rate("paired_ties", ties, len(paired_with_ref))
        if paired_with_ref else
        MetricResult(metric_id="paired_ties", status=MetricStatus.NOT_ESTIMABLE, denominator=0)
    )
    metrics.append(MetricResult(metric_id="probability_calibration", status=MetricStatus.NOT_COMPARABLE))

    usability = [item for item in adjudications if item.arm_id == "B" and item.explanation_score is not None]
    explanation_median = statistics.median(item.explanation_score for item in usability) if usability else None
    metrics.append(MetricResult(
        metric_id="explanation_score_median", arm_id="B",
        status=MetricStatus.OK if usability else MetricStatus.NOT_ESTIMABLE,
        denominator=len(usability), value=explanation_median,
    ))
    retrieval = [item for item in adjudications if item.arm_id == "B" and item.source_retrieval_success is not None]
    metrics.append(_rate("source_retrieval", sum(bool(item.source_retrieval_success) for item in retrieval), len(retrieval), "B"))
    durations = [item.review_duration_ms for item in adjudications if item.arm_id == "B" and item.review_duration_ms is not None]
    metrics.append(MetricResult(
        metric_id="expert_review_duration_ms_median", arm_id="B",
        status=MetricStatus.OK if durations else MetricStatus.NOT_ESTIMABLE,
        denominator=len(durations), value=float(statistics.median(durations)) if durations else None,
    ))
    corrections = [item.correction_count for item in adjudications if item.arm_id == "B" and item.correction_count is not None]
    metrics.append(MetricResult(
        metric_id="expert_correction_count", arm_id="B",
        status=MetricStatus.OK if corrections else MetricStatus.NOT_ESTIMABLE,
        numerator=float(sum(corrections)) if corrections else None,
        denominator=len(corrections), value=(sum(corrections) / len(corrections)) if corrections else None,
    ))

    metric_index = {(item.metric_id, item.arm_id): item for item in metrics}
    gates: list[AcceptanceGate] = []
    valid = not issues and pair_complete == len(cases) and technical_failures == 0
    gates.append(AcceptanceGate(gate_id="validity", status="PASS" if valid else "FAIL", reason_codes=() if valid else ("INVALID_INPUT_OR_TECHNICAL_FAILURE",)))

    class_counts = Counter(item.outcome for item in final_refs.values())
    thresholds = plan.thresholds
    sample_ok = len(final_refs) >= thresholds.min_adjudicated_cases and all(count >= thresholds.min_per_reference_class for count in class_counts.values())
    if plan.profile != AcceptanceProfile.CONFORMANCE and not sample_ok:
        gates.append(AcceptanceGate(gate_id="sample", status="INSUFFICIENT", reason_codes=("ADJUDICATED_SAMPLE_TOO_SMALL",)))
    else:
        gates.append(AcceptanceGate(gate_id="sample", status="PASS"))

    def value(metric_id: str, arm_id: str | None = None, attr: str = "value") -> float | None:
        metric = metric_index.get((metric_id, arm_id))
        return getattr(metric, attr) if metric else None

    b_ground = value("decisive_grounding", "B")
    b_locator = value("locator_resolvability", "B")
    b_prov = value("provenance_completeness", "B")
    b_proof = value("proof_completeness", "B")
    b_stability = value("canonical_repeat_stability", "B")
    b_auto_ground = value("auto_decision_grounding", "B")
    auto_ground_ok = b_auto_ground is None or b_auto_ground >= thresholds.min_auto_grounding
    integrity_ok = all(item is not None for item in (b_ground, b_locator, b_prov, b_proof, b_stability)) and b_ground >= thresholds.min_grounding and b_locator >= thresholds.min_locator and b_prov == 1.0 and b_proof == 1.0 and b_stability == 1.0 and auto_ground_ok
    gates.append(AcceptanceGate(gate_id="evidence_integrity", status="PASS" if integrity_ok else "FAIL", reason_codes=() if integrity_ok else ("EVIDENCE_GATE_FAILED",)))

    safety_regressions = sum(diff > 0 for diff in unsafe_diffs)
    safety_ok = safety_regressions == 0
    harm_upper = value("paired_harm_loss_diff", None, "upper_95")
    correct_lower = value("paired_correct_automation_diff", None, "lower_95")
    if plan.profile != AcceptanceProfile.CONFORMANCE:
        safety_ok = safety_ok and harm_upper is not None and harm_upper <= (thresholds.max_harm_diff_upper or 0)
        unsafe_diff_upper = value("paired_unsafe_auto_diff", None, "upper_95")
        safety_ok = safety_ok and unsafe_diff_upper is not None and unsafe_diff_upper <= (thresholds.max_unsafe_upper or 0)
        if plan.profile == AcceptanceProfile.PROMOTION:
            b_unsafe_upper = value("unsafe_auto_decision", "B", "upper_95")
            safety_ok = safety_ok and b_unsafe_upper is not None and b_unsafe_upper <= (thresholds.max_unsafe_upper or 0)
        if thresholds.min_correct_automation_diff_lower is not None:
            safety_ok = safety_ok and correct_lower is not None and correct_lower >= thresholds.min_correct_automation_diff_lower
    gates.append(AcceptanceGate(gate_id="clinical_safety_and_utility", status="PASS" if safety_ok else ("NOT_APPLICABLE" if plan.profile == AcceptanceProfile.CONFORMANCE and not final_refs else "FAIL"), reason_codes=() if safety_ok else ("SAFETY_OR_UTILITY_GATE_FAILED",)))

    usability_ok = True
    if plan.profile == AcceptanceProfile.PROMOTION:
        usability_ok = bool(explanation_median is not None and explanation_median >= (thresholds.min_explanation_median or 0) and value("source_retrieval", "B") is not None and value("source_retrieval", "B") >= (thresholds.min_source_retrieval or 0))
    gates.append(AcceptanceGate(gate_id="expert_usability", status="PASS" if usability_ok else "FAIL"))

    if not valid:
        status = EvaluationStatus.INVALID
    elif plan.profile != AcceptanceProfile.CONFORMANCE and not sample_ok:
        status = EvaluationStatus.INSUFFICIENT_EVIDENCE
    elif any(gate.status == "FAIL" for gate in gates):
        status = EvaluationStatus.FAIL
    else:
        status = EvaluationStatus.PASS
    return EvaluationReport(
        evaluation_id=evaluation_id, plan_id=plan.plan_id,
        plan_checksum=plan.content_checksum, profile=plan.profile, status=status,
        input_digests=_digest_inputs(plan, cases, observations, adjudications),
        metrics=tuple(sorted(metrics, key=lambda item: (item.metric_id, item.arm_id or ""))),
        gates=tuple(gates), confusion_matrices=confusion,
        case_deltas=case_deltas,
        issues=tuple(sorted(issues, key=lambda item: (item.code, item.record_id, item.path))),
    )


def build_blinded_packets(
    plan: EvaluationPlan, cases: tuple[EvaluationCase, ...],
    observations: tuple[ArmObservation, ...],
) -> tuple[BlindedReviewPacket, ...]:
    grouped: dict[tuple[str, str], list[ArmObservation]] = defaultdict(list)
    for observation in observations:
        grouped[(observation.case_id, observation.arm_id)].append(observation)
    packets = []
    for case in sorted(cases, key=lambda item: item.case_id):
        arm_rows = {arm: grouped[(case.case_id, arm)] for arm in ("A", "B")}
        if not all(arm_rows.values()):
            continue
        swap = random.Random(f"{plan.bootstrap_seed}:{case.case_id}").randrange(2) == 1
        order = ("B", "A") if swap else ("A", "B")
        packets.append(BlindedReviewPacket(
            packet_id=f"packet:{case.case_id}", case_id=case.case_id,
            source_snapshot_checksum=case.source_snapshot_checksum,
            slots=tuple(
                BlindedArmSlot(
                    slot_id="X" if index == 0 else "Y",
                    outcome=_modal(arm_rows[arm]),
                    evidence_summary_codes=tuple(sorted({code for row in arm_rows[arm] for code in row.reason_codes})),
                )
                for index, arm in enumerate(order)
            ),
        ))
    return tuple(packets)


def persist_evaluation_package(
    package: EvaluationPackage, directory: Path, *, real_case: bool = False,
) -> EvaluationManifest:
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError("evaluation 目录已存在且非空，禁止覆盖历史")
    if real_case:
        ancestors = (directory.parent, *directory.parents)
        if any((path / ".git").exists() for path in ancestors):
            raise ValueError("真实病例 evaluation package 不得写入 Git 工作区")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    artifacts = {
        "evaluation_plan.json": package.plan,
        "evaluation_cases.json": package.cases,
        "arm_observations.json": package.observations,
        "expert_adjudications.json": package.adjudications,
        "evaluation_report.json": package.report,
    }
    expected_input_digests = _digest_inputs(
        package.plan, package.cases, package.observations, package.adjudications,
    )
    if package.report.input_digests != expected_input_digests:
        raise ValueError("evaluation report input digests 与工件不一致")
    all_privacy = tuple(issue for value in artifacts.values() for issue in privacy_issues(value))
    if all_privacy:
        raise ValueError("evaluation package privacy gate failed: " + all_privacy[0].path)
    digests: dict[str, str] = {}
    for name, value in artifacts.items():
        payload = canonical_json_bytes(value)
        path = directory / name
        path.write_bytes(payload)
        os.chmod(path, 0o600)
        digests[name] = sha256_digest(payload)
    manifest = EvaluationManifest(
        evaluation_id=package.report.evaluation_id,
        artifact_digests=digests,
        phi_policy=(
            "真实病例工件仅允许受控 0700/0600 外部目录与 salted refs；"
            if real_case else "仅含明确标记的 synthetic/deidentified 工件；"
        ) + "公开报告不含患者号、原文、凭据或 salt。",
    )
    manifest_path = directory / "manifest.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    os.chmod(manifest_path, 0o600)
    return manifest
