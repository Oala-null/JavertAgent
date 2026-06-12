# -*- coding: utf-8 -*-
"""快照桥 etl_from_sql 测试.

两类 (都不连 142):
  1. 防漂移: create_aidb_tables.sql 每张表列名 == build_intake_templates.META 表头
     (= docs/schema Excel 模板列头, = manifest 字段). META 一变即红.
  2. 离线 oracle: 内存 DataFrame (友好中文列) 喂 run_bridge → 断言产出内部 schema 列
     + 复合患者键合成 + 连接预检 🟢. (run_bridge 的 SQL 读取 seam _read_aidb_tables 不在此测.)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

from javert.onboarding.join_preflight import preflight_keys
from javert.onboarding.manifest_loader import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from etl_from_sql import AIDB_TABLES, load_alias, run_bridge  # noqa: E402
from build_intake_templates import META  # noqa: E402

DDL_PATH = PROJECT_ROOT / "scripts" / "sql" / "create_aidb_tables.sql"


def _ddl_columns(sql_text: str, table: str) -> list[str]:
    """从 create_aidb_tables.sql 抽某张表的列名 (中括号包的中文列), 去掉自增 id."""
    start = sql_text.index(f"CREATE TABLE {table}")
    end = sql_text.index("GO", start)
    block = sql_text[start:end]
    return re.findall(r"\[([^\]]+)\]", block)  # 块内仅列名带中括号; id/表名不带


# ────────────────────────── 1. 防漂移 ──────────────────────────

def test_ddl_tables_cover_all_six_spokes():
    """AIDB_TABLES 6 个键 == manifest tabular spoke."""
    manifest = load_manifest()
    assert set(AIDB_TABLES) == set(manifest.tabular_spokes())


@pytest.mark.parametrize("spoke_key,table", sorted(AIDB_TABLES.items()))
def test_ddl_columns_match_intake_template_headers(spoke_key, table):
    """每张 aidb 表列清单 == 该 spoke 的 META 友好表头 (顺序敏感). 防 DDL/模板/manifest 漂移."""
    manifest = load_manifest()
    expected = [META[spoke_key][f.key][0] for f in manifest.spoke(spoke_key).fields]
    actual = _ddl_columns(DDL_PATH.read_text(encoding="utf-8"), table)
    assert actual == expected, f"{table} 列与 META 模板表头不一致 (DDL 漂移?)"


# ────────────────────────── 2. 离线 oracle ──────────────────────────

def _synthetic_tables(pids: list[str]) -> dict[str, pd.DataFrame]:
    """友好中文列的 6 表样本 (含全部必填字段), 同住院号跨表一致 → 预检应 🟢."""
    return {
        "fees": pd.DataFrame([
            {"住院号": p, "收费项目名称": "血常规", "金额": "35.00",
             "收费日期": "2026-01-03", "费用类别": "检查费"} for p in pids
        ]),
        "notes": pd.DataFrame([
            {"住院号": p, "文书名称": "入院记录", "内容": "颈部肿物 3 月, 查体见结节"} for p in pids
        ]),
        "diagnoses": pd.DataFrame([
            {"住院号": p, "主诊断标志": "1", "诊断名称": "甲状腺恶性肿瘤",
             "诊断编码": "C73.x00"} for p in pids
        ]),
        "surgeries": pd.DataFrame([
            {"住院号": p, "手术名称": "甲状腺全切除术", "主手术标志": "1"} for p in pids
        ]),
    }


def test_run_bridge_produces_internal_csvs_and_compound_key(tmp_path):
    manifest = load_manifest()
    alias = load_alias()
    pids = ["211454284", "211454285"]
    result = run_bridge(_synthetic_tables(pids), "TESTH", tmp_path, manifest, alias)

    assert result is not None and result.ok

    # 费用: 内部列 bah, 复合键 {code}-{住院号}
    fee = pd.read_csv(tmp_path / "shi_fee.csv", dtype=str)
    assert "bah" in fee.columns and "medins_list_name" in fee.columns
    assert fee["bah"].iloc[0].strip() == "TESTH-211454284"
    assert set(fee["medins_list_name"]) == {"血常规"}

    # 文书: 裸号 (id_form=bare), 内容保留
    notes = pd.read_csv(tmp_path / "case_notes.csv", dtype=str)
    assert "住院号" in notes.columns
    assert notes["住院号"].iloc[0].strip() == "211454284"
    assert notes["内容"].str.contains("颈部肿物").any()

    # 诊断: 复合 ba_id, 主诊断名落到 inhosp_diag_name
    zd = pd.read_csv(tmp_path / "shi_zd.csv", dtype=str)
    assert zd["ba_id"].iloc[0].strip() == "TESTH-211454284"
    assert (zd["inhosp_diag_name"] == "甲状腺恶性肿瘤").any()


def test_run_bridge_preflight_green_when_keys_consistent(tmp_path):
    """同住院号跨 4 表 → 连接预检键交集 100% (🟢)."""
    manifest = load_manifest()
    alias = load_alias()
    pids = ["211454284", "211454285"]
    result = run_bridge(_synthetic_tables(pids), "TESTH", tmp_path, manifest, alias)
    spoke_meta = {sr.key: manifest.spoke(sr.key) for sr in result.spokes}
    pf = preflight_keys(result.spokes, spoke_meta)
    assert pf is not None
    assert pf.coverage == 1.0
    assert pf.verdict == "green"


def test_run_bridge_empty_returns_none(tmp_path):
    """全空表 → None, 不写盘."""
    manifest = load_manifest()
    out = run_bridge({}, "TESTH", tmp_path, manifest, load_alias())
    assert out is None
    assert not (tmp_path / "shi_fee.csv").exists()


def test_run_bridge_dry_run_does_not_write(tmp_path):
    manifest = load_manifest()
    alias = load_alias()
    result = run_bridge(_synthetic_tables(["211454284"]), "TESTH", tmp_path, manifest, alias,
                        dry_run=True)
    assert result is not None and result.ok
    assert not (tmp_path / "shi_fee.csv").exists()
    assert not (tmp_path / "case_notes.csv").exists()
