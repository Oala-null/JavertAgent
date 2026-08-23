# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest

from javert.evidence.diagnosis import (
    DiagnosisConcept, DiagnosisResolver, DiagnosisTerminology, build_terminology, extract_document_diagnoses,
    extract_structured_diagnoses, ontology_ref_for_pack, source_artifact_for_rows,
)
from javert.evidence.diagnosis_evaluation import (
    build_diagnosis_conformance_package, build_diagnosis_conformance_plan,
)
from javert.evidence.evaluation import AcceptanceProfile, EvaluationStatus, MetricStatus
from javert.evidence.ledger import LedgerCorruptionError, SqliteShadowLedger
from javert.evidence.models import CandidateAssertion, EntityRef, OntologyPack
from javert.evidence.projection import rebuild_diagnosis_projection
from javert.evidence.serialization import checksum_value
from javert.evidence.shadow import ValidationLedgerCoordinator, run_shadow_job
from javert.tools.note_diagnosis import extract_diagnoses

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "diagnosis_shadow" / "golden.json"


def _context():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pack = OntologyPack.model_validate(raw["ontology_pack"])
    terminology = DiagnosisTerminology.model_validate(raw["terminology"])
    resolver = DiagnosisResolver(terminology)
    recorded_at = datetime.fromisoformat(raw["recorded_at"].replace("Z", "+00:00"))
    ontology_ref = ontology_ref_for_pack(pack)
    structured_source = source_artifact_for_rows(
        source_artifact_id=raw["structured_source_id"], artifact_version="1",
        artifact_kind="structured", rows=raw["structured_rows"], recorded_at=recorded_at,
    )
    note_source = source_artifact_for_rows(
        source_artifact_id=raw["note_source_id"], artifact_version="1",
        artifact_kind="notes", rows=raw["note_rows"], recorded_at=recorded_at,
    )
    return raw, pack, resolver, recorded_at, ontology_ref, structured_source, note_source


def _extractions():
    raw, pack, resolver, recorded_at, ontology_ref, structured_source, note_source = _context()
    structured = extract_structured_diagnoses(
        raw["structured_rows"], source=structured_source,
        patient_entity_id=raw["patient_entity_id"], resolver=resolver,
        ontology_ref=ontology_ref, recorded_at=recorded_at,
    )
    document = extract_document_diagnoses(
        raw["note_rows"], source=note_source,
        patient_entity_id=raw["patient_entity_id"], resolver=resolver,
        ontology_ref=ontology_ref, recorded_at=recorded_at,
    )
    return raw, pack, structured, document


def test_dual_extractors_keep_exact_spans_unknown_and_old_output():
    raw, _pack, structured, document = _extractions()
    assert len(structured.candidates) == 1
    assert [issue.code for issue in structured.issues] == ["DIAGNOSIS_UNMAPPED"]
    assert [item.bundle.assertions[0].value.value for item in document.candidates] == [
        "TRUE", "UNKNOWN", "FALSE",
    ]
    spans = []
    for candidate in document.candidates:
        evidence = candidate.bundle.evidence_items[0]
        locator = evidence.locator
        spans.append(raw["note_rows"][locator.row_ordinal]["内容"][locator.start_char:locator.end_char])
    assert spans == ["PTC", "疑似甲状腺乳头状癌", "否认PTC"]
    legacy_rows = [{**row, "住院号": raw["legacy_patient_ref"]} for row in raw["note_rows"]]
    assert extract_diagnoses(pd.DataFrame(legacy_rows), raw["legacy_patient_ref"]) == raw["expected_legacy_output"]


def test_same_text_different_row_ordinal_remains_two_occurrences():
    raw, pack, resolver, recorded_at, ontology_ref, _structured_source, _note_source = _context()
    rows = [raw["note_rows"][1], raw["note_rows"][1].copy()]
    source = source_artifact_for_rows(
        source_artifact_id="source:duplicate-notes", artifact_version="1",
        artifact_kind="notes", rows=rows, recorded_at=recorded_at,
    )
    extracted = extract_document_diagnoses(
        rows, source=source, patient_entity_id=raw["patient_entity_id"],
        resolver=resolver, ontology_ref=ontology_ref, recorded_at=recorded_at,
    )
    assert len(extracted.candidates) == 2
    ordinals = [item.bundle.evidence_items[0].locator.row_ordinal for item in extracted.candidates]
    assert ordinals == [0, 1]


