# -*- coding: utf-8 -*-
"""LabLoader 测试 — tmp_path 小 csv fixture."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from javert.data.lab_loader import LabLoader


@pytest.fixture
def lab_csv(tmp_path: Path) -> Path:
    df = pd.DataFrame({
        "zyh": ["J60661 ", "J60661 ", "J54057 ", "J54057 ", "J54057 ", "J83318 "],
        "rpt_itemname": [
            "类风湿因子", "抗\"O\"",
            "促甲状腺素", "血清游离T3", "乙肝表面抗体",
            "TT",
        ],
        "rpt_itemcode": [
            "RFN", "ASON",
            "TSH", "FT3", "E-HBSAB",
            "TT-2",
        ],
        "result": ["<9.8", "<53", "3.469", "4.42", "23.379", "17.40"],
        "result_unit": ["IU/ml", "IU/ml", "mIU/L", "pmol/L", "", "秒"],
        "result_ref": ["<15.8", "<408", "0.38-4.34", "2.8-6.3", "<10mIU/ml(阴性)", "正常人对照±3"],
        "result_flag": ["正常", "正常", "正常", "正常", "↑", "正常"],
        "diagnosisOpinion": [
            "急性肾功能不全", "急性肾功能不全",
            "甲状腺结节", "甲状腺结节", "甲状腺结节",
            "腹痛",
        ],
        "department": ["肾脏内科", "肾脏内科", "甲状腺乳腺科病房", "甲状腺乳腺科病房", "甲状腺乳腺科病房", "消化内科三区"],
        "report_dt": [
            "2024-09-11 09:41:46", "2024-09-11 09:41:46",
            "2024-11-19 13:55:55", "2024-11-19 13:55:55", "2024-11-19 09:38:34",
            "2024-11-26 14:13:21",
        ],
        "specimen": ["血(免疫1)"] * 5 + ["血"],
        "inspectionName": ["【MB】免疫(BN2)"] * 2 + ["【FM】核医学科"] * 2 + ["【MG】免疫美康"] + ["【XD】凝血功能(CS5100)"],
        "age": ["67", "67", "45", "45", "45", "56"],
        "sex": ["女"] * 6,
        "trier": ["龙曙萍", "龙曙萍", "史秋园", "史秋园", "王雯", "何春燕"],
        "auditor": ["俞蕾"] * 5 + ["李丽玲"],
    })
    path = tmp_path / "sy_检验.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def test_get_lab_by_patient(lab_csv):
    loader = LabLoader(lab_csv, chunksize=3)  # 强制多 chunk 触发流读 + 合并
    rows = loader.get_lab_results("J60661")
    assert len(rows) == 2
    assert all(r["zyh"] == "J60661" for r in rows)


def test_unknown_patient_returns_empty(lab_csv):
    loader = LabLoader(lab_csv)
    assert loader.get_lab_results("Z99999") == []


def test_abnormal_only_filter(lab_csv):
    loader = LabLoader(lab_csv)
    rows = loader.get_lab_results("J54057", abnormal_only=True)
    assert len(rows) == 1
    assert rows[0]["rpt_itemname"] == "乙肝表面抗体"
    assert rows[0]["result_flag"] == "↑"


def test_item_keyword_filter_chinese(lab_csv):
    loader = LabLoader(lab_csv)
    rows = loader.get_lab_results("J54057", item_keyword="甲状腺")
    assert len(rows) == 1
    assert rows[0]["rpt_itemname"] == "促甲状腺素"


def test_item_keyword_filter_english_case_insensitive(lab_csv):
    loader = LabLoader(lab_csv)
    rows = loader.get_lab_results("J54057", item_keyword="t3")
    assert len(rows) == 1
    assert "T3" in rows[0]["rpt_itemname"]


def test_item_keyword_hits_rpt_itemcode(lab_csv):
    """rpt_itemcode (英文缩写) 命中验证 — 真实数据 AFP/TSH/CEA 这种是 itemcode 不是 itemname."""
    loader = LabLoader(lab_csv)
    rows = loader.get_lab_results("J54057", item_keyword="TSH")
    assert len(rows) == 1
    assert rows[0]["rpt_itemcode"] == "TSH"
    assert rows[0]["rpt_itemname"] == "促甲状腺素"


def test_results_sorted_by_report_dt(lab_csv):
    loader = LabLoader(lab_csv)
    rows = loader.get_lab_results("J54057")
    dates = [r["report_dt"] for r in rows]
    assert dates == sorted(dates)


def test_missing_file_returns_empty(tmp_path):
    loader = LabLoader(tmp_path / "nonexistent.csv")
    assert loader.get_lab_results("J60661") == []
    assert loader.patient_count() == 0


def test_index_cached(lab_csv):
    loader = LabLoader(lab_csv)
    loader.get_lab_results("J60661")
    lab_csv.unlink()
    rows = loader.get_lab_results("J60661")
    assert len(rows) == 2


def test_combined_filters(lab_csv):
    loader = LabLoader(lab_csv)
    # 甲状腺类 + 异常 (J54057 仅有"乙肝表面抗体"异常, 不含"甲状腺")
    rows = loader.get_lab_results("J54057", item_keyword="甲状腺", abnormal_only=True)
    assert rows == []
    # 异常 + 乙肝
    rows = loader.get_lab_results("J54057", item_keyword="乙肝", abnormal_only=True)
    assert len(rows) == 1
