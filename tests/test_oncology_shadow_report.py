# -*- coding: utf-8 -*-
"""RD04 shadow 严格验收、候选口径和 PHI 临时文件回归."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import scripts.run_oncology_shadow_batch as shadow_batch
from scripts.oncology_shadow_report import build_direct_report
from scripts.run_oncology_shadow_batch import (
    CANDIDATE_QUERY_VERSION,
    EXPECTED_COUNTS_ORIGIN,
    build_run_manifest,
    discover_coarse_candidate_ids,
    git_state,
    secure_workspace,
    write_private_csv,
    write_private_json,
)


def _assessment() -> dict:
    return {
        "criterion_id": "criterion-1",
        "criterion_type": "diagnosis",
        "state": "SATISFIED",
        "expected_condition": {"contains": "测试癌种"},
        "normalized_facts": [],
        "evidence_anchors": [],
        "reason": "合成事实",
        "missing_items": [],
    }


def _evaluation() -> dict:
    assessment = _assessment()
    return {
        "legacy_verdict": "CLEAN",
        "audit_disposition": "NO_VIOLATION_FOUND",
        "eligibility_status": "SATISFIED",
        "rule_id": "synthetic-rule",
        "rule_version": "2026.1",
        "indication_branch_id": "synthetic-branch",
        "source_versions": ["synthetic-source@1"],
        "criterion_assessments": [assessment],
        "proof_tree": {
            "node_id": "criterion-1",
            "operator": "leaf",
            "state": "SATISFIED",
            "criterion_type": "diagnosis",
            "criterion_id": "criterion-1",
            "children": [],
            "assessment": assessment,
        },
        "data_quality_flags": [],
        "documentation_suggestions": [],
    }


def _source_summary(*, patients: int = 1, candidates: int = 1) -> dict:
    return {
        "schema_version": "1.0.0",
        "source_query_started_at": "2026-07-17T12:00:00Z",
        "source_query_finished_at": "2026-07-17T12:01:00Z",
        "source_consistency": (
            "read_committed_query_window_no_snapshot_isolation"
        ),
        "candidate_query_version": CANDIDATE_QUERY_VERSION,
        "candidate_query_modes": [
            "national_code_exact",
            "uncoded_full_generic_name",
        ],
        "expected_counts_origin": EXPECTED_COUNTS_ORIGIN,
        "coarse_candidate_patient_count": patients,
        "confirmed_net_positive_patient_count": patients,
        "candidate_evaluation_count": candidates,
    }


def _shadow_run(patient_id: str = "SYNTHETIC-PATIENT") -> dict:
    evaluation = _evaluation()
    return {
        "patient_id": patient_id,
        "structured": {
            "mode": "shadow",
            "candidate_evaluations": [
                {
                    "generic_name": "合成测试药",
                    "drug_concept_id": "synthetic-drug",
                    "ownership_key": f"ownership:{patient_id}",
                    "selected_eligibility_evaluation": evaluation,
                }
            ],
            "selected_eligibility_evaluation": evaluation,
        },
    }


def test_direct_report_requires_independent_counts_and_complete_shadow():
    patient_id = "SYNTHETIC-PATIENT"
    source_summary = _source_summary()
    source_summary["patient_id"] = patient_id
    report = build_direct_report(
        [_shadow_run(patient_id)],
        history_by_patient={
            patient_id: {
                "run_id": "historical-run",
                "verdict": "VIOLATION",
            }
        },
        salt="unit-test-private-salt",
        source_summary=source_summary,
    )

    encoded = json.dumps(report, ensure_ascii=False)
    assert report["status"] == "complete"
    assert report["validation_error_count"] == 0
    assert report["candidate_evaluation_count"] == 1
    assert report["old_new_comparable_patient_count"] == 1
    assert report["comparison_design"] == "historical_unpaired"
    assert (
        report["expert_review_interpretation"]
        == "exploratory_historical_unpaired"
    )
    assert patient_id not in encoded
    assert "historical-run" not in encoded
    assert "patient_id" not in report["source_summary"]


def test_mode_on_empty_candidate_can_never_report_complete():
    report = build_direct_report(
        [
            {
                "patient_id": "SYNTHETIC-PATIENT",
                "structured": {
                    "mode": "on",
                    "candidate_evaluations": [],
                    "selected_eligibility_evaluation": None,
                },
            }
        ],
        history_by_patient={},
        salt="unit-test-private-salt",
        source_summary=_source_summary(),
    )

    codes = {item["code"] for item in report["validation_errors"]}
    assert report["status"] == "blocked"
    assert report["validation_error_count"] > 0
    assert "STRUCTURED_MODE_NOT_SHADOW" in codes
    assert "CANDIDATE_EVALUATIONS_EMPTY" in codes
    assert "SELECTED_EVALUATION_MISSING" in codes
    assert "CANDIDATE_COUNT_MISMATCH" in codes


def test_missing_source_summary_and_candidate_ownership_are_blocking():
    run = _shadow_run()
    del run["structured"]["candidate_evaluations"][0]["ownership_key"]
    report = build_direct_report(
        [run],
        history_by_patient={},
        salt="unit-test-private-salt",
        source_summary=None,
    )

    codes = {item["code"] for item in report["validation_errors"]}
    assert report["status"] == "blocked"
    assert "SOURCE_SUMMARY_REQUIRED" in codes
    assert "SOURCE_SUMMARY_NOT_INDEPENDENT" in codes
    assert "CANDIDATE_OWNERSHIP_MISSING" in codes


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("proof_tree", "SENSITIVE_RAW_NOTE_SENTINEL"),
        (
            "documentation_suggestions",
            {"criterion_id": "SENSITIVE_RAW_NOTE_SENTINEL"},
        ),
        (
            "criterion_assessments",
            {"criterion_id": "SENSITIVE_RAW_NOTE_SENTINEL"},
        ),
    ],
)
def test_nested_evaluation_schema_mutations_fail_closed(
    field: str,
    invalid_value: object,
):
    run = _shadow_run()
    run["structured"]["selected_eligibility_evaluation"][field] = (
        invalid_value
    )
    run["structured"]["selected_eligibility_evaluation"][
        "data_quality_flags"
    ] = ["SENSITIVE_RAW_NOTE_SENTINEL"]
    report = build_direct_report(
        [run],
        history_by_patient={},
        salt="unit-test-private-salt",
        source_summary=_source_summary(),
    )

    codes = {item["code"] for item in report["validation_errors"]}
    assert report["status"] == "blocked"
    assert "SELECTED_EVALUATION_SCHEMA_INVALID" in codes
    assert report["evaluation_error_count"] >= 1
    assert report["comparisons"][0]["new_verdict"] == ""
    assert report["comparisons"][0]["decisive_criteria"] == []
    assert report["comparisons"][0]["documentation_gaps"] == []
    assert report["comparisons"][0]["data_quality_flags"] == []
    encoded = json.dumps(report, ensure_ascii=False)
    assert "SENSITIVE_RAW_NOTE_SENTINEL" not in encoded


@pytest.mark.parametrize(
    (
        "ownership_key",
        "drug_concept_id",
        "generic_name",
        "expected_status",
        "expected_error",
    ),
    [
        (
            "   ",
            "synthetic-drug",
            "合成测试药",
            "blocked",
            "CANDIDATE_OWNERSHIP_MISSING",
        ),
        ("ownership", "   ", "合成测试药", "complete", None),
        (
            "ownership",
            "   ",
            "   ",
            "blocked",
            "CANDIDATE_DRUG_IDENTITY_MISSING",
        ),
    ],
)
def test_candidate_identity_whitespace_gate(
    ownership_key: str,
    drug_concept_id: str,
    generic_name: str,
    expected_status: str,
    expected_error: str | None,
):
    run = _shadow_run()
    candidate = run["structured"]["candidate_evaluations"][0]
    candidate["ownership_key"] = ownership_key
    candidate["drug_concept_id"] = drug_concept_id
    candidate["generic_name"] = generic_name
    report = build_direct_report(
        [run],
        history_by_patient={},
        salt="unit-test-private-salt",
        source_summary=_source_summary(),
    )

    codes = {item["code"] for item in report["validation_errors"]}
    assert report["status"] == expected_status
    if expected_error:
        assert expected_error in codes
    else:
        assert "CANDIDATE_DRUG_IDENTITY_MISSING" not in codes
        assert report["comparisons"][0]["drug_concept_id"] == ""
        assert report["comparisons"][0]["generic_name"] == "合成测试药"


def test_secure_workspace_uses_private_permissions_and_cleans_up(
    tmp_path: Path,
):
    with secure_workspace(tmp_path) as workspace:
        json_path = workspace / "raw.json"
        csv_path = workspace / "raw.csv"
        write_private_json(json_path, {"patient_id": "PRIVATE-ID"})
        write_private_csv(
            csv_path,
            pd.DataFrame([{"patient_id": "PRIVATE-ID"}]),
        )
        retained_path = workspace
        assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
        assert stat.S_IMODE(json_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(csv_path.stat().st_mode) == 0o600
    assert not retained_path.exists()


class _FakeCursor:
    description = [("patient_id",)]

    def __init__(self) -> None:
        self.sql = ""
        self.params: tuple[str, ...] = ()

    def execute(self, sql: str, params: tuple[str, ...]) -> None:
        self.sql = sql
        self.params = params

    @staticmethod
    def fetchall() -> list[tuple[str]]:
        return [("SYNTHETIC-PATIENT",)]


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance


def test_candidate_query_has_code_and_uncoded_full_name_paths():
    connection = _FakeConnection()
    result = discover_coarse_candidate_ids(
        connection,
        {"完整通用名": {"NATIONAL-CODE"}},
    )

    assert result == ["SYNTHETIC-PATIENT"]
    assert "MXXMBMYB" in connection.cursor_instance.sql
    assert "MXXMMC LIKE" in connection.cursor_instance.sql
    assert "LOWER(" in connection.cursor_instance.sql
    assert "N'nan', N'none', N'null'" in connection.cursor_instance.sql
    assert connection.cursor_instance.params == (
        "NATIONAL-CODE",
        "%完整通用名%",
    )


def test_manifest_has_commit_asset_hashes_and_no_patient_input(
    tmp_path: Path,
):
    assets = []
    for name in ("a.json", "b.json"):
        path = tmp_path / name
        path.write_text(
            json.dumps(
                {
                    "metadata": {
                        "schema_version": "1.0.0",
                        "content_version": "2026.1",
                    }
                }
            ),
            encoding="utf-8",
        )
        assets.append(path)
    report = {
        "status": "complete",
        "validation_error_count": 0,
        "duplicate_count": 0,
        "old_new_comparable_patient_count": 1,
    }
    manifest = build_run_manifest(
        source_summary=_source_summary(),
        report=report,
        source_database="synthetic_hub",
        history_database="synthetic_history",
        asset_paths=assets,
        code_commit="a" * 40,
        working_tree_clean=True,
        working_tree_fingerprint={
            "algorithm": "sha256",
            "fingerprint": "b" * 64,
            "tracked_diff_sha256": "c" * 64,
            "untracked_aggregate_sha256": "d" * 64,
            "untracked_file_count": 0,
        },
        run_ref="shadow-synthetic",
    )

    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    assert manifest["code"]["commit"] == "a" * 40
    assert manifest["source"]["candidate_query_version"] == (
        CANDIDATE_QUERY_VERSION
    )
    assert manifest["source"]["source_query_started_at"].endswith("Z")
    assert manifest["source"]["snapshot_isolation"] is False
    assert manifest["code"]["working_tree_fingerprint"]["fingerprint"] == (
        "b" * 64
    )
    assert manifest["knowledge_assets"]["a.json"]["schema_version"] == (
        "1.0.0"
    )
    assert manifest["knowledge_assets"]["a.json"]["sha256"] == (
        hashlib.sha256((tmp_path / "a.json").read_bytes()).hexdigest()
    )
    assert "patient_id" not in encoded
    assert "unit-test-private-salt" not in encoded


def test_git_fingerprint_excludes_generated_outputs(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Shadow Test"],
        cwd=tmp_path,
        check=True,
    )
    source = tmp_path / "source.py"
    report = tmp_path / "shadow.json"
    manifest = tmp_path / "manifest.json"
    source.write_text("VERSION = 1\n", encoding="utf-8")
    report.write_text("{}\n", encoding="utf-8")
    manifest.write_text("{}\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "source.py", "shadow.json", "manifest.json"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "initial"],
        cwd=tmp_path,
        check=True,
    )

    _, clean_before, fingerprint_before = git_state(
        tmp_path,
        excluded_paths=(report, manifest),
    )
    report.write_text('{"generated": 1}\n', encoding="utf-8")
    manifest.write_text('{"generated": 1}\n', encoding="utf-8")
    _, clean_after_output, fingerprint_after_output = git_state(
        tmp_path,
        excluded_paths=(report, manifest),
    )
    source.write_text("VERSION = 2\n", encoding="utf-8")
    _, clean_after_source, fingerprint_after_source = git_state(
        tmp_path,
        excluded_paths=(report, manifest),
    )

    assert clean_before is True
    assert clean_after_output is True
    assert (
        fingerprint_before["fingerprint"]
        == fingerprint_after_output["fingerprint"]
    )
    assert clean_after_source is False
    assert (
        fingerprint_after_source["fingerprint"]
        != fingerprint_after_output["fingerprint"]
    )
    assert fingerprint_before["excluded_generated_outputs"] == [
        "manifest.json",
        "shadow.json",
    ]


def test_batch_writes_blocked_outputs_then_cli_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    for name in shadow_batch.KNOWLEDGE_ASSET_NAMES:
        (config_dir / name).write_text(
            json.dumps(
                {
                    "version": "synthetic",
                    "metadata": {
                        "schema_version": "1.0.0",
                        "content_version": "synthetic",
                    },
                }
            ),
            encoding="utf-8",
        )

    class _Config:
        hub_database = "synthetic_hub"
        sql_database = "synthetic_history"

        @staticmethod
        def resolve(value: str) -> Path:
            assert value == "configs"
            return config_dir

    class _Connection:
        @staticmethod
        def close() -> None:
            return None

    invalid_evaluation = _evaluation()
    invalid_evaluation["proof_tree"] = "not-an-object"
    structured = {
        "mode": "shadow",
        "candidate_evaluations": [
            {
                "generic_name": "合成测试药",
                "drug_concept_id": "synthetic-drug",
                "ownership_key": "private-ownership",
                "selected_eligibility_evaluation": invalid_evaluation,
            }
        ],
        "selected_eligibility_evaluation": invalid_evaluation,
    }
    times = iter(
        ["2026-07-17T12:00:00Z", "2026-07-17T12:01:00Z"]
    )
    monkeypatch.setattr(shadow_batch, "get_config", lambda: _Config())
    monkeypatch.setattr(
        shadow_batch.hs,
        "connect",
        lambda *_args, **_kwargs: _Connection(),
    )
    monkeypatch.setattr(
        shadow_batch,
        "source_server_time",
        lambda _connection: next(times),
    )
    monkeypatch.setattr(
        shadow_batch,
        "insurance_oncology_drugs",
        lambda _path: {"合成测试药": {"SYNTHETIC-CODE"}},
    )
    monkeypatch.setattr(
        shadow_batch,
        "discover_coarse_candidate_ids",
        lambda *_args: ["PRIVATE-PATIENT"],
    )
    monkeypatch.setattr(
        shadow_batch,
        "fetch_candidate_frames",
        lambda *_args: (
            pd.DataFrame(columns=["bah"]),
            pd.DataFrame(columns=["住院号"]),
            pd.DataFrame(columns=["ba_id"]),
        ),
    )
    monkeypatch.setattr(
        shadow_batch,
        "lookup_patient_drugs",
        lambda *_args, **_kwargs: {
            "matches": [{"generic_name": "合成测试药"}],
            "oncology_structured": structured,
        },
    )
    monkeypatch.setattr(
        shadow_batch,
        "load_historical_context",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        shadow_batch,
        "git_state",
        lambda **_kwargs: (
            "a" * 40,
            False,
            {
                "algorithm": "git-head-dirty-content-sha256-v1",
                "fingerprint": "b" * 64,
                "tracked_diff_sha256": "c" * 64,
                "untracked_aggregate_sha256": "d" * 64,
                "untracked_file_count": 1,
                "excluded_generated_outputs": [
                    "shadow.json",
                    "manifest.json",
                ],
            },
        ),
    )
    report_path = tmp_path / "shadow.json"
    manifest_path = tmp_path / "manifest.json"
    temp_root = tmp_path / "private-temp"
    report, manifest = shadow_batch.run_batch(
        report_path=report_path,
        manifest_path=manifest_path,
        salt="unit-test-private-salt",
        temp_root=temp_root,
    )

    assert report["status"] == "blocked"
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == (
        "blocked"
    )
    assert json.loads(
        manifest_path.read_text(encoding="utf-8")
    )["report_status"] == "blocked"
    assert not list(temp_root.glob("javert-oncology-shadow-*"))

    monkeypatch.setattr(
        shadow_batch,
        "run_batch",
        lambda **_kwargs: (report, manifest),
    )
    monkeypatch.setenv(
        "JAVERT_SHADOW_DEID_SALT",
        "unit-test-private-salt",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_oncology_shadow_batch.py",
            "--report",
            str(report_path),
            "--manifest",
            str(manifest_path),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        shadow_batch.main()
    assert exc_info.value.code == 2
