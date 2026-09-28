#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 142 只读肿瘤患者快照验证全量 authoring 候选；可显式写入工作台结果库。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from javert.audit.result import AuditResult  # noqa: E402
from javert.audit.run_id import new_run_id  # noqa: E402
from javert.audit.runner import _structured_evidence  # noqa: E402
from javert.config import get_config  # noqa: E402
from javert.data import hub_source as hs  # noqa: E402
from javert.data.csv_loader import CsvLoader  # noqa: E402
from javert.oncology.authoring.preview import (  # noqa: E402
    evaluate_authoring_preview_rule,
    load_authoring_preview,
)
from javert.oncology.contracts import EligibilityEvaluation  # noqa: E402
from javert.oncology.runtime import (  # noqa: E402
    _best_evaluation,
    _candidate_service_date,
    _note_records,
    _resolve_regimens,
    _worst_evaluation,
)
from javert.store.audit_store import SqliteStore  # noqa: E402
from javert.store.result_persister import persist_one  # noqa: E402
from javert.tools.drug_audit_lookup import lookup_patient_drugs  # noqa: E402
from scripts.prescan_med_rst import insurance_oncology_drugs  # noqa: E402
from scripts.run_oncology_kb_test_batch import (  # noqa: E402
    _chunks,
    _configure_runtime,
    _existing_batch_counts,
    _selected_frames,
    _write_runtime_snapshot,
)
from scripts.run_oncology_shadow_batch import (  # noqa: E402
    discover_coarse_candidate_ids,
    secure_workspace,
    write_private_csv,
)


_MALIGNANCY_NAME_TOKENS = (
    "恶性肿瘤",
    "癌",
    "淋巴瘤",
    "白血病",
    "骨髓瘤",
    "肉瘤",
    "黑色素瘤",
    "母细胞瘤",
    "胶质瘤",
    "转移瘤",
)
_LUNG_CANCER_NAME_TOKENS = (
    "肺癌",
    "肺腺癌",
    "肺鳞癌",
    "非小细胞肺",
    "小细胞肺",
    "支气管肺癌",
    "肺部恶性肿瘤",
    "肺恶性肿瘤",
)


@dataclass(frozen=True)
class _CohortCandidate:
    patient_id: str
    drug_names: frozenset[str]
    lung_cancer: bool


@dataclass(frozen=True)
class _CohortSelectionStats:
    coarse_candidate_count: int
    confirmed_drug_candidates_scanned: int
    sy_homepage_candidates_scanned: int
    oncology_drug_candidates_scanned: int
    lung_cancer_drug_candidates_scanned: int
    eligible_oncology_candidates_scanned: int
    excluded_non_sy_candidates: int
    excluded_non_oncology_candidates: int
    excluded_missing_note_candidates: int
    selected_lung_cancer_count: int


def _diagnosis_profile(diagnoses: list[dict]) -> tuple[bool, bool]:
    """返回（明确恶性肿瘤，原发肺癌）；不把占位或癌前病变当作肿瘤患者。"""
    has_malignancy = False
    has_lung_cancer = False
    for diagnosis in diagnoses:
        name = str(diagnosis.get("name") or "").replace(" ", "")
        code = re.sub(r"\s+", "", str(diagnosis.get("code") or "")).upper()
        code_match = re.match(r"^C(\d{2})", code)
        malignant_code = bool(code_match and 0 <= int(code_match.group(1)) <= 97)
        lung_code = code.startswith("C34")
        malignant_name = (
            "癌前" not in name
            and any(token in name for token in _MALIGNANCY_NAME_TOKENS)
        )
        lung_name = any(token in name for token in _LUNG_CANCER_NAME_TOKENS)
        has_malignancy = has_malignancy or malignant_code or malignant_name
        has_lung_cancer = has_lung_cancer or lung_code or lung_name
    return has_malignancy or has_lung_cancer, has_lung_cancer


