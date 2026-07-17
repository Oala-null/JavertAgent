# -*- coding: utf-8 -*-
"""从 RD04 shadow 审计行生成去标识旧/新裁决对照报告."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from javert.oncology.contracts import EligibilityEvaluation
from javert.oncology.knowledge import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "output" / "audit.sqlite"
DEFAULT_OUTPUT = ROOT / "docs" / "oncology" / "shadow_comparison.json"


def _patient_ref(patient_id: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}\0{patient_id}".encode("utf-8")).hexdigest()
    return f"patient-{digest[:12]}"


def _salted_ref(value: str, salt: str, prefix: str) -> str:
    digest = hashlib.sha256(f"{salt}\0{value}".encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12]}"


def _decisive_criteria(evaluation: dict[str, Any]) -> list[str]:
    if not isinstance(evaluation, dict):
        return []
    raw_proof = evaluation.get("proof_tree")
    proof = raw_proof if isinstance(raw_proof, dict) else {}
    found: set[str] = set()

    def visit(node: dict[str, Any], decisive: bool) -> None:
        if not isinstance(node, dict):
            return
        raw_children = node.get("children")
        children = raw_children if isinstance(raw_children, list) else []
        if not children:
            criterion_id = str(node.get("criterion_id") or "")
            if decisive and criterion_id:
                found.add(criterion_id)
            return
        decisive_ids = {
            str(value)
            for value in (
                node.get("decisive_child_ids")
                if isinstance(node.get("decisive_child_ids"), list)
                else []
            )
        }
        for child in children:
            if not isinstance(child, dict):
                continue
            visit(
                child,
                decisive and (
                    not decisive_ids or child.get("node_id") in decisive_ids
                ),
            )

    visit(proof, True)
    if not found:
        decisive_ids = {
            str(value)
            for value in (
                proof.get("decisive_child_ids")
                if isinstance(proof.get("decisive_child_ids"), list)
                else []
            )
        }
        raw_assessments = evaluation.get("criterion_assessments")
        assessments = (
            raw_assessments if isinstance(raw_assessments, list) else []
        )
        found.update(
            str(item.get("criterion_id") or "")
            for item in assessments
            if isinstance(item, dict)
            if item.get("criterion_id") in decisive_ids
        )
    return sorted(found)


def _documentation_gaps(evaluation: dict[str, Any]) -> list[str]:
    if not isinstance(evaluation, dict):
        return []
    raw_suggestions = evaluation.get("documentation_suggestions")
    suggestions = (
        raw_suggestions if isinstance(raw_suggestions, list) else []
    )
    criterion_ids = {
        str(item.get("criterion_id") or "")
        for item in suggestions
        if isinstance(item, dict)
        if item.get("criterion_id")
    }
    raw_assessments = evaluation.get("criterion_assessments")
    assessments = (
        raw_assessments if isinstance(raw_assessments, list) else []
    )
    criterion_ids.update(
        str(item.get("criterion_id") or "")
        for item in assessments
        if isinstance(item, dict)
        if item.get("state") == "UNKNOWN"
        and item.get("missing_items")
        and item.get("criterion_id")
    )
    return sorted(criterion_ids)


def _review_verdict(value: str) -> str:
    return {
        "V": "VIOLATION",
        "C": "CLEAN",
        "I": "INCONCLUSIVE",
        "VIOLATION": "VIOLATION",
        "CLEAN": "CLEAN",
        "INCONCLUSIVE": "INCONCLUSIVE",
    }.get(str(value or "").strip().upper(), "")


def _agreement(left: str, right: str) -> bool | None:
    return left == right if left and right else None


def _shadow_payloads(tool_calls_json: str) -> list[dict[str, Any]]:
    try:
        calls = json.loads(tool_calls_json or "[]")
    except json.JSONDecodeError:
        return []
    return [
        call["structured_output"]
        for call in calls
        if isinstance(call, dict)
        and isinstance(call.get("structured_output"), dict)
        and call["structured_output"].get("mode") == "shadow"
    ]


_SUMMARY_REQUIRED_FIELDS = (
    "confirmed_net_positive_patient_count",
    "candidate_evaluation_count",
    "expected_counts_origin",
    "candidate_query_version",
    "source_query_started_at",
    "source_query_finished_at",
    "source_consistency",
)
_EXPECTED_COUNTS_ORIGIN = "pre_evaluation_lookup_matches"
_EXPECTED_CANDIDATE_QUERY_VERSION = "rd04-hub-candidate-v1"
_EXPECTED_SOURCE_CONSISTENCY = (
    "read_committed_query_window_no_snapshot_isolation"
)
_SUMMARY_PUBLIC_FIELDS = {
    "schema_version",
    "source_query_started_at",
    "source_query_finished_at",
    "source_consistency",
    "candidate_query_version",
    "candidate_query_modes",
    "expected_counts_origin",
    "coarse_candidate_patient_count",
    "confirmed_net_positive_patient_count",
    "candidate_evaluation_count",
}


def _add_validation_error(
    errors: list[dict[str, Any]],
    code: str,
    *,
    patient_ref: str = "",
    scope: str = "",
) -> None:
    error: dict[str, Any] = {"code": code}
    if patient_ref:
        error["patient_ref"] = patient_ref
    if scope:
        error["scope"] = scope
    errors.append(error)


def _validate_source_summary(
    source_summary: dict[str, Any] | None,
    errors: list[dict[str, Any]],
) -> tuple[dict[str, Any], int, int]:
    raw_summary = dict(source_summary or {})
    summary = {
        key: raw_summary[key]
        for key in _SUMMARY_PUBLIC_FIELDS
        if key in raw_summary
    }
    if not source_summary:
        _add_validation_error(errors, "SOURCE_SUMMARY_REQUIRED")
    for field in _SUMMARY_REQUIRED_FIELDS:
        if field not in summary or summary[field] in ("", None):
            _add_validation_error(
                errors,
                "SOURCE_SUMMARY_FIELD_MISSING",
                scope=field,
            )

    def positive_count(field: str) -> int:
        value = summary.get(field)
        if isinstance(value, bool):
            value = None
        try:
            count = int(value)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            _add_validation_error(
                errors,
                "SOURCE_SUMMARY_COUNT_INVALID",
                scope=field,
            )
        return count

    expected_patient_count = positive_count(
        "confirmed_net_positive_patient_count"
    )
    expected_candidate_count = positive_count("candidate_evaluation_count")
    if (
        summary.get("expected_counts_origin")
        != _EXPECTED_COUNTS_ORIGIN
    ):
        _add_validation_error(
            errors,
            "SOURCE_SUMMARY_NOT_INDEPENDENT",
            scope="expected_counts_origin",
        )
    if (
        summary.get("candidate_query_version")
        != _EXPECTED_CANDIDATE_QUERY_VERSION
    ):
        _add_validation_error(
            errors,
            "SOURCE_QUERY_VERSION_UNSUPPORTED",
            scope="candidate_query_version",
        )
    if (
        summary.get("source_consistency")
        != _EXPECTED_SOURCE_CONSISTENCY
    ):
        _add_validation_error(
            errors,
            "SOURCE_CONSISTENCY_UNSUPPORTED",
            scope="source_consistency",
        )
    try:
        started_at = datetime.fromisoformat(
            str(summary["source_query_started_at"]).replace("Z", "+00:00")
        )
        finished_at = datetime.fromisoformat(
            str(summary["source_query_finished_at"]).replace("Z", "+00:00")
        )
        if finished_at < started_at:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        _add_validation_error(
            errors,
            "SOURCE_QUERY_WINDOW_INVALID",
            scope="source_query_started_at/source_query_finished_at",
        )
    return summary, expected_patient_count, expected_candidate_count


def _validate_evaluation(
    evaluation: Any,
    *,
    errors: list[dict[str, Any]],
    patient_ref: str,
    scope: str,
) -> dict[str, Any]:
    if not isinstance(evaluation, dict) or not evaluation:
        _add_validation_error(
            errors,
            "SELECTED_EVALUATION_MISSING",
            patient_ref=patient_ref,
            scope=scope,
        )
        return {}
    for field in (
        "legacy_verdict",
        "audit_disposition",
        "eligibility_status",
        "criterion_assessments",
        "proof_tree",
        "data_quality_flags",
        "documentation_suggestions",
    ):
        if field not in evaluation or evaluation[field] in ("", None):
            _add_validation_error(
                errors,
                "SELECTED_EVALUATION_FIELD_MISSING",
                patient_ref=patient_ref,
                scope=f"{scope}.{field}",
            )
    try:
        validated = EligibilityEvaluation.model_validate(evaluation)
    except Exception:  # Pydantic 细节可能含原文；报告只保留去标识错误码。
        _add_validation_error(
            errors,
            "SELECTED_EVALUATION_SCHEMA_INVALID",
            patient_ref=patient_ref,
            scope=scope,
        )
        return {}
    return validated.model_dump(mode="json")


def build_report(
    rows: Iterable[dict[str, Any]],
    *,
    salt: str,
) -> dict[str, Any]:
    comparisons = []
    ownership_counts: Counter[str] = Counter()
    for row in rows:
        for payload in _shadow_payloads(str(row.get("tool_calls_json") or "")):
            for candidate in payload.get("candidate_evaluations", []):
                evaluation = candidate.get("selected_eligibility_evaluation") or {}
                ownership = str(candidate.get("ownership_key") or "")
                if ownership:
                    ownership_counts[ownership] += 1
                comparisons.append(
                    {
                        "patient_ref": _patient_ref(str(row["patient_id"]), salt),
                        "run_ref": hashlib.sha256(
                            f"{salt}\0{row['run_id']}".encode("utf-8")
                        ).hexdigest()[:12],
                        "drug_concept_id": candidate.get("drug_concept_id", ""),
                        "old_verdict": row.get("verdict", ""),
                        "new_verdict": evaluation.get("legacy_verdict", ""),
                        "audit_disposition": evaluation.get("audit_disposition", ""),
                        "eligibility_status": evaluation.get("eligibility_status", ""),
                        "decisive_criteria": _decisive_criteria(evaluation),
                        "documentation_gaps": _documentation_gaps(evaluation),
                        "data_quality_flags": sorted(
                            evaluation.get("data_quality_flags") or []
                        ),
                        "ownership_ref": (
                            _salted_ref(ownership, salt, "ownership")
                            if ownership
                            else ""
                        ),
                        "expert_review_agreement": None,
                    }
                )

    duplicate_keys = sorted(
        key for key, count in ownership_counts.items() if count > 1
    )
    status = "complete" if comparisons else "blocked"
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "blocked_reason": (
            ""
            if comparisons
            else "未找到 RD04 shadow 运行；需在含 case_notes/shi_fee 的环境先显式批跑 RD04。"
        ),
        "comparison_count": len(comparisons),
        "duplicate_count": len(duplicate_keys),
        "duplicate_ownership_refs": [
            _salted_ref(key, salt, "ownership") for key in duplicate_keys
        ],
        "expert_review_available": False,
        "comparisons": sorted(
            comparisons,
            key=lambda item: (
                item["patient_ref"],
                item["drug_concept_id"],
                item["run_ref"],
            ),
        ),
        "phi_policy": (
            "仅含 salted patient_ref/run_ref/ownership_ref；"
            "不输出患者号、姓名或病历原文。"
        ),
    }


def build_direct_report(
    shadow_runs: Iterable[dict[str, Any]],
    *,
    history_by_patient: dict[str, dict[str, Any]],
    salt: str,
    source_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成严格的直接 shadow 报告。

    旧裁决和专家复核来自历史运行，并非与当前结构化求值共享同一输入快照；
    因而只提供 ``historical_unpaired`` 的探索性对照。完整性分母必须来自
    求值前的独立候选统计，不能用输出自身回填。
    """
    comparisons: list[dict[str, Any]] = []
    patient_comparisons: list[dict[str, Any]] = []
    ownership_counts: Counter[str] = Counter()
    old_verdicts: Counter[str] = Counter()
    new_verdicts: Counter[str] = Counter()
    eligibility_statuses: Counter[str] = Counter()
    old_new_matrix: Counter[tuple[str, str]] = Counter()
    reviewed_patient_count = 0
    expert_new_comparable_count = 0
    expert_old_comparable_count = 0
    expert_new_agreements = 0
    expert_old_agreements = 0
    old_available_patient_count = 0
    validation_errors: list[dict[str, Any]] = []
    summary, expected_patient_count, expected_candidate_count = (
        _validate_source_summary(source_summary, validation_errors)
    )
    runs = list(shadow_runs)
    patient_ids = [
        str(run.get("patient_id") or "")
        if isinstance(run, dict)
        else ""
        for run in runs
    ]
    for patient_id, count in Counter(patient_ids).items():
        if patient_id and count > 1:
            _add_validation_error(
                validation_errors,
                "DUPLICATE_PATIENT",
                patient_ref=_patient_ref(patient_id, salt),
            )

    for run_index, run_value in enumerate(runs):
        run = run_value if isinstance(run_value, dict) else {}
        patient_id = str(run.get("patient_id") or "")
        ref_source = patient_id or f"missing-patient-row-{run_index}"
        patient_ref = _patient_ref(ref_source, salt)
        if not patient_id:
            _add_validation_error(
                validation_errors,
                "PATIENT_ID_MISSING",
                patient_ref=patient_ref,
            )
        structured_value = run.get("structured")
        structured = (
            structured_value if isinstance(structured_value, dict) else {}
        )
        if structured.get("mode") != "shadow":
            _add_validation_error(
                validation_errors,
                "STRUCTURED_MODE_NOT_SHADOW",
                patient_ref=patient_ref,
            )
        if structured.get("error"):
            _add_validation_error(
                validation_errors,
                "STRUCTURED_EVALUATION_ERROR",
                patient_ref=patient_ref,
            )
        if structured.get("no_candidate") is True:
            _add_validation_error(
                validation_errors,
                "CONFIRMED_PATIENT_HAS_NO_CANDIDATE",
                patient_ref=patient_ref,
            )

        history = history_by_patient.get(patient_id) or {}
        old_verdict = str(history.get("verdict") or "")
        old_run_id = str(history.get("run_id") or "")
        if old_verdict:
            old_available_patient_count += 1
            old_verdicts[old_verdict] += 1

        latest_review = history.get("latest_expert_review") or {}
        expert_verdict = _review_verdict(
            str(latest_review.get("review_verdict") or "")
        )
        review_run_old_verdict = str(
            latest_review.get("review_run_old_verdict") or ""
        )
        patient_evaluation = _validate_evaluation(
            structured.get("selected_eligibility_evaluation"),
            errors=validation_errors,
            patient_ref=patient_ref,
            scope="patient_selected_evaluation",
        )
        patient_new_verdict = str(
            patient_evaluation.get("legacy_verdict") or ""
        )
        if patient_new_verdict:
            new_verdicts[patient_new_verdict] += 1
        if old_verdict and patient_new_verdict:
            old_new_matrix[(old_verdict, patient_new_verdict)] += 1
        patient_expert_agreement = _agreement(
            patient_new_verdict, expert_verdict
        )
        old_expert_agreement = _agreement(
            review_run_old_verdict, expert_verdict
        )
        if expert_verdict:
            reviewed_patient_count += 1
        if patient_expert_agreement is not None:
            expert_new_comparable_count += 1
            expert_new_agreements += int(patient_expert_agreement is True)
        if old_expert_agreement is not None:
            expert_old_comparable_count += 1
            expert_old_agreements += int(old_expert_agreement is True)

        candidates_value = structured.get("candidate_evaluations")
        if not isinstance(candidates_value, list):
            _add_validation_error(
                validation_errors,
                "CANDIDATE_EVALUATIONS_INVALID",
                patient_ref=patient_ref,
            )
            candidates: list[Any] = []
        else:
            candidates = candidates_value
        if not candidates:
            _add_validation_error(
                validation_errors,
                "CANDIDATE_EVALUATIONS_EMPTY",
                patient_ref=patient_ref,
            )
        patient_comparisons.append(
            {
                "patient_ref": patient_ref,
                "run_ref": _salted_ref(
                    old_run_id or f"direct-shadow:{patient_id}",
                    salt,
                    "run",
                ),
                "candidate_count": len(candidates),
                "old_verdict": old_verdict,
                "old_verdict_available": bool(old_verdict),
                "new_verdict": patient_new_verdict,
                "eligibility_status": patient_evaluation.get(
                    "eligibility_status", ""
                ),
                "decisive_criteria": _decisive_criteria(
                    patient_evaluation
                ),
                "documentation_gaps": _documentation_gaps(
                    patient_evaluation
                ),
                "expert_review_verdict": expert_verdict,
                "expert_review_new_agreement": patient_expert_agreement,
                "expert_review_old_agreement": old_expert_agreement,
                "expert_review_ref": (
                    _salted_ref(
                        str(latest_review.get("run_id") or ""),
                        salt,
                        "review-run",
                    )
                    if latest_review.get("run_id")
                    else ""
                ),
            }
        )

        for candidate_index, candidate_value in enumerate(candidates):
            candidate = (
                candidate_value
                if isinstance(candidate_value, dict)
                else {}
            )
            scope = f"candidate_evaluations[{candidate_index}]"
            if not isinstance(candidate_value, dict):
                _add_validation_error(
                    validation_errors,
                    "CANDIDATE_INVALID",
                    patient_ref=patient_ref,
                    scope=scope,
                )
            evaluation = _validate_evaluation(
                candidate.get("selected_eligibility_evaluation"),
                errors=validation_errors,
                patient_ref=patient_ref,
                scope=f"{scope}.selected_eligibility_evaluation",
            )
            ownership = str(candidate.get("ownership_key") or "").strip()
            if ownership:
                ownership_counts[ownership] += 1
            else:
                _add_validation_error(
                    validation_errors,
                    "CANDIDATE_OWNERSHIP_MISSING",
                    patient_ref=patient_ref,
                    scope=scope,
                )
            drug_concept_id = str(
                candidate.get("drug_concept_id") or ""
            ).strip()
            generic_name = str(
                candidate.get("generic_name") or ""
            ).strip()
            if not (drug_concept_id or generic_name):
                _add_validation_error(
                    validation_errors,
                    "CANDIDATE_DRUG_IDENTITY_MISSING",
                    patient_ref=patient_ref,
                    scope=scope,
                )
            status = str(evaluation.get("eligibility_status") or "")
            if status:
                eligibility_statuses[status] += 1
            comparisons.append(
                {
                    "patient_ref": patient_ref,
                    "run_ref": _salted_ref(
                        old_run_id or f"direct-shadow:{patient_id}",
                        salt,
                        "run",
                    ),
                    "drug_concept_id": drug_concept_id,
                    "generic_name": generic_name,
                    "old_verdict": old_verdict,
                    "old_verdict_available": bool(old_verdict),
                    "new_verdict": evaluation.get(
                        "legacy_verdict", ""
                    ),
                    "audit_disposition": evaluation.get(
                        "audit_disposition", ""
                    ),
                    "eligibility_status": status,
                    "decisive_criteria": _decisive_criteria(evaluation),
                    "documentation_gaps": _documentation_gaps(evaluation),
                    "data_quality_flags": sorted(
                        evaluation.get("data_quality_flags") or []
                    ),
                    "ownership_ref": (
                        _salted_ref(ownership, salt, "ownership")
                        if ownership
                        else ""
                    ),
                }
            )

    duplicate_keys = sorted(
        key for key, count in ownership_counts.items() if count > 1
    )
    for key in duplicate_keys:
        _add_validation_error(
            validation_errors,
            "DUPLICATE_OWNERSHIP",
            scope=_salted_ref(key, salt, "ownership"),
        )
    patient_count = len(patient_comparisons)
    candidate_evaluation_count = len(comparisons)
    if patient_count != expected_patient_count:
        _add_validation_error(
            validation_errors,
            "PATIENT_COUNT_MISMATCH",
            scope="confirmed_net_positive_patient_count",
        )
    if candidate_evaluation_count != expected_candidate_count:
        _add_validation_error(
            validation_errors,
            "CANDIDATE_COUNT_MISMATCH",
            scope="candidate_evaluation_count",
        )
    patient_coverage_rate = (
        patient_count / expected_patient_count if expected_patient_count else 0.0
    )
    evaluation_coverage_rate = (
        candidate_evaluation_count / expected_candidate_count
        if expected_candidate_count
        else 0.0
    )
    evaluation_error_codes = {
        "STRUCTURED_EVALUATION_ERROR",
        "SELECTED_EVALUATION_MISSING",
        "SELECTED_EVALUATION_FIELD_MISSING",
        "SELECTED_EVALUATION_SCHEMA_INVALID",
        "CANDIDATE_EVALUATIONS_INVALID",
        "CANDIDATE_EVALUATIONS_EMPTY",
        "CANDIDATE_INVALID",
        "CANDIDATE_OWNERSHIP_MISSING",
        "CANDIDATE_DRUG_IDENTITY_MISSING",
    }
    evaluation_error_count = sum(
        error["code"] in evaluation_error_codes
        for error in validation_errors
    )
    validation_errors = sorted(
        validation_errors,
        key=lambda item: (
            item["code"],
            item.get("patient_ref", ""),
            item.get("scope", ""),
        ),
    )
    complete = (
        bool(comparisons)
        and patient_count == expected_patient_count
        and candidate_evaluation_count == expected_candidate_count
        and not validation_errors
    )
    status = "complete" if complete else "blocked"
    old_new_comparable_patient_count = sum(old_new_matrix.values())
    return {
        "schema_version": "1.2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "blocked_reason": (
            ""
            if complete
            else (
                "当前候选 shadow 覆盖不完整或存在结构化求值错误；"
                "不得作为激活验收结论。"
            )
        ),
        "source": (
            "数据中台当前净正收费候选的结构化 shadow 求值；"
            "旧裁决来自结果库每患者最新历史 RD04，二者不是同输入配对实验。"
        ),
        "comparison_design": "historical_unpaired",
        "comparison_interpretation": (
            "旧裁决与当前结构化求值可能使用不同数据快照、规则和候选集合；"
            "矩阵仅作方向性历史对照，不代表 paired shadow。"
        ),
        "source_summary": summary,
        "patient_count": patient_count,
        "candidate_evaluation_count": candidate_evaluation_count,
        "comparison_count": candidate_evaluation_count,
        "old_new_comparable_patient_count": (
            old_new_comparable_patient_count
        ),
        "evaluation_error_count": evaluation_error_count,
        "validation_error_count": len(validation_errors),
        "validation_errors": validation_errors,
        "patient_coverage_rate": patient_coverage_rate,
        "evaluation_coverage_rate": evaluation_coverage_rate,
        "old_verdict_available_patient_count": old_available_patient_count,
        "old_verdict_missing_patient_count": (
            patient_count - old_available_patient_count
        ),
        "old_verdict_distribution": dict(sorted(old_verdicts.items())),
        "new_verdict_distribution": dict(sorted(new_verdicts.items())),
        "old_new_verdict_matrix": {
            f"{old}->{new}": count
            for (old, new), count in sorted(old_new_matrix.items())
        },
        "unchanged_verdict_patient_count": sum(
            count
            for (old, new), count in old_new_matrix.items()
            if old == new
        ),
        "changed_verdict_patient_count": sum(
            count
            for (old, new), count in old_new_matrix.items()
            if old != new
        ),
        "eligibility_status_distribution": dict(
            sorted(eligibility_statuses.items())
        ),
        "duplicate_count": len(duplicate_keys),
        "duplicate_ownership_refs": [
            _salted_ref(key, salt, "ownership") for key in duplicate_keys
        ],
        "expert_review_available": reviewed_patient_count > 0,
        "expert_review_interpretation": (
            "exploratory_historical_unpaired"
        ),
        "expert_reviewed_patient_count": reviewed_patient_count,
        "expert_review_new_comparable_count": expert_new_comparable_count,
        "expert_review_new_agreement_count": expert_new_agreements,
        "expert_review_new_agreement_rate": (
            expert_new_agreements / expert_new_comparable_count
            if expert_new_comparable_count
            else None
        ),
        "expert_review_old_comparable_count": expert_old_comparable_count,
        "expert_review_old_agreement_count": expert_old_agreements,
        "expert_review_old_agreement_rate": (
            expert_old_agreements / expert_old_comparable_count
            if expert_old_comparable_count
            else None
        ),
        "patient_comparisons": sorted(
            patient_comparisons,
            key=lambda item: item["patient_ref"],
        ),
        "comparisons": sorted(
            comparisons,
            key=lambda item: (
                item["patient_ref"],
                item["generic_name"],
                item["ownership_ref"],
            ),
        ),
        "phi_policy": (
            "仅含 salted patient_ref/run_ref/ownership_ref/review-run ref；"
            "不输出患者号、姓名、文书原文、证据锚点或未盐化运行/所有权标识。"
        ),
    }


