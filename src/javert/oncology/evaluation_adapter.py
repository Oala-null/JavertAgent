# -*- coding: utf-8 -*-
"""Oncology legacy/structured same-run paired A/B adapter。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from javert.evidence.evaluation import (
    ArmObservation, CaseDelta, EvaluationCase, EvaluationPackage, EvaluationPlan,
    EvaluationStatus, ExpertAdjudication, evaluate_ab,
)
from javert.evidence.models import ValidationIssue
from javert.evidence.serialization import checksum_value

from .contracts import CriterionState, EligibilityEvaluation, ProofNode


class OncologyPairedRowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case: EvaluationCase | None = None
    observations: tuple[ArmObservation, ...] = ()
    delta: CaseDelta | None = None
    issues: tuple[ValidationIssue, ...] = ()


def _decisive_criteria(node: ProofNode) -> set[str]:
    if node.operator == "leaf":
        return {node.criterion_id} if node.criterion_id else set()
    decisive = set(node.decisive_child_ids)
    output: set[str] = set()
    for child in node.children:
        if not decisive or child.node_id in decisive:
            output.update(_decisive_criteria(child))
    return output


def _manifest_matches(plan: EvaluationPlan, manifests: Any) -> bool:
    if not isinstance(manifests, dict):
        return False
    by_arm = {arm.arm_id: arm for arm in plan.arms}
    for arm_id in ("A", "B"):
        raw = manifests.get(arm_id)
        expected = by_arm[arm_id]
        if not isinstance(raw, dict):
            return False
        if raw.get("code_commit") != expected.code_commit or raw.get("config_checksum") != expected.config_checksum:
            return False
    return True


def oncology_paired_row(row: dict[str, Any], plan: EvaluationPlan) -> OncologyPairedRowResult:
    issues: list[ValidationIssue] = []
    case_id = str(row.get("case_id") or "")
    snapshot = str(row.get("source_snapshot_checksum") or "")
    if not case_id:
        issues.append(ValidationIssue(code="PAIRED_CASE_ID_MISSING"))
    if snapshot != plan.source_snapshot_checksum:
        issues.append(ValidationIssue(code="PAIRED_SOURCE_SNAPSHOT_MISMATCH", record_id=case_id))
    if not _manifest_matches(plan, row.get("arm_manifests")):
        issues.append(ValidationIssue(code="PAIRED_ARM_MANIFEST_MISMATCH", record_id=case_id))
    try:
        denominator = int(row.get("candidate_denominator"))
        if denominator <= 0:
            raise ValueError
    except (TypeError, ValueError):
        issues.append(ValidationIssue(code="PAIRED_CANDIDATE_DENOMINATOR_INVALID", record_id=case_id))

    legacy = str(row.get("legacy_verdict") or "")
    if legacy not in {"VIOLATION", "CLEAN", "INCONCLUSIVE"}:
        issues.append(ValidationIssue(code="PAIRED_LEGACY_OUTCOME_MISSING", record_id=case_id))
    structured_raw = row.get("structured")
    evaluation_raw = structured_raw.get("selected_eligibility_evaluation") if isinstance(structured_raw, dict) else None
    try:
        evaluation = EligibilityEvaluation.model_validate(evaluation_raw)
    except Exception:
        issues.append(ValidationIssue(code="PAIRED_STRUCTURED_EVALUATION_INVALID", record_id=case_id))
        evaluation = None
    if issues or evaluation is None:
        return OncologyPairedRowResult(issues=tuple(sorted(issues, key=lambda issue: issue.code)))

    case = EvaluationCase(
        case_id=case_id, source_snapshot_checksum=snapshot,
        analysis_unit_ref=str(row.get("analysis_unit_ref") or case_id),
        stratum=str(row.get("stratum") or "oncology"),
    )
    repetition = int(row.get("repetition") or 1)
    legacy_evidence = row.get("legacy_evidence") if isinstance(row.get("legacy_evidence"), list) else []
    decisive = _decisive_criteria(evaluation.proof_tree)
    assessment_by_id = {item.criterion_id: item for item in evaluation.criterion_assessments}
    decisive_assessments = [assessment_by_id[item] for item in decisive if item in assessment_by_id]
    grounded = sum(bool(item.evidence_anchors) for item in decisive_assessments)
    resolvable = sum(
        bool(anchor.locator or anchor.anchor)
        for item in decisive_assessments for anchor in item.evidence_anchors[:1]
    )
    b_payload = evaluation.model_dump(mode="json")
    observations = (
        ArmObservation(
            observation_id=f"obs:{case_id}:A:{repetition}", case_id=case_id, arm_id="A",
            repetition=repetition, source_snapshot_checksum=snapshot, outcome=legacy,
            canonical_result_checksum=checksum_value({"verdict": legacy, "evidence": legacy_evidence}),
            technical_status="ok", duration_ms=int(row.get("legacy_duration_ms") or 0),
            token_count=int(row.get("legacy_token_count") or 0),
            decisive_count=1, grounded_decisive_count=int(bool(legacy_evidence)),
            resolvable_locator_count=int(any(isinstance(item, dict) and item.get("locator") for item in legacy_evidence)),
            provenance_complete=bool(row.get("legacy_provenance_complete")), proof_complete=False,
        ),
        ArmObservation(
            observation_id=f"obs:{case_id}:B:{repetition}", case_id=case_id, arm_id="B",
            repetition=repetition, source_snapshot_checksum=snapshot,
            outcome=evaluation.legacy_verdict,
            canonical_result_checksum=checksum_value(b_payload), technical_status="ok",
            duration_ms=int(row.get("structured_duration_ms") or 0),
            decisive_count=len(decisive_assessments), grounded_decisive_count=grounded,
            resolvable_locator_count=min(grounded, resolvable),
            provenance_complete=bool(evaluation.rule_version and evaluation.source_versions),
            proof_complete=bool(evaluation.proof_tree),
            conflict_expected=any(item.state == CriterionState.CONFLICT for item in evaluation.criterion_assessments),
            conflict_detected=evaluation.eligibility_status.value == "CONFLICT" or any(item.state == CriterionState.CONFLICT for item in evaluation.criterion_assessments),
        ),
    )
    gaps = sorted({
        item.criterion_id for item in evaluation.criterion_assessments
        if item.state == CriterionState.UNKNOWN and item.missing_items
    })
    added_evidence_refs = tuple(sorted(
        f"criterion:{item.criterion_id}:anchor:{anchor_index}"
        for item in decisive_assessments
        for anchor_index, _ in enumerate(item.evidence_anchors)
    ))
    delta = CaseDelta(
        case_id=case_id, a_outcome=legacy, b_outcome=evaluation.legacy_verdict,
        eligibility_status=evaluation.eligibility_status.value,
        decisive_criteria=tuple(sorted(decisive)), documentation_gaps=tuple(gaps),
        data_quality_flags=tuple(sorted(evaluation.data_quality_flags)),
        added_evidence_refs=added_evidence_refs,
        version_refs=tuple(sorted({evaluation.rule_version, *evaluation.source_versions} - {""})),
        reason_codes=tuple(sorted({
            "OUTCOME_CHANGED" if legacy != evaluation.legacy_verdict else "OUTCOME_SAME",
            "STRUCTURED_PROOF_AVAILABLE",
        })),
    )
    return OncologyPairedRowResult(case=case, observations=observations, delta=delta)


def build_oncology_paired_package(
    *, evaluation_id: str, plan: EvaluationPlan, rows: list[dict[str, Any]],
    adjudications: tuple[ExpertAdjudication, ...] = (),
) -> EvaluationPackage:
    cases: dict[str, EvaluationCase] = {}
    observations: list[ArmObservation] = []
    deltas: dict[str, CaseDelta] = {}
    adapter_issues: list[ValidationIssue] = []
    for row in rows:
        result = oncology_paired_row(row, plan)
        adapter_issues.extend(result.issues)
        if result.case is not None:
            cases[result.case.case_id] = result.case
        observations.extend(result.observations)
        if result.delta is not None:
            deltas[result.delta.case_id] = result.delta
    report = evaluate_ab(
        evaluation_id=evaluation_id, plan=plan,
        cases=tuple(cases.values()), observations=tuple(observations),
        adjudications=adjudications, case_deltas=tuple(deltas.values()),
    )
    if adapter_issues:
        report = report.model_copy(update={
            "status": EvaluationStatus.INVALID,
            "issues": tuple(sorted((*report.issues, *adapter_issues), key=lambda issue: (issue.code, issue.record_id, issue.path))),
        })
    return EvaluationPackage(
        plan=plan, cases=tuple(sorted(cases.values(), key=lambda item: item.case_id)),
        observations=tuple(sorted(observations, key=lambda item: item.observation_id)),
        adjudications=adjudications, report=report,
    )