def _choose_cohort_candidates(
    candidates: list[_CohortCandidate],
    *,
    limit: int,
) -> list[_CohortCandidate]:
    """肺癌优先；同一优先层内贪心扩大药品覆盖，最后保持发现顺序稳定。"""
    if limit < 1:
        return []
    selected: list[_CohortCandidate] = []
    seen_drugs: set[str] = set()
    for lung_preference in (True, False):
        remaining = [
            item for item in candidates if item.lung_cancer is lung_preference
        ]
        while remaining and len(selected) < limit:
            best_index = max(
                range(len(remaining)),
                key=lambda index: (
                    len(remaining[index].drug_names - seen_drugs),
                    len(remaining[index].drug_names),
                    -index,
                ),
            )
            item = remaining.pop(best_index)
            selected.append(item)
            seen_drugs.update(item.drug_names)
        if len(selected) == limit:
            break
    return selected


def _sy_homepage_patient_ids(connection, patient_ids: list[str]) -> set[str]:
    """仅认有非空 SYJBK 主诊锚的患者，和 hub_source 的 SY 诊断源选择保持一致。"""
    if not patient_ids:
        return set()
    placeholders = ",".join("?" for _ in patient_ids)
    cursor = connection.cursor()
    cursor.execute(
        "SELECT DISTINCT LTRIM(RTRIM(SYXH)) FROM TB_BA_SYJBK "
        "WHERE NULLIF(LTRIM(RTRIM(COALESCE(SYXH, N''))), N'') IS NOT NULL "
        "AND NULLIF(LTRIM(RTRIM(COALESCE(ZYZD, N''))), N'') IS NOT NULL "
        f"AND LTRIM(RTRIM(SYXH)) IN ({placeholders})",
        tuple(patient_ids),
    )
    return {
        str(row[0]).strip()
        for row in cursor.fetchall()
        if row and str(row[0]).strip()
    }


def _select_stratified_patients(
    connection,
    *,
    limit: int,
    workspace: Path,
    kb_path: Path,
) -> tuple[list[str], _CohortSelectionStats]:
    drugs = insurance_oncology_drugs(kb_path)
    coarse_ids = discover_coarse_candidate_ids(connection, drugs)
    candidates: list[_CohortCandidate] = []
    confirmed = 0
    sy_confirmed = 0
    oncology_confirmed = 0
    lung_confirmed = 0
    excluded_non_sy = 0
    excluded_non_oncology = 0
    excluded_missing_notes = 0
    for chunk_no, chunk in enumerate(_chunks(coarse_ids, size=50), start=1):
        sy_patient_ids = _sy_homepage_patient_ids(connection, chunk)
        fees, notes, diagnoses = _selected_frames(connection, chunk)
        chunk_dir = workspace / f"candidate-{chunk_no:04d}"
        chunk_dir.mkdir(mode=0o700)
        fees_path = chunk_dir / "shi_fee.csv"
        notes_path = chunk_dir / "case_notes.csv"
        zd_path = chunk_dir / "shi_zd.csv"
        write_private_csv(fees_path, fees)
        write_private_csv(notes_path, notes)
        write_private_csv(zd_path, diagnoses)
        loader = CsvLoader(notes_path, fees_path)
        for patient_id in chunk:
            result = lookup_patient_drugs(
                patient_id,
                loader,
                kb_path,
                zd_path,
                rule_type="限适应症",
                source_type="insurance",
                oncology_v2_mode="off",
                audit_rule_id="RD04",
            )
            names = {
                str(item.get("generic_name") or "")
                for item in result.get("matches", [])
                if str(item.get("generic_name") or "")
            }
            if not names:
                continue
            confirmed += 1
            if patient_id not in sy_patient_ids:
                excluded_non_sy += 1
                continue
            sy_confirmed += 1
            has_malignancy, has_lung_cancer = _diagnosis_profile(
                result.get("diagnoses", [])
            )
            if not has_malignancy:
                excluded_non_oncology += 1
                continue
            oncology_confirmed += 1
            lung_confirmed += int(has_lung_cancer)
            if not _note_records(loader.get_notes(patient_id)):
                excluded_missing_notes += 1
                continue
            candidates.append(
                _CohortCandidate(
                    patient_id=patient_id,
                    drug_names=frozenset(names),
                    lung_cancer=has_lung_cancer,
                )
            )
        lung_candidates = [item for item in candidates if item.lung_cancer]
        lung_drugs = {
            drug_name
            for item in lung_candidates
            for drug_name in item.drug_names
        }
        if len(lung_candidates) >= limit and len(lung_drugs) >= min(5, limit):
            break
    selected_candidates = _choose_cohort_candidates(candidates, limit=limit)
    selected = [item.patient_id for item in selected_candidates]
    return selected, _CohortSelectionStats(
        coarse_candidate_count=len(coarse_ids),
        confirmed_drug_candidates_scanned=confirmed,
        sy_homepage_candidates_scanned=sy_confirmed,
        oncology_drug_candidates_scanned=oncology_confirmed,
        lung_cancer_drug_candidates_scanned=lung_confirmed,
        eligible_oncology_candidates_scanned=len(candidates),
        excluded_non_sy_candidates=excluded_non_sy,
        excluded_non_oncology_candidates=excluded_non_oncology,
        excluded_missing_note_candidates=excluded_missing_notes,
        selected_lung_cancer_count=sum(
            item.lung_cancer for item in selected_candidates
        ),
    )