def test_coordinator_rejects_illegal_relation_without_raw_payload(tmp_path):
    _raw, pack, structured, _document = _extractions()
    candidate = structured.candidates[0]
    patient = candidate.bundle.entities[0].model_copy(update={"entity_type": "Medication"})
    invalid = CandidateAssertion(
        candidate_id="candidate:illegal-domain",
        assertion_id=candidate.assertion_id,
        bundle=candidate.bundle.model_copy(update={
            "entities": (patient, *candidate.bundle.entities[1:]),
        }),
    )
    with SqliteShadowLedger(tmp_path / "ledger" / "shadow.sqlite") as ledger:
        outcome = ValidationLedgerCoordinator(ledger, pack).submit(invalid)
        assert outcome.status == "rejected"
        assert ledger.stats() == {"accepted": 0, "rejected": 1, "technical": 0}
        payload = list(ledger.iter_events(stream="rejected"))[0].payload
        assert payload["issue_codes"] == ["PREDICATE_DOMAIN_MISMATCH"]
        assert "甲状腺" not in json.dumps(payload, ensure_ascii=False)


def test_ledger_idempotency_concurrency_permissions_and_corruption(tmp_path):
    path = tmp_path / "private" / "shadow.sqlite"
    payload = {"safe": "SYNTH"}
    with SqliteShadowLedger(path) as ledger:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: ledger.append_once(
                    stream="accepted", event_id=f"event:{_}",
                    idempotency_key="same-key", payload=payload,
                ),
                range(2),
            ))
        assert sorted(item.status for item in results) == ["accepted", "duplicate"]
        with pytest.raises(sqlite3.DatabaseError):
            ledger._conn.execute("DELETE FROM shadow_events")  # append-only trigger
    assert (path.parent.stat().st_mode & 0o777) == 0o700
    assert (path.stat().st_mode & 0o777) == 0o600
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER tr_shadow_no_update")
    conn.execute("UPDATE shadow_events SET payload_checksum='sha256:broken'")
    conn.commit()
    conn.close()
    with SqliteShadowLedger(path) as ledger:
        with pytest.raises(LedgerCorruptionError):
            list(ledger.iter_events())


def test_projection_preserves_multi_evidence_and_strict_conflict(tmp_path):
    _raw, pack, structured, document = _extractions()
    with SqliteShadowLedger(tmp_path / "ledger" / "shadow.sqlite") as ledger:
        coordinator = ValidationLedgerCoordinator(ledger, pack)
        for candidate in (*structured.candidates, *document.candidates):
            assert coordinator.submit(candidate).status == "accepted"
        projection = rebuild_diagnosis_projection(list(ledger.iter_events(stream="accepted")))
        replay = rebuild_diagnosis_projection(list(ledger.iter_events(stream="accepted")))
    assert projection == replay
    assert len(projection.items) == 1
    item = projection.items[0]
    assert item.state == "CONFLICT"
    assert len(item.assertion_ids) == 4
    assert len(item.evidence_ids) == 3  # UNKNOWN 不伪造 supports/contradicts
    assert len(item.locators) == 3


def test_unknown_is_coverage_not_negative_fact(tmp_path):
    raw, pack, resolver, recorded_at, ontology_ref, _structured_source, _note_source = _context()
    rows = [{"子阶段": "出院诊断", "内容": "疑似PTC"}]
    source = source_artifact_for_rows(
        source_artifact_id="source:unknown-note", artifact_version="1",
        artifact_kind="notes", rows=rows, recorded_at=recorded_at,
    )
    extracted = extract_document_diagnoses(
        rows, source=source, patient_entity_id=raw["patient_entity_id"],
        resolver=resolver, ontology_ref=ontology_ref, recorded_at=recorded_at,
    )
    with SqliteShadowLedger(tmp_path / "ledger" / "shadow.sqlite") as ledger:
        assert ValidationLedgerCoordinator(ledger, pack).submit(extracted.candidates[0]).status == "accepted"
        item = rebuild_diagnosis_projection(list(ledger.iter_events(stream="accepted"))).items[0]
    assert item.state == "UNKNOWN"
    assert item.evidence_ids == ()
    assert item.coverage_reasons == ("诊断来源已扫描",)


