# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from javert.data.csv_loader import CsvLoader
from javert.evidence.hub_snapshot import (
    CatalogTable, HubSourceProfile, SOURCE_CONSISTENCY, SOURCE_PROFILES,
    TABLE_SPECS, SourceCapabilities, build_snapshot_bundle, evaluate_preflight,
    _assert_no_private_values, read_snapshot_manifest, read_table_rows,
    validate_ordered_rows, validate_snapshot_pair,
)

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "hub_snapshot" / "minimal.json"


def _fixture():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    frames = {
        name: pd.DataFrame(rows)
        for name, rows in raw["canonical_frames"].items()
    }
    return raw, frames


def _capabilities():
    return SourceCapabilities(
        snapshot_isolation="OFF", read_committed_snapshot=False,
        cdc_enabled=False, change_tracking_enabled=False,
    )


def _catalog(*, writable: bool = False, missing_table: str = ""):
    return {
        spec.table: CatalogTable(
            table=spec.table, columns=spec.selected_columns,
            primary_key=spec.primary_key,
            permissions={
                "select": True, "insert": writable,
                "update": writable, "delete": writable,
            },
        )
        for spec in TABLE_SPECS
        if spec.table != missing_table
    }


def _preflight(profile_id="sh_yb_platform-readonly"):
    profile = SOURCE_PROFILES[profile_id]
    return evaluate_preflight(
        profile=profile, database_name=profile.database,
        catalog=_catalog(), capabilities=_capabilities(),
    )


def _build(path: Path, *, raw_override=None, frames_override=None, started="2026-08-23T00:00:00Z"):
    raw, frames = _fixture()
    return build_snapshot_bundle(
        output_dir=path, preflight=_preflight(),
        raw_rows=raw_override or raw["raw_rows"],
        canonical_frames=frames_override or frames,
        cohort=raw["cohort"], code_version="abcdef1",
        cohort_query_version="synthetic-cohort-v1",
        query_started_at=started,
        query_finished_at="2026-08-23T00:00:01Z",
    )


def test_readonly_profile_passes_and_writable_or_wrong_database_fails():
    assert _preflight().ok is True
    writable = evaluate_preflight(
        profile=SOURCE_PROFILES["tp-data-hub-readonly"],
        database_name="TP_data_hub", catalog=_catalog(writable=True),
        capabilities=_capabilities(),
    )
    assert writable.ok is False
    assert {issue.code for issue in writable.issues} == {"HUB_DML_PERMISSION_FORBIDDEN"}
    mismatch = evaluate_preflight(
        profile=SOURCE_PROFILES["sh_yb_platform-readonly"],
        database_name="TP_data_hub", catalog=_catalog(), capabilities=_capabilities(),
    )
    assert any(issue.code == "HUB_PROFILE_DATABASE_MISMATCH" for issue in mismatch.issues)
    missing = evaluate_preflight(
        profile=SOURCE_PROFILES["sh_yb_platform-readonly"],
        database_name="sh_yb_platform",
        catalog=_catalog(missing_table="TB_CIS_MEDICAL_DOCUMENT"),
        capabilities=_capabilities(),
    )
    assert any(issue.code == "HUB_REQUIRED_TABLE_MISSING" for issue in missing.issues)
    spec = TABLE_SPECS[0]
    wrong_schema = _catalog()
    wrong_schema[spec.table] = wrong_schema[spec.table].model_copy(update={
        "columns": tuple(column for column in spec.selected_columns if column != "YYJC"),
        "primary_key": ("YYJC",),
    })
    schema = evaluate_preflight(
        profile=SOURCE_PROFILES["sh_yb_platform-readonly"],
        database_name="sh_yb_platform", catalog=wrong_schema,
        capabilities=_capabilities(),
    )
    assert {issue.code for issue in schema.issues} == {
        "HUB_PRIMARY_KEY_MISMATCH", "HUB_REQUIRED_COLUMN_MISSING",
    }


def test_snapshot_is_deterministic_private_and_readable_by_legacy_loader(tmp_path):
    first_manifest, first_summary = _build(tmp_path / "first")
    second_manifest, second_summary = _build(
        tmp_path / "second", started="2026-08-24T00:00:00Z",
    )
    assert first_manifest.snapshot_id == second_manifest.snapshot_id
    assert first_manifest.snapshot_checksum == second_manifest.snapshot_checksum
    assert first_manifest.query_started_at != second_manifest.query_started_at
    assert first_manifest.atomic_snapshot is False
    assert first_manifest.source_consistency == SOURCE_CONSISTENCY
    assert first_summary.snapshot_id == first_manifest.snapshot_id
    assert "SYNTH-P1" not in first_manifest.model_dump_json()
    assert "FEE-001" not in first_summary.model_dump_json()
    for directory in (tmp_path / "first", tmp_path / "second"):
        assert (directory.stat().st_mode & 0o777) == 0o700
        assert all((path.stat().st_mode & 0o777) == 0o600 for path in directory.rglob("*") if path.is_file())
    loader = CsvLoader(
        tmp_path / "first" / "canonical" / "case_notes.csv",
        tmp_path / "first" / "canonical" / "shi_fee.csv",
    )
    assert len(loader.get_notes("SYNTH-P1")) == 1
    assert len(loader.get_fees("SYNTH-P1")) == 2
    lineage = (tmp_path / "first" / "lineage" / "source_rows.jsonl").read_text(encoding="utf-8")
    assert "SYNTH-P1" in lineage and "FEE-001" in lineage