def _cancer_context(diagnoses: list[dict], records: list[dict]) -> str:
    diagnosis_text = " / ".join(str(item.get("name") or "") for item in diagnoses)
    context_text = diagnosis_text + " / " + " ".join(item["text"] for item in records)
    if any(token in context_text for token in ("尿路上皮", "移行细胞癌", "移行上皮癌")):
        return "尿路上皮癌"
    if "弥漫大B" in diagnosis_text or "DLBCL" in diagnosis_text.upper():
        return "弥漫大B细胞淋巴瘤"
    return diagnosis_text


_PREVIEW_STATUS_ZH = {
    "SATISFIED": "当前病历证据满足候选资格分支",
    "NOT_SATISFIED": "当前病历证据明确不满足候选资格分支",
    "DOCUMENTATION_GAP": "关键资格条件缺少可核验文书记载",
    "CONFLICT": "资格条件存在相互矛盾的证据",
}
_PREVIEW_STATE_MARK = {
    "SATISFIED": "✓",
    "NOT_SATISFIED": "✗",
    "UNKNOWN": "？",
    "CONFLICT": "⚠",
}


def _draft_preview_release_id(snapshot_checksum: str) -> str:
    return f"draft-preview-{snapshot_checksum[:16]}"


def _prepare_preview_evaluation(
    evaluation: EligibilityEvaluation,
    *,
    snapshot_checksum: str,
    service_date: date,
) -> EligibilityEvaluation:
    """补齐工作台 scope 合同，并再次验证 DRAFT 不会投影成自动裁决。"""
    return EligibilityEvaluation.model_validate(
        {
            **evaluation.model_dump(mode="json"),
            "release_id": _draft_preview_release_id(snapshot_checksum),
            "evaluated_service_date": service_date.isoformat(),
            "effective_date_enforced": False,
            "audit_disposition": "REVIEW_REQUIRED",
            "legacy_verdict": "INCONCLUSIVE",
            "data_quality_flags": sorted(
                set(
                    evaluation.data_quality_flags
                    + ["DRAFT_RULE_PREVIEW_ONLY", "AUTHORING_REVIEW_REQUIRED"]
                )
            ),
        }
    )


def _aggregate_patient_preview(
    evaluations_by_scope: dict[tuple[str, str], list[EligibilityEvaluation]],
) -> EligibilityEvaluation:
    """每药/政策范围选择最佳 OR 分支，再跨药按最保守状态生成患者级结果。"""
    if not evaluations_by_scope:
        raise ValueError("患者没有可持久化的 DRAFT 资格分支")
    selected_scopes = [
        _best_evaluation(evaluations_by_scope[key])
        for key in sorted(evaluations_by_scope)
    ]
    selected_overall = _worst_evaluation(selected_scopes)
    return EligibilityEvaluation.model_validate(
        {
            **selected_overall.model_dump(
                mode="json",
                exclude={"scope_evaluations"},
            ),
            "scope_evaluations": [
                item.as_scope_evaluation().model_dump(mode="json")
                for item in selected_scopes
            ],
        }
    )