def test_different_diagnoses_coexist_without_automatic_conflict(tmp_path):
    raw, pack, _resolver, recorded_at, ontology_ref, _structured_source, _note_source = _context()
    terminology = build_terminology(
        terminology_id="two-diagnoses", version="1", concept_system="urn:synthetic",
        concept_version="1", concepts=(
            DiagnosisConcept(code="PTC", canonical_name="甲状腺乳头状癌", aliases=("PTC",)),
            DiagnosisConcept(code="THYROID-MALIGNANCY", canonical_name="甲状腺恶性肿瘤"),
        ),
    )
    rows = [
        {"diag_code": "PTC", "diag_name": "甲状腺乳头状癌"},
        {"diag_code": "THYROID-MALIGNANCY", "diag_name": "甲状腺恶性肿瘤"},
    ]
    source = source_artifact_for_rows(
        source_artifact_id="source:two-diagnoses", artifact_version="1",
        artifact_kind="structured", rows=rows, recorded_at=recorded_at,
    )
    extracted = extract_structured_diagnoses(
        rows, source=source, patient_entity_id=raw["patient_entity_id"],
        resolver=DiagnosisResolver(terminology), ontology_ref=ontology_ref,
        recorded_at=recorded_at,
    )
    with SqliteShadowLedger(tmp_path / "ledger" / "shadow.sqlite") as ledger:
        coordinator = ValidationLedgerCoordinator(ledger, pack)
        for candidate in extracted.candidates:
            assert coordinator.submit(candidate).status == "accepted"
        projection = rebuild_diagnosis_projection(list(ledger.iter_events(stream="accepted")))
    assert len(projection.items) == 2
    assert {item.state for item in projection.items} == {"TRUE"}


def test_new_source_version_appends_without_guessing_revision_relation(tmp_path):
    raw, pack, resolver, recorded_at, ontology_ref, _structured_source, _note_source = _context()
    rows = [raw["structured_rows"][0]]
    candidates = []
    for version in ("1", "2"):
        source = source_artifact_for_rows(
            source_artifact_id="source:versioned-diagnosis", artifact_version=version,
            artifact_kind="structured", rows=rows, recorded_at=recorded_at,
        )
        candidates.append(extract_structured_diagnoses(
            rows, source=source, patient_entity_id=raw["patient_entity_id"],
            resolver=resolver, ontology_ref=ontology_ref, recorded_at=recorded_at,
        ).candidates[0])
    with SqliteShadowLedger(tmp_path / "ledger" / "shadow.sqlite") as ledger:
        coordinator = ValidationLedgerCoordinator(ledger, pack)
        assert [coordinator.submit(item).status for item in candidates] == ["accepted", "accepted"]
        events = list(ledger.iter_events(stream="accepted"))
    assert len(events) == 2
    assert all(
        not candidate.bundle.assertions[0].supersedes_assertion_id
        and not candidate.bundle.assertions[0].retracts_assertion_id
        for candidate in candidates
    )


def test_source_failure_is_partial_and_does_not_block_other_source(tmp_path):
    _raw, pack, structured, _document = _extractions()
    with SqliteShadowLedger(tmp_path / "ledger" / "shadow.sqlite") as ledger:
        summary = run_shadow_job(
            coordinator=ValidationLedgerCoordinator(ledger, pack),
            structured_source=lambda: structured,
            note_source=lambda: (_ for _ in ()).throw(RuntimeError("sensitive details")),
        )
        assert summary.status == "partial"
        assert summary.counts["accepted"] == 1
        assert summary.error_codes == ("DIAGNOSIS_UNMAPPED", "DOCUMENT_SOURCE_FAILED")
        assert ledger.stats()["technical"] == 1


def test_sqlite_lock_failure_is_isolated_as_technical_outcome(tmp_path):
    _raw, pack, structured, _document = _extractions()
    path = tmp_path / "ledger" / "shadow.sqlite"
    first = SqliteShadowLedger(path, timeout=0.01)
    second = SqliteShadowLedger(path, timeout=0.01)
    try:
        first._conn.execute("BEGIN EXCLUSIVE")
        outcome = ValidationLedgerCoordinator(second, pack).submit(structured.candidates[0])
        assert outcome.status == "failed"
        assert outcome.issue_codes == ("LEDGER_WRITE_FAILED",)
        first._conn.rollback()
        assert first.stats()["accepted"] == 0
    finally:
        first.close()
        second.close()