def load_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT run_id, patient_id, verdict, tool_calls_json "
                "FROM audit_runs WHERE rule_id = 'RD04' ORDER BY started_at"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--shadow-input",
        type=Path,
        help="当前候选直接 shadow 临时 JSON；设置后不读取 --db。",
    )
    parser.add_argument(
        "--history-input",
        type=Path,
        help="按原始 patient_id 键控的历史 RD04/专家复核临时 JSON。",
    )
    parser.add_argument(
        "--source-summary",
        type=Path,
        help="不含 PHI 的候选全集统计 JSON。",
    )
    parser.add_argument(
        "--salt",
        required=True,
        help="去标识 salt；正式环境应从受控环境变量传入。",
    )
    args = parser.parse_args()
    if args.shadow_input:
        shadow_runs = json.loads(
            args.shadow_input.read_text(encoding="utf-8")
        )
        history = (
            json.loads(args.history_input.read_text(encoding="utf-8"))
            if args.history_input
            else {}
        )
        source_summary = (
            json.loads(args.source_summary.read_text(encoding="utf-8"))
            if args.source_summary
            else {}
        )
        report = build_direct_report(
            shadow_runs,
            history_by_patient=history,
            salt=args.salt,
            source_summary=source_summary,
        )
    else:
        report = build_report(load_rows(args.db), salt=args.salt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(report))
    print(
        f"{args.output} status={report['status']} "
        f"comparisons={report['comparison_count']} duplicates={report['duplicate_count']}"
    )
    if report["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