def _preview_reasoning(
    evaluation: EligibilityEvaluation,
    drug_names_by_concept: dict[str, str],
) -> str:
    """给专家看的 DRAFT 说明；不把候选状态表述为合规或违规结论。"""
    lines = [
        "【草稿知识预览｜仅供专家复核】以下逐条核对使用尚未批准的结构化资格规则；"
        "结果统一保持“不明”，不代表自动判定合规或违规。"
    ]
    scopes = evaluation.scope_evaluations or [evaluation]
    for scope in scopes:
        drug_name = drug_names_by_concept.get(
            str(scope.drug_concept_id or ""),
            "本例肿瘤药",
        )
        status = _PREVIEW_STATUS_ZH.get(
            scope.eligibility_status.value,
            "该候选分支需要人工复核",
        )
        lines.extend(
            [
                "",
                f"[{drug_name}｜{scope.policy_scope_display_label or '医保支付限定'}] "
                f"{status}；因知识仍为草稿，本次不自动定性。",
            ]
        )
        for assessment in scope.criterion_assessments:
            mark = _PREVIEW_STATE_MARK.get(assessment.state.value, "·")
            lines.append(f"{mark} {assessment.reason or assessment.criterion_id}")
        if scope.temporal_warning:
            lines.append(f"[时间提示] {scope.temporal_warning}")
    return "\n".join(lines)


def _build_preview_audit_result(
    *,
    patient_id: str,
    evaluation: EligibilityEvaluation,
    drug_names_by_concept: dict[str, str],
    started_at: datetime,
    duration_ms: int,
) -> AuditResult:
    drug_names = sorted(set(drug_names_by_concept.values()))
    evidence = [
        item.model_copy(
            update={
                "text": (
                    "命中肿瘤药收费；已使用 DRAFT 结构化资格分支逐条核对，"
                    "仅供人工复核。"
                )
            }
        )
        if item.source == "drug_audit_lookup"
        else item
        for item in _structured_evidence(evaluation, drug_names)
    ]
    result = AuditResult(
        run_id=new_run_id(),
        rule_id="RD04",
        patient_id=patient_id,
        verdict="INCONCLUSIVE",
        confidence=0.0,
        reasoning=_preview_reasoning(evaluation, drug_names_by_concept),
        evidence=evidence,
        tool_calls=[],
        duration_ms=duration_ms,
        model="deterministic-authoring-preview",
        started_at=started_at,
        eligibility_evaluation=evaluation,
    )
    return AuditResult.model_validate(result.model_dump(mode="json"))


def _persist_preview_results(
    results: list[AuditResult],
    *,
    workspace: Path,
    source_database: str,
    result_database: str,
    batch_tag: str,
) -> tuple[int, int]:
    if not batch_tag or len(batch_tag) > 20:
        raise ValueError("batch_tag 必须为 1..20 个字符")
    _configure_runtime(
        workspace=workspace,
        source_database=source_database,
        result_database=result_database,
        batch_tag=batch_tag,
    )
    cfg = get_config()
    connection = hs.connect(cfg, database=result_database)
    try:
        existing_rows, existing_patients = _existing_batch_counts(
            connection,
            batch_tag,
        )
    finally:
        connection.close()
    if existing_rows or existing_patients:
        raise RuntimeError("目标 batch_tag 已存在，拒绝混入或覆盖既有结果")

    store = SqliteStore(cfg.audit_db_path)
    store.init_schema()
    sync_states: Counter[str] = Counter()
    try:
        for result in results:
            state = persist_one(
                result,
                None,
                triggered_by="cli-authoring-preview",
                sqlite_store=store,
                batch_tag=batch_tag,
            )
            sync_states[str(state.get("sync_state") or "pending")] += 1
    finally:
        store.close()
    if sync_states["synced"] != len(results):
        raise RuntimeError(
            "工作台结果未全部同步："
            f"expected={len(results)}, synced={sync_states['synced']}, "
            f"pending={sync_states['pending']}, skipped={sync_states['skipped']}"
        )

    connection = hs.connect(cfg, database=result_database)
    try:
        result_rows, result_patients = _existing_batch_counts(connection, batch_tag)
    finally:
        connection.close()
    if result_rows != len(results) or result_patients != len(results):
        raise RuntimeError(
            "工作台落库后对账失败："
            f"rows={result_rows}, patients={result_patients}, expected={len(results)}"
        )
    return result_rows, result_patients


