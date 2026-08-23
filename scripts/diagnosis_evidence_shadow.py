# -*- coding: utf-8 -*-
"""完全显式、默认不接生产的 Diagnosis Evidence shadow CLI。"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from javert.evidence.diagnosis import (
    DiagnosisResolver, DiagnosisTerminology, extract_document_diagnoses,
    extract_structured_diagnoses, ontology_ref_for_pack, source_artifact_for_rows,
)
from javert.evidence.diagnosis_evaluation import (
    build_diagnosis_conformance_package, build_diagnosis_conformance_plan,
)
from javert.evidence.evaluation import persist_evaluation_package
from javert.evidence.ledger import SqliteShadowLedger
from javert.evidence.models import OntologyPack
from javert.evidence.privacy import privacy_issues
from javert.evidence.projection import rebuild_diagnosis_projection
from javert.evidence.serialization import canonical_json_bytes, checksum_value, sha256_digest
from javert.evidence.shadow import ValidationLedgerCoordinator, run_shadow_job
from javert.tools.note_diagnosis import extract_diagnoses


def _inside_git(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (path.parent, *path.parents))


def _real_gate(raw: dict, ledger_path: Path) -> None:
    if raw.get("deidentified") is True:
        return
    required = {
        "JAVERT_DIAGNOSIS_EVIDENCE_SHADOW": "on",
        "JAVERT_DIAGNOSIS_SHADOW_OWNER": None,
        "JAVERT_DIAGNOSIS_SHADOW_RETENTION_DAYS": None,
    }
    for key, expected in required.items():
        value = os.environ.get(key, "")
        if not value or (expected is not None and value != expected):
            raise RuntimeError("REAL_SHADOW_POLICY_REQUIRED")
    try:
        if int(os.environ["JAVERT_DIAGNOSIS_SHADOW_RETENTION_DAYS"]) <= 0:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("REAL_SHADOW_POLICY_REQUIRED") from exc
    safe_ref = re.compile(r"^(?:entity:patient:)?anon-[0-9a-f]{12,64}$")
    if not safe_ref.fullmatch(str(raw.get("patient_entity_id") or "")):
        raise RuntimeError("REAL_SHADOW_DEIDENTIFIED_REF_REQUIRED")
    if not safe_ref.fullmatch(str(raw.get("legacy_patient_ref") or "")):
        raise RuntimeError("REAL_SHADOW_DEIDENTIFIED_REF_REQUIRED")
    if _inside_git(ledger_path):
        raise RuntimeError("REAL_SHADOW_GIT_PATH_FORBIDDEN")


def run(
    *, input_path: Path, ledger_path: Path, summary_path: Path,
    evaluation_output: Path, repeat: int,
) -> dict:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    privacy = privacy_issues(raw)
    if privacy:
        raise RuntimeError(f"FIXTURE_PRIVACY_FAILED:{privacy[0].path}")
    _real_gate(raw, ledger_path)
    if repeat < 2:
        raise ValueError("golden/conformance 至少重复两次")
    pack = OntologyPack.model_validate(raw["ontology_pack"])
    terminology = DiagnosisTerminology.model_validate(raw["terminology"])
    resolver = DiagnosisResolver(terminology)
    recorded_at = datetime.fromisoformat(str(raw["recorded_at"]).replace("Z", "+00:00"))
    structured_rows = list(raw.get("structured_rows") or [])
    note_rows = list(raw.get("note_rows") or [])
    structured_source = source_artifact_for_rows(
        source_artifact_id=str(raw["structured_source_id"]),
        artifact_version=str(raw["source_version"]), artifact_kind="structured-diagnoses",
        rows=structured_rows, recorded_at=recorded_at,
    )
    note_source = source_artifact_for_rows(
        source_artifact_id=str(raw["note_source_id"]),
        artifact_version=str(raw["source_version"]), artifact_kind="clinical-notes",
        rows=note_rows, recorded_at=recorded_at,
    )
    ontology_ref = ontology_ref_for_pack(pack)
    patient_entity_id = str(raw["patient_entity_id"])
    source_snapshot_checksum = checksum_value([
        structured_source.content_checksum, note_source.content_checksum,
    ])
    projections = []
    run_summaries = []
    with SqliteShadowLedger(ledger_path) as ledger:
        coordinator = ValidationLedgerCoordinator(
            ledger, pack,
            source_material={
                structured_source.source_artifact_id: structured_rows,
                note_source.source_artifact_id: note_rows,
            },
        )
        for _ in range(repeat):
            summary = run_shadow_job(
                coordinator=coordinator,
                structured_source=lambda: extract_structured_diagnoses(
                    structured_rows, source=structured_source,
                    patient_entity_id=patient_entity_id, resolver=resolver,
                    ontology_ref=ontology_ref, recorded_at=recorded_at,
                ),
                note_source=lambda: extract_document_diagnoses(
                    note_rows, source=note_source,
                    patient_entity_id=patient_entity_id, resolver=resolver,
                    ontology_ref=ontology_ref, recorded_at=recorded_at,
                ),
            )
            run_summaries.append(summary)
            projections.append(rebuild_diagnosis_projection(list(ledger.iter_events(stream="accepted"))))
        stats = ledger.stats()
    legacy_patient_ref = str(raw["legacy_patient_ref"])
    legacy_rows = [{**row, "住院号": legacy_patient_ref} for row in note_rows]
    legacy_output = extract_diagnoses(pd.DataFrame(legacy_rows), legacy_patient_ref)
    expected_legacy = dict(raw["expected_legacy_output"])
    plan = build_diagnosis_conformance_plan(
        plan_id=str(raw["evaluation_plan_id"]), created_at=recorded_at,
        source_snapshot_checksum=source_snapshot_checksum,
        code_commit=str(raw["code_commit"]),
        config_checksum=checksum_value({
            "terminology": terminology.content_checksum,
            "ontology": pack.content_checksum,
        }),
        repetitions=repeat,
    )
    package = build_diagnosis_conformance_package(
        evaluation_id=str(raw["evaluation_id"]), plan=plan,
        case_id=str(raw["evaluation_case_id"]), legacy_output=legacy_output,
        expected_legacy_output=expected_legacy, projections=tuple(projections),
        expected_occurrence_count=int(raw["expected_projection_item_count"]),
    )
    manifest = persist_evaluation_package(
        package, evaluation_output, real_case=raw.get("deidentified") is not True,
    )
    output = {
        "schema_version": "0.1.0",
        "status": run_summaries[-1].status,
        "runs": [item.model_dump(mode="json") for item in run_summaries],
        "ledger_stats": stats,
        "projection_checksum": projections[-1].canonical_checksum,
        "projection_item_count": len(projections[-1].items),
        "evaluation_status": package.report.status.value,
        "evaluation_manifest_checksum": sha256_digest(manifest),
        "error_codes": sorted({code for item in run_summaries for code in item.error_codes}),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(summary_path.parent, 0o700)
    summary_path.write_bytes(canonical_json_bytes(output))
    os.chmod(summary_path, 0o600)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="运行显式 Diagnosis Evidence shadow")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--evaluation-output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=2)
    args = parser.parse_args()
    output = run(
        input_path=args.input, ledger_path=args.ledger,
        summary_path=args.summary, evaluation_output=args.evaluation_output,
        repeat=args.repeat,
    )
    print(
        f"status={output['status']} accepted={output['ledger_stats']['accepted']} "
        f"duplicates={sum(item['counts']['duplicate'] for item in output['runs'])} "
        f"evaluation={output['evaluation_status']}"
    )
    if output["status"] == "failed" or output["evaluation_status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