def test_raw_change_changes_snapshot_id_and_pair_validation(tmp_path):
    first, _ = _build(tmp_path / "first")
    raw, frames = _fixture()
    changed = json.loads(json.dumps(raw["raw_rows"], ensure_ascii=False))
    changed["TB_IH_DIAGNOSIS_DETAIL"][0]["ZDSM"] = "修订后的合成诊断"
    second, _ = _build(tmp_path / "second", raw_override=changed, frames_override=frames)
    assert first.snapshot_id != second.snapshot_id
    assert validate_snapshot_pair(first, first) == ()
    assert {issue.code for issue in validate_snapshot_pair(first, second)} == {
        "SNAPSHOT_CONTENT_MISMATCH"
    }
    other_source = first.model_copy(update={
        "source_profile": "tp-data-hub-readonly", "database": "TP_data_hub",
    })
    assert {issue.code for issue in validate_snapshot_pair(first, other_source)} == {
        "SNAPSHOT_SOURCE_PROFILE_MISMATCH"
    }


def test_existing_output_and_partial_failure_are_cleaned(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("owned by caller", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _build(existing)
    assert (existing / "keep.txt").exists()
    raw, frames = _fixture()
    frames.pop("shi_zd.csv")
    failed = tmp_path / "failed"
    with pytest.raises(KeyError):
        _build(failed, raw_override=raw["raw_rows"], frames_override=frames)
    assert not failed.exists()


def test_canonical_cohort_escape_invalidates_and_cleans(tmp_path):
    raw, frames = _fixture()
    frames["case_notes.csv"] = pd.concat([
        frames["case_notes.csv"],
        pd.DataFrame([{
            "住院号": "SYNTH-OUTSIDE", "事件时间": "", "阶段": "出院记录",
            "子阶段": "出院诊断", "内容": "人工文本", "来源文件": "synthetic",
        }]),
    ], ignore_index=True)
    output = tmp_path / "escape"
    with pytest.raises(ValueError, match="SNAPSHOT_CANONICAL_COHORT_ESCAPE"):
        _build(output, raw_override=raw["raw_rows"], frames_override=frames)
    assert not output.exists()


def test_read_table_rows_uses_parameter_binding_and_declared_order():
    spec = next(item for item in TABLE_SPECS if item.table == "TB_IH_DIAGNOSIS_DETAIL")
    row = ("0001", "DIAG-001", "SYNTH-P1", "SYNTH-C73", "人工诊断", "1")

    class Cursor:
        description = [(name,) for name in spec.selected_columns]
        sql = ""
        params = ()
        def execute(self, sql, params):
            self.sql, self.params = sql, params
        def fetchall(self):
            return [row]

    class Connection:
        cursor_instance = Cursor()
        def cursor(self):
            return self.cursor_instance

    connection = Connection()
    result = read_table_rows(connection, spec, ["SYNTH-P1"])
    assert result[0]["ZYZDLSH"] == "DIAG-001"
    assert connection.cursor_instance.params == ("SYNTH-P1",)
    assert "ORDER BY [YLJGYQDM],[ZYZDLSH]" in connection.cursor_instance.sql
    assert "SYNTH-P1" not in connection.cursor_instance.sql


def test_duplicate_or_unsorted_primary_keys_and_phi_summary_fail_closed():
    spec = TABLE_SPECS[0]
    with pytest.raises(ValueError, match="HUB_PRIMARY_KEY_DUPLICATE"):
        validate_ordered_rows(
            [{"YLJGYQDM": "0001"}, {"YLJGYQDM": "0001"}], spec,
        )
    with pytest.raises(ValueError, match="HUB_PRIMARY_KEY_ORDER_INVALID"):
        validate_ordered_rows(
            [{"YLJGYQDM": "0002"}, {"YLJGYQDM": "0001"}], spec,
        )
    with pytest.raises(ValueError, match="SNAPSHOT_PUBLIC_PHI_LEAK"):
        _assert_no_private_values(
            {"summary": "contains-SYNTH-PATIENT-001"}, ["SYNTH-PATIENT-001"],
        )


def test_existing_output_is_rejected_before_database_connect(tmp_path, monkeypatch):
    from scripts import hub_evidence_snapshot as cli

    output = tmp_path / "existing"
    output.mkdir()
    (output / "owned.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(cli, "_connect", lambda _database: (_ for _ in ()).throw(AssertionError("connected")))
    with pytest.raises(FileExistsError):
        cli.run(
            profile_id="sh_yb_platform-readonly", limit=1,
            output_dir=output, smoke=False,
        )


def test_snapshot_module_has_no_dml_or_authoritative_runtime_imports():
    source = (ROOT / "src/javert/evidence/hub_snapshot.py").read_text(encoding="utf-8")
    upper = source.upper()
    for statement in ("INSERT INTO", "UPDATE [", "DELETE FROM", "MERGE INTO"):
        assert statement not in upper
    for module in ("javert.store", "javert.web", "routes_2c", "sqlalchemy"):
        assert module not in source
