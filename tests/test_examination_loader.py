# -*- coding: utf-8 -*-
"""ExaminationLoader 测试 — tmp_path 小 csv fixture."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from javert.data.examination_loader import ExaminationLoader


@pytest.fixture
def exam_csv(tmp_path: Path) -> Path:
    df = pd.DataFrame({
        "zyh": ["J86568 ", "J86568 ", "K29954", "J28165", "J28165"],
        "checkType": ["电生理", "放射", "放射", "放射", "心超"],
        "checkItemName": ["肺功能", "CT", "CT", "CR", "心脏"],
        "checkConclusion": [
            "通气弥散残气",
            "目前胸部CT平扫两肺未见明显活动性病变",
            "左肾多发囊肿,部分为复杂囊肿",
            "目前两肺未见明显活动性病变",
            "主动脉瓣微量反流",
        ],
        "checkDescribe": ["", "", "腹部 CT", "", "心脏彩超"],
        "checkPosition": ["", "胸部", "上腹部", "胸部", "心脏"],
        "department": ["", "", "", "", ""],
        "checkDate": ["2024-09-11", "2024-09-11", "2024-09-11", "2024-09-11", "2024-09-11"],
        "reportDate": [
            "2024-09-11 00:00:00", "2024-09-11 09:12:47", "2024-09-11 08:32:21",
            "2024-09-11 09:06:34", "2024-09-11 09:54:30",
        ],
        "diagnosis": ["", "", "", "", ""],
        "isPos": ["1", "1", "1", "0", "1"],
        "age": ["", "", "", "", ""],
        "Sex": ["", "", "", "", ""],
        "reporter": ["彭爱梅", "周荻", "谢中锋", "", "陈依心"],
        "auditor": ["", "", "", "", ""],
    })
    path = tmp_path / "sy_patient_examination.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def test_get_examinations_by_patient(exam_csv):
    loader = ExaminationLoader(exam_csv)
    rows = loader.get_examinations("J86568")
    assert len(rows) == 2
    # zyh 被 strip 过
    assert all(r["zyh"] == "J86568" for r in rows)


def test_unknown_patient_returns_empty(exam_csv):
    loader = ExaminationLoader(exam_csv)
    assert loader.get_examinations("Z99999") == []


def test_filter_by_check_type(exam_csv):
    loader = ExaminationLoader(exam_csv)
    rows = loader.get_examinations("J86568", check_type="放射")
    assert len(rows) == 1
    assert rows[0]["checkConclusion"].startswith("目前胸部CT")


def test_filter_by_keyword_hits_conclusion(exam_csv):
    loader = ExaminationLoader(exam_csv)
    rows = loader.get_examinations("J86568", keyword="弥散")
    assert len(rows) == 1
    assert "弥散" in rows[0]["checkConclusion"]


def test_keyword_hits_describe_or_position(exam_csv):
    loader = ExaminationLoader(exam_csv)
    rows = loader.get_examinations("K29954", keyword="腹部")
    assert len(rows) == 1


def test_results_sorted_by_report_date(exam_csv):
    loader = ExaminationLoader(exam_csv)
    rows = loader.get_examinations("J28165")
    dates = [r["reportDate"] for r in rows]
    assert dates == sorted(dates)


def test_missing_file_returns_empty(tmp_path):
    loader = ExaminationLoader(tmp_path / "nonexistent.csv")
    assert loader.get_examinations("J66252") == []
    assert loader.patient_count() == 0


def test_index_cached_across_calls(exam_csv):
    loader = ExaminationLoader(exam_csv)
    loader.get_examinations("J86568")  # 首次 build
    exam_csv.unlink()  # 删源文件
    # 仍能命中缓存
    rows = loader.get_examinations("J86568")
    assert len(rows) == 2