def test_diagnosis_evaluation_marks_clinical_metrics_not_applicable(tmp_path):
    raw, pack, structured, document = _extractions()
    with SqliteShadowLedger(tmp_path / "ledger" / "shadow.sqlite") as ledger:
        coordinator = ValidationLedgerCoordinator(ledger, pack)
        for candidate in (*structured.candidates, *document.candidates):
            coordinator.submit(candidate)
        projection = rebuild_diagnosis_projection(list(ledger.iter_events(stream="accepted")))
    snapshot = checksum_value([
        structured.candidates[0].bundle.sources[0].content_checksum,
        document.candidates[0].bundle.sources[0].content_checksum,
    ])
    plan = build_diagnosis_conformance_plan(
        plan_id="plan:diagnosis:test", created_at=datetime.fromisoformat(raw["recorded_at"].replace("Z", "+00:00")),
        source_snapshot_checksum=snapshot, code_commit="abcdef1",
        config_checksum=checksum_value({"config": "synthetic"}), repetitions=2,
    )
    package = build_diagnosis_conformance_package(
        evaluation_id="evaluation:diagnosis:test", plan=plan,
        case_id="case:diagnosis:test", legacy_output=raw["expected_legacy_output"],
        expected_legacy_output=raw["expected_legacy_output"],
        projections=(projection, projection), expected_occurrence_count=1,
    )
    assert package.report.status == EvaluationStatus.PASS
    false_clean = next(item for item in package.report.metrics if item.metric_id == "false_clean" and item.arm_id == "B")
    assert false_clean.status == MetricStatus.NOT_APPLICABLE
    assert not hasattr(package.report, "score")
    promotion = plan.model_copy(update={"profile": AcceptanceProfile.PROMOTION})
    invalid = build_diagnosis_conformance_package(
        evaluation_id="evaluation:diagnosis:invalid", plan=promotion,
        case_id="case:diagnosis:test", legacy_output={}, expected_legacy_output={},
        projections=(projection, projection), expected_occurrence_count=1,
    )
    assert invalid.report.status == EvaluationStatus.INVALID
    assert invalid.report.issues[0].code == "METRIC_NOT_APPLICABLE"


def test_cli_runs_twice_without_growing_accepted_ledger(tmp_path):
    ledger = tmp_path / "ledger" / "shadow.sqlite"
    env = {**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"}
    def invoke(suffix: str):
        return subprocess.run(
            [
                sys.executable, "scripts/diagnosis_evidence_shadow.py",
                "--input", str(FIXTURE), "--ledger", str(ledger),
                "--summary", str(tmp_path / f"summary-{suffix}.json"),
                "--evaluation-output", str(tmp_path / f"evaluation-{suffix}"),
                "--repeat", "2",
            ],
            cwd=ROOT, env=env, text=True, capture_output=True, check=False,
        )
    first = invoke("first")
    assert first.returncode == 0, first.stderr
    assert "evaluation=PASS" in first.stdout
    with SqliteShadowLedger(ledger) as store:
        first_stats = store.stats()
    second = invoke("second")
    assert second.returncode == 0, second.stderr
    with SqliteShadowLedger(ledger) as store:
        second_stats = store.stats()
    assert first_stats == second_stats
    assert first_stats["accepted"] == 4
    assert (
        (tmp_path / "evaluation-first" / "manifest.json").read_bytes()
        == (tmp_path / "evaluation-second" / "manifest.json").read_bytes()
    )
    assert (
        (tmp_path / "evaluation-first" / "evaluation_report.json").read_bytes()
        == (tmp_path / "evaluation-second" / "evaluation_report.json").read_bytes()
    )
    summary = json.loads((tmp_path / "summary-second.json").read_text(encoding="utf-8"))
    assert summary["projection_item_count"] == 1
    assert summary["error_codes"] == ["DIAGNOSIS_UNMAPPED"]


def test_real_source_is_disabled_without_owner_and_retention_policy(tmp_path):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["deidentified"] = False
    input_path = tmp_path / "real-disabled.json"
    input_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("JAVERT_DIAGNOSIS_SHADOW")
        and key != "JAVERT_DIAGNOSIS_EVIDENCE_SHADOW"
    }
    env.update({"PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"})
    completed = subprocess.run(
        [
            sys.executable, "scripts/diagnosis_evidence_shadow.py",
            "--input", str(input_path), "--ledger", str(tmp_path / "ledger.sqlite"),
            "--summary", str(tmp_path / "summary.json"),
            "--evaluation-output", str(tmp_path / "evaluation"),
        ],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )
    assert completed.returncode != 0
    assert "REAL_SHADOW_POLICY_REQUIRED" in completed.stderr
    assert not (tmp_path / "ledger.sqlite").exists()


def test_shadow_modules_do_not_import_authoritative_runtime_or_external_clients():
    paths = [
        ROOT / "src/javert/evidence/diagnosis.py",
        ROOT / "src/javert/evidence/ledger.py",
        ROOT / "src/javert/evidence/shadow.py",
        ROOT / "src/javert/evidence/projection.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in (
        "javert.store", "javert.web", "sqlalchemy", "pyodbc", "requests", "httpx",
        "neo4j", "pymilvus", "kafka",
    ):
        assert forbidden not in text