def run(
    *,
    limit: int,
    source_database: str,
    output_path: Path,
    result_database: str | None = None,
    batch_tag: str | None = None,
) -> dict:
    if limit < 1 or limit > 100:
        raise ValueError("limit 必须在 1..100")
    if (result_database is None) != (batch_tag is None):
        raise ValueError("写工作台时必须同时提供 result_database 和 batch_tag")
    write_workbench = result_database is not None
    cfg = get_config()
    kb_path = cfg.resolve("configs/drug_audit_kb.json")
    candidates_path = ROOT / "docs/oncology/authoring/oncology_authoring_candidates.json"
    pathology_path = cfg.resolve("configs/pathology_biomarker_kb.json")
    regimen_path = cfg.resolve("configs/oncology_regimen_kb.json")
    preview = load_authoring_preview(candidates_path)
    workbench_results: list[AuditResult] = []
    persisted_result_rows = 0
    persisted_result_patients = 0

    with secure_workspace() as workspace:
        connection = hs.connect(cfg, database=source_database)
        try:
            selected, selection_stats = _select_stratified_patients(
                connection,
                limit=limit,
                workspace=workspace,
                kb_path=kb_path,
            )
            if len(selected) != limit:
                raise RuntimeError(
                    "只读源中同时满足 SY 主诊、肿瘤诊断、病历文本和 RD04 净正收费"
                    f"的患者不足：selected={len(selected)}, required={limit}"
                )
            _write_runtime_snapshot(connection, selected, workspace)
        finally:
            connection.close()

        loader = CsvLoader(workspace / "case_notes.csv", workspace / "shi_fee.csv")
        zd_path = workspace / "shi_zd.csv"
        criterion_states: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        flags: Counter[str] = Counter()
        unknown_reasons: Counter[str] = Counter()
        by_drug: dict[str, Counter[str]] = defaultdict(Counter)
        distinct_drugs: set[str] = set()
        mapped_matches = 0
        unmapped_matches = 0
        ambiguous_matches = 0
        branch_evaluations = 0
        evaluation_errors = 0
        patients_with_notes = 0
        patients_with_diagnoses = 0
        patients_with_oncology_diagnosis = 0
        patients_with_lung_cancer_diagnosis = 0

        for patient_id in selected:
            patient_started_at = datetime.now(timezone.utc)
            patient_started = time.perf_counter()
            patient_evaluations: dict[
                tuple[str, str],
                list[EligibilityEvaluation],
            ] = defaultdict(list)
            patient_drug_names: dict[str, str] = {}
            result = lookup_patient_drugs(
                patient_id,
                loader,
                kb_path,
                zd_path,
                rule_type="限适应症",
                source_type="insurance",
                oncology_v2_mode="off",
                audit_rule_id="RD04",
            )
            diagnoses = result.get("diagnoses", [])
            notes = loader.get_notes(patient_id)
            fees = loader.get_fees(patient_id)
            records = _note_records(notes)
            has_malignancy, has_lung_cancer = _diagnosis_profile(diagnoses)
            if not has_malignancy or not records:
                raise RuntimeError(
                    "入选队列不满足肿瘤诊断和病历文本双门禁"
                )
            patients_with_notes += int(bool(records))
            patients_with_diagnoses += int(bool(diagnoses))
            patients_with_oncology_diagnosis += int(has_malignancy)
            patients_with_lung_cancer_diagnosis += int(has_lung_cancer)
            cancer_context = _cancer_context(diagnoses, records)
            for match in result.get("matches", []):
                generic_name = str(match.get("generic_name") or "")
                distinct_drugs.add(generic_name)
                by_drug[generic_name]["matched_patients"] += 1
                concept_ids = preview.concept_ids_for_match(
                    generic_name=generic_name,
                    codes=match.get("fee_codes", []),
                )
                if not concept_ids:
                    unmapped_matches += 1
                    by_drug[generic_name]["unmapped"] += 1
                    continue
                if len(concept_ids) != 1:
                    ambiguous_matches += 1
                    by_drug[generic_name]["ambiguous"] += 1
                    continue
                mapped_matches += 1
                concept_id = concept_ids[0]
                rules = preview.rules_for_concept(
                    concept_id,
                    policy_scope="INSURANCE_PAYMENT",
                )
                by_drug[generic_name]["available_branches"] = len(rules)
                service_date = _candidate_service_date(fees, match)
                if service_date is None:
                    flags["MISSING_CANDIDATE_SERVICE_DATE"] += 1
                    continue
                regimens = _resolve_regimens(
                    [
                        record
                        for record in records
                        if record["date"] is None or record["date"] <= service_date
                    ],
                    regimen_path=regimen_path,
                    cancer_context=cancer_context,
                    fee_codes=[str(item) for item in match.get("fee_codes", [])],
                    service_date=service_date,
                )
                for rule in rules:
                    try:
                        evaluation = evaluate_authoring_preview_rule(
                            rule,
                            diagnoses=diagnoses,
                            records=records,
                            regimens=regimens,
                            service_date=service_date,
                            pathology_path=pathology_path,
                            cancer_context=cancer_context,
                        )
                        evaluation = _prepare_preview_evaluation(
                            evaluation,
                            snapshot_checksum=preview.snapshot_checksum,
                            service_date=service_date,
                        )
                    except Exception:  # noqa: BLE001
                        evaluation_errors += 1
                        continue
                    policy_scope = str(evaluation.policy_scope or "")
                    if not policy_scope:
                        evaluation_errors += 1
                        continue
                    patient_evaluations[(concept_id, policy_scope)].append(
                        evaluation
                    )
                    patient_drug_names[concept_id] = generic_name
                    branch_evaluations += 1
                    by_drug[generic_name]["evaluated_branches"] += 1
                    statuses[evaluation.eligibility_status.value] += 1
                    for flag in evaluation.data_quality_flags:
                        flags[flag] += 1
                    for item in evaluation.criterion_assessments:
                        criterion_states[item.state.value] += 1
                        if item.state.value == "UNKNOWN":
                            unknown_reasons[item.reason] += 1

            patient_evaluation = _aggregate_patient_preview(patient_evaluations)
            workbench_results.append(
                _build_preview_audit_result(
                    patient_id=patient_id,
                    evaluation=patient_evaluation,
                    drug_names_by_concept=patient_drug_names,
                    started_at=patient_started_at,
                    duration_ms=max(
                        0,
                        int((time.perf_counter() - patient_started) * 1000),
                    ),
                )
            )

        if (
            patients_with_lung_cancer_diagnosis
            != selection_stats.selected_lung_cancer_count
        ):
            raise RuntimeError("肺癌队列选择与运行时复核计数不一致")
        if len(workbench_results) != limit:
            raise RuntimeError("DRAFT 患者级结果构建数量与入选队列不一致")
        if any(
            result.eligibility_evaluation is None
            or "NO_APPROVED_ELIGIBILITY_RULE"
            in result.eligibility_evaluation.data_quality_flags
            or any(
                "NO_APPROVED_ELIGIBILITY_RULE" in scope.data_quality_flags
                for scope in result.eligibility_evaluation.scope_evaluations
            )
            for result in workbench_results
        ):
            raise RuntimeError("DRAFT 工作台结果不得退化为 NO_APPROVED_ELIGIBILITY_RULE")
        if write_workbench:
            persisted_result_rows, persisted_result_patients = (
                _persist_preview_results(
                    workbench_results,
                    workspace=workspace,
                    source_database=source_database,
                    result_database=str(result_database),
                    batch_tag=str(batch_tag),
                )
            )

    payload = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "DRAFT_RULE_PREVIEW_ONLY",
        "source_database": source_database,
        "cohort_policy": {
            "sy_homepage_required": True,
            "oncology_diagnosis_required": True,
            "clinical_note_required": True,
            "lung_cancer_preferred": True,
            "within_priority_drug_diversity_preferred": True,
        },
        "knowledge_database_write": False,
        "result_database_write": write_workbench,
        "result_database": result_database,
        "workbench_batch_tag": batch_tag,
        "persisted_result_rows": persisted_result_rows,
        "persisted_result_patients": persisted_result_patients,
        "candidate_snapshot_checksum": preview.snapshot_checksum,
        "patient_count": limit,
        "coarse_candidate_count": selection_stats.coarse_candidate_count,
        "confirmed_candidates_scanned": (
            selection_stats.confirmed_drug_candidates_scanned
        ),
        "sy_homepage_candidates_scanned": (
            selection_stats.sy_homepage_candidates_scanned
        ),
        "oncology_candidates_scanned": (
            selection_stats.oncology_drug_candidates_scanned
        ),
        "lung_cancer_candidates_scanned": (
            selection_stats.lung_cancer_drug_candidates_scanned
        ),
        "eligible_oncology_candidates_scanned": (
            selection_stats.eligible_oncology_candidates_scanned
        ),
        "excluded_non_sy_candidates": (
            selection_stats.excluded_non_sy_candidates
        ),
        "excluded_non_oncology_candidates": (
            selection_stats.excluded_non_oncology_candidates
        ),
        "excluded_missing_note_candidates": (
            selection_stats.excluded_missing_note_candidates
        ),
        "patients_with_notes": patients_with_notes,
        "patients_with_diagnoses": patients_with_diagnoses,
        "patients_with_sy_homepage_data": len(selected),
        "patients_with_oncology_diagnosis": patients_with_oncology_diagnosis,
        "patients_with_lung_cancer_diagnosis": (
            patients_with_lung_cancer_diagnosis
        ),
        "distinct_drug_count": len(distinct_drugs),
        "mapped_matches": mapped_matches,
        "unmapped_matches": unmapped_matches,
        "ambiguous_matches": ambiguous_matches,
        "branch_evaluations": branch_evaluations,
        "evaluation_errors": evaluation_errors,
        "criterion_states": dict(sorted(criterion_states.items())),
        "eligibility_statuses": dict(sorted(statuses.items())),
        "data_quality_flags": dict(sorted(flags.items())),
        "unknown_reasons": [
            {"reason": reason, "count": count}
            for reason, count in unknown_reasons.most_common(20)
        ],
        "by_drug": {
            drug: dict(sorted(counts.items()))
            for drug, counts in sorted(by_drug.items())
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(output_path, 0o600)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--source-database", default="sh_yb_platform")
    parser.add_argument(
        "--result-database",
        help="显式提供时把患者级 DRAFT 结果写入工作台结果库",
    )
    parser.add_argument(
        "--batch-tag",
        help="与 --result-database 同时提供；最长 20 字符",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "outputs/add-oncology-kb-authoring/authoring_preview_batch.json",
    )
    args = parser.parse_args()
    summary = run(
        limit=args.limit,
        source_database=args.source_database,
        output_path=args.output,
        result_database=args.result_database,
        batch_tag=args.batch_tag,
    )
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "mode",
                    "patient_count",
                    "patients_with_oncology_diagnosis",
                    "patients_with_lung_cancer_diagnosis",
                    "distinct_drug_count",
                    "mapped_matches",
                    "unmapped_matches",
                    "ambiguous_matches",
                    "branch_evaluations",
                    "evaluation_errors",
                    "result_database_write",
                    "persisted_result_patients",
                    "persisted_result_rows",
                )
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
