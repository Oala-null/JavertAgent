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


def test_get_fees_substring_match_on_bah(fixture_csvs):
    loader = CsvLoader(*fixture_csvs)
    df = loader.get_fees("J66252")
    assert len(df) == 2
    df2 = loader.get_fees("J18906")
    assert len(df2) == 1


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
