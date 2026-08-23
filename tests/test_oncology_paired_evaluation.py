# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from javert.evidence.evaluation import (
    AcceptanceProfile, ArmVersion, EvaluationPlan, EvaluationStatus, build_evaluation_plan,
    default_thresholds,
)
from javert.evidence.serialization import checksum_value
from javert.oncology.contracts import (
    AuditDisposition, CriterionAssessment, CriterionState, EligibilityEvaluation,
    EligibilityStatus, EvidenceAnchor, ProofNode,
)
from javert.oncology.evaluation_adapter import build_oncology_paired_package

NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)
SNAPSHOT = checksum_value({"snapshot": "paired-synthetic"})
CONFIG = checksum_value({"config": "paired"})


def _plan():
    arm = lambda arm_id, system: ArmVersion(
        arm_id=arm_id, system_version=system, code_commit="abcdef1",
        config_checksum=CONFIG, model_version=system, tool_version=system,
        ontology_version="ontology-1", knowledge_version="knowledge-1",
    )
    return build_evaluation_plan(
        plan_id="plan:oncology:paired", plan_version="1", created_at=NOW,
        source_snapshot_checksum=SNAPSHOT, candidate_query_version="synthetic-v1",
        analysis_unit="patient", arms=(arm("A", "legacy"), arm("B", "structured")),
        repetitions=1, blinded=True, harm_matrix_version="harm-v1",
        harm_matrix={"VIOLATION|INCONCLUSIVE": 0.75},
        profile=AcceptanceProfile.CONFORMANCE,
        thresholds=default_thresholds(AcceptanceProfile.CONFORMANCE),
        signer_roles=("clinical", "engineering"),
    )


def _evaluation():
    assessment = CriterionAssessment(
        criterion_id="dx", criterion_type="diagnosis", state=CriterionState.UNKNOWN,
        evidence_anchors=[EvidenceAnchor(source="synthetic", locator="row:0")],
        missing_items=["diagnosis"], source_version="source-v1",
    )
    return EligibilityEvaluation(
        audit_disposition=AuditDisposition.REVIEW_REQUIRED,
        eligibility_status=EligibilityStatus.DOCUMENTATION_GAP,
        rule_id="SYNTH-RD04", rule_version="1", source_versions=["source-v1"],
        criterion_assessments=[assessment],
        proof_tree=ProofNode(
            node_id="root", operator="leaf", state=CriterionState.UNKNOWN,
            criterion_id="dx", criterion_type="diagnosis", assessment=assessment,
        ),
    )


def _row(*, include_pairing=True):
    row = {
        "case_id": "case:oncology:1", "source_snapshot_checksum": SNAPSHOT,
        "analysis_unit_ref": "patient:synthetic:1", "candidate_denominator": 1,
        "repetition": 1, "legacy_verdict": "VIOLATION",
        "legacy_evidence": [{"locator": "legacy-row"}],
        "legacy_provenance_complete": True,
        "structured": {"selected_eligibility_evaluation": _evaluation().model_dump(mode="json")},
    }
    if include_pairing:
        row["arm_manifests"] = {
            "A": {"code_commit": "abcdef1", "config_checksum": CONFIG},
            "B": {"code_commit": "abcdef1", "config_checksum": CONFIG},
        }
    return row


def test_same_run_oncology_pair_uses_one_patient_outcome_and_explains_delta():
    package = build_oncology_paired_package(
        evaluation_id="evaluation:oncology:1", plan=_plan(), rows=[_row()],
    )
    assert len(package.cases) == 1
    assert len(package.observations) == 2
    assert package.report.status == EvaluationStatus.PASS
    assert package.report.case_deltas[0].a_outcome == "VIOLATION"
    assert package.report.case_deltas[0].b_outcome == "INCONCLUSIVE"
    assert package.report.deployment_authorized is False


def test_missing_pairing_metadata_is_invalid_not_historical_fallback():
    package = build_oncology_paired_package(
        evaluation_id="evaluation:oncology:invalid", plan=_plan(),
        rows=[_row(include_pairing=False)],
    )
    assert package.report.status.value == "INVALID"
    assert any(issue.code == "PAIRED_ARM_MANIFEST_MISMATCH" for issue in package.report.issues)


def test_canonical_paired_fixture_remains_same_run_and_deidentified():
    path = Path(__file__).parent / "fixtures" / "evidence_contract" / "oncology_paired_ab.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    plan = EvaluationPlan.model_validate(raw["plan"])
    package = build_oncology_paired_package(
        evaluation_id=raw["evaluation_id"], plan=plan, rows=raw["rows"],
    )
    assert package.report.status == EvaluationStatus.PASS
    assert package.report.case_deltas[0].reason_codes == (
        "OUTCOME_CHANGED", "STRUCTURED_PROOF_AVAILABLE",
    )


def test_paired_cli_persists_canonical_package(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "evidence_contract" / "oncology_paired_ab.json"
    output = tmp_path / "paired-package"
    env = {**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [sys.executable, "scripts/oncology_paired_evaluation.py", "--input", str(fixture), "--output-dir", str(output)],
        cwd=Path(__file__).parents[1], env=env, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "status=PASS" in completed.stdout
    assert {path.name for path in output.iterdir()} == {
        "evaluation_plan.json", "evaluation_cases.json", "arm_observations.json",
        "expert_adjudications.json", "evaluation_report.json", "manifest.json",
    }
