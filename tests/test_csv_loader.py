# -*- coding: utf-8 -*-
"""CsvLoader 测试 — 用 fixture 构造小 csv."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from javert.data.csv_loader import CsvLoader


@pytest.fixture
def fixture_csvs(tmp_path: Path) -> tuple[Path, Path]:
    notes = pd.DataFrame({
        "住院号": ["J66252", "J66252", "J18906", "K03341"],
        "事件时间": ["2026-01-01"] * 4,
        "阶段": ["入院"] * 4,
        "子阶段": ["入院诊断", "出院诊断", "入院诊断", "入院诊断"],
        "内容": [
            "甲状腺乳头状癌",
            "甲状腺癌术后",
            "脑干海绵状血管瘤",
            "甲状腺良性结节",
        ],
        "来源文件": ["test"] * 4,
    })
    fees = pd.DataFrame({
        "bah": [
            "H31010600042-J66252 ",
            "H31010600042-J66252 ",
            "H31010600042-J18906 ",
        ],
        "fee_ocur_time": ["2026-01-01"] * 3,
        "cnt": [1.0, 1.0, 1.0],
        "pric": [80.0, 200.0, 50.0],
        "det_item_fee_sumamt": [80.0, 200.0, 50.0],
        "medins_list_name": ["脑功能成像", "CT平扫", "X线平片"],
    })
    notes_path = tmp_path / "case_notes.csv"
    fees_path = tmp_path / "shi_fee.csv"
    notes.to_csv(notes_path, index=False, encoding="utf-8")
    fees.to_csv(fees_path, index=False, encoding="utf-8")
    return notes_path, fees_path


def test_get_notes_known_patient(fixture_csvs):
    loader = CsvLoader(*fixture_csvs)
    df = loader.get_notes("J66252")
    assert len(df) == 2
    assert set(df["住院号"]) == {"J66252"}


def test_get_notes_unknown_returns_empty(fixture_csvs):
    loader = CsvLoader(*fixture_csvs)
    df = loader.get_notes("Z99999")
    assert df.empty
    assert "住院号" in df.columns


def test_get_fees_composite_key_tail_match_on_bah(fixture_csvs):
    loader = CsvLoader(*fixture_csvs)
    df = loader.get_fees("J66252")
    assert len(df) == 2
    df2 = loader.get_fees("J18906")
    assert len(df2) == 1


# =========================================================
# harden-onsite-redlines D4: 费用行患者归属两级精确匹配
# =========================================================
@pytest.fixture
def overlap_id_csvs(tmp_path: Path) -> tuple[Path, Path]:
    """患者号 123 是 1123 的子串; 复合键 0003-211530148 形态并存."""
    notes = pd.DataFrame({
        "住院号": ["123"],
        "事件时间": ["2026-01-01"], "阶段": ["入院"], "子阶段": ["入院诊断"],
        "内容": ["测试"], "来源文件": ["test"],
    })
    fees = pd.DataFrame({
        "bah": ["123", "1123", "H0001-123 ", "H0001-1123 ", "0003-211530148"],
        "fee_ocur_time": ["2026-01-01"] * 5,
        "cnt": [1.0] * 5,
        "pric": [10.0, 20.0, 30.0, 40.0, 50.0],
        "det_item_fee_sumamt": [10.0, 20.0, 30.0, 40.0, 50.0],
        "medins_list_name": ["a", "b", "c", "d", "e"],
    })
    notes_path = tmp_path / "case_notes.csv"
    fees_path = tmp_path / "shi_fee.csv"
    notes.to_csv(notes_path, index=False, encoding="utf-8")
    fees.to_csv(fees_path, index=False, encoding="utf-8")
    return notes_path, fees_path


def test_short_id_does_not_absorb_long_id(overlap_id_csvs):
    loader = CsvLoader(*overlap_id_csvs)
    df = loader.get_fees("123")
    # 只命中 精确 "123" + 复合键末段 "123" (H0001-123), 绝不含 1123 的行
    assert set(df["medins_list_name"]) == {"a", "c"}
    df2 = loader.get_fees("1123")
    assert set(df2["medins_list_name"]) == {"b", "d"}


def test_composite_key_tail_hits(overlap_id_csvs):
    loader = CsvLoader(*overlap_id_csvs)
    df = loader.get_fees("211530148")
    assert set(df["medins_list_name"]) == {"e"}


def test_full_composite_key_query_still_hits(overlap_id_csvs):
    loader = CsvLoader(*overlap_id_csvs)
    df = loader.get_fees("0003-211530148")
    assert set(df["medins_list_name"]) == {"e"}


def test_j66252_baseline_row_count_unchanged():
    """现有基线患者行为不变 (spec 场景). 真数据缺失时跳过."""
    from javert.config import get_config
    cfg = get_config()
    if not (cfg.notes_path.exists() and cfg.fees_path.exists()):
        pytest.skip("本机无真数据快照")
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    fees = loader.all_fees()
    # 旧 contains 语义 oracle: 逐键子串扫 (仅测试内使用)
    key = fees["bah"].astype(str) if "bah" in fees.columns else fees.iloc[:, 0].astype(str)
    old_n = int(key.str.contains("J66252", regex=False).sum())
    new_n = len(loader.get_fees("J66252"))
    assert new_n == old_n
    assert new_n > 0


def test_second_call_uses_cache(fixture_csvs):
    notes_path, fees_path = fixture_csvs
    loader = CsvLoader(notes_path, fees_path)
    loader.get_notes("J66252")
    # mtime 应不被刷新, 因为不再读 file
    notes_path.unlink()  # 删源文件
    df = loader.get_notes("J66252")
    assert len(df) == 2  # 仍能命中缓存


def test_all_notes_returns_full_df(fixture_csvs):
    loader = CsvLoader(*fixture_csvs)
    df = loader.all_notes()
    assert len(df) == 4
