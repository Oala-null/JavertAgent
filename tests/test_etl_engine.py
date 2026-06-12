# -*- coding: utf-8 -*-
"""ETL 引擎回归 + manifest_loader 校验 (add-visual-schema-onboarding Layer1).

回归核心 (task 1.5): manifest 驱动引擎 (run_etl) 与旧硬编码 transform_* 在 szx 真数据上
逐列 (to_csv) 一致 — 两个独立实现互为 oracle, 证明重构未改 4 表行为.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from javert.onboarding.etl_engine import read_source, run_etl, transform_spoke
from javert.onboarding.manifest_loader import (
    ManifestError,
    Manifest,
    load_manifest,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SZX_DIR = PROJECT_ROOT / "data" / "szx"

# 旧实现 (scripts 非包) — 作为回归 oracle
_SPEC = importlib.util.spec_from_file_location(
    "etl_import_legacy",
    PROJECT_ROOT / "scripts" / "etl_import.py",
)
legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(legacy)


def _szx_mapping() -> dict:
    """从 column_mapping.yaml 取 szx 的 4 节 columns, 文件路径改指向 repo data/szx."""
    with open(PROJECT_ROOT / "configs" / "column_mapping.yaml", encoding="utf-8") as f:
        m = yaml.safe_load(f)
    files = {
        "fees": "random_5pts_fee.csv",
        "notes": "random_5pts_doc.csv",
        "diagnoses": "random_5pts_zd.csv",
        "surgeries": "random_5pts_oprn.csv",
    }
    for spoke, fname in files.items():
        m[spoke]["file"] = str(SZX_DIR / fname)
    return m


requires_szx = pytest.mark.skipif(
    not (SZX_DIR / "random_5pts_fee.csv").exists(), reason="szx fixture 不存在"
)


# ────────────── manifest_loader 校验 ──────────────

def test_manifest_loads_and_classifies():
    m = load_manifest()
    assert set(m.spokes) >= {"fees", "notes", "diagnoses", "surgeries", "labs", "examinations"}
    assert set(m.live_spokes()) == {"fees", "notes", "diagnoses", "surgeries", "labs", "examinations"}
    assert set(m.view_spokes()) == {"anesthesia", "pathology"}
    assert "anesthesia" not in m.tabular_spokes()


def test_manifest_required_keys_from_yaml():
    m = load_manifest()
    assert m.spoke("fees").required_keys == ["patient_id", "item_name", "amount", "date", "category"]
    assert m.spoke("notes").split_sections is True
    assert m.spoke("diagnoses").synth_seq == "ipt_medcas_hmpg_sn"
    # 一字段多 target (diag_name → inhosp_diag_name + diag_name)
    assert m.spoke("diagnoses").field("diag_name").targets == ["inhosp_diag_name", "diag_name"]


def test_manifest_rejects_bad_status(tmp_path):
    bad = {"version": 1, "spokes": {"x": {"name": "X", "status": "bogus"}}}
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_manifest_rejects_target_outside_schema(tmp_path):
    bad = {
        "version": 1,
        "spokes": {
            "fees": {
                "name": "费用", "status": "live", "output_file": "shi_fee.csv",
                "id_form": "compound", "id_column": "bah", "join_key": "x",
                "output_schema": ["bah", "amt"],
                "fields": [
                    {"key": "patient_id", "name": "p", "required": True, "targets": ["bah"]},
                    {"key": "amount", "name": "金额", "required": True, "targets": ["NOT_IN_SCHEMA"]},
                ],
            }
        },
    }
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_manifest_rejects_view_without_tool(tmp_path):
    bad = {"version": 1, "spokes": {"v": {"name": "V", "status": "view"}}}
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(p)


# ────────────── ETL 回归: new (engine) == old (legacy) ──────────────

@requires_szx
@pytest.mark.parametrize("spoke_key,legacy_fn,kind", [
    ("fees", "transform_fees", "tabular"),
    ("notes", "transform_notes", "notes"),
    ("diagnoses", "transform_diagnoses", "tabular"),
    ("surgeries", "transform_surgeries", "tabular"),
])
def test_engine_matches_legacy(spoke_key, legacy_fn, kind):
    manifest = load_manifest()
    mapping = _szx_mapping()
    hospital_code = mapping.get("hospital_code", "H99999999999")

    # new
    result = run_etl(mapping, manifest)
    assert result.ok, result.fatal_errors
    new_df = next(s.df for s in result.spokes if s.key == spoke_key)

    # old (legacy oracle)
    src = read_source(mapping[spoke_key]["file"])
    col_map = mapping[spoke_key]["columns"]
    fn = getattr(legacy, legacy_fn)
    if kind == "notes":
        old_df = fn(src, col_map)
    else:
        old_df = fn(src, col_map, hospital_code)

    assert new_df.to_csv(index=False) == old_df.to_csv(index=False), (
        f"{spoke_key}: manifest 引擎与旧实现输出不一致"
    )


@requires_szx
def test_engine_full_run_all_four_tables():
    result = run_etl(_szx_mapping(), load_manifest())
    assert result.ok
    keys = {s.key for s in result.spokes}
    assert keys == {"fees", "notes", "diagnoses", "surgeries"}
    for s in result.spokes:
        assert len(s.df) > 0
        assert s.patient_count > 0
        assert list(s.df.columns) == load_manifest().spoke(s.key).output_schema
