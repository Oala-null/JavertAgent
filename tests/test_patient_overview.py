# -*- coding: utf-8 -*-
"""tests for web/patient_overview.py 卡片摘要 (enhance-workbench-usability G1).

get_fees_sum_map (进程缓存 + 已知 patient 命中) + get_primary_dx (maindiag_flag=1
优先 / note 兜底 / 再无返回空).
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from javert.data.csv_loader import CsvLoader
from javert.web import patient_overview as po
from javert.web.patient_overview import build_overview


@pytest.fixture
def synthetic_patient(tmp_path, monkeypatch):
    """最小患者三件套：文书、费用、病案首页诊断。"""
    patient_id = "TESTPT001"
    notes_path = tmp_path / "case_notes.csv"
    fees_path = tmp_path / "shi_fee.csv"
    zd_path = tmp_path / "shi_zd.csv"

    pd.DataFrame(
        {
            "住院号": [patient_id, patient_id],
            "阶段": ["住院", "住院"],
            "子阶段": ["主诉", "出院诊断"],
            "内容": ["合成患者，女，45岁，因测试入院", "合成主诊"],
        }
    ).to_csv(notes_path, index=False)
    pd.DataFrame(
        {
            "bah": [
                f"HOSP-{patient_id}",
                f"HOSP-{patient_id}",
                f"HOSP-{patient_id}",
                "HOSP-TESTPT002",
            ],
            "medins_list_name": ["测试治疗A", "测试治疗B", "测试药品", "其他患者项目"],
            "med_list_codg": ["NAT-A", "NAT-B", "NAT-C", "NAT-X"],
            "medins_list_codg": ["LOC-A", "LOC-B", "LOC-C", "LOC-X"],
            "medins_chrgitm_type": ["治疗", "治疗", "西药", "治疗"],
            "cnt": [2, 1, 1, 1],
            "pric": [50, 40, 20, 10],
            "det_item_fee_sumamt": [100, 40, 20, 10],
            "fee_ocur_time": ["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-01"],
            "acord_dept_name": ["测试科", "测试科", "测试科", "其他科"],
            "orders_dr_name": ["测试医生", "测试医生", "测试医生", "其他医生"],
            "prodname": ["", "", "测试药品商品名", ""],
            "spec": ["", "", "1支", ""],
        }
    ).to_csv(fees_path, index=False)
    pd.DataFrame(
        {
            "ba_id": [f"HOSP-{patient_id}", f"HOSP-{patient_id}"],
            "maindiag_flag": [0, 1],
            "inhosp_diag_name": ["合成次诊", "合成主诊"],
            "inhosp_diag_code": ["Z00.000", "C73.TEST"],
            "ipt_medcas_hmpg_sn": [2, 1],
        }
    ).to_csv(zd_path, index=False)

    monkeypatch.setattr(
        po,
        "get_config",
        lambda: SimpleNamespace(zd_path=zd_path, ss_path=tmp_path / "missing_ss.csv"),
    )
    return patient_id, CsvLoader(notes_path, fees_path)


@pytest.fixture(autouse=True)
def _fresh_caches():
    po.reset_caches()
    yield
    po.reset_caches()


def test_get_fees_sum_map_known_patient(synthetic_patient):
    """费用按复合键末段患者号汇总，且不串入其他患者。"""
    patient_id, loader = synthetic_patient
    m = po.get_fees_sum_map(loader)
    assert m == {patient_id: 160.0, "TESTPT002": 10.0}


def test_get_fees_sum_map_cached_once():
    """二次调用返回同一缓存对象 (不重复 groupby 全表)."""
    first = po.get_fees_sum_map()
    second = po.get_fees_sum_map()
    assert first is second


def test_get_primary_dx_maindiag_flag(synthetic_patient):
    """病案首页按 maindiag_flag=1 取主诊，而非文件首行。"""
    patient_id, _loader = synthetic_patient
    assert po.get_primary_dx(patient_id) == "合成主诊 (C73.TEST)"


def test_get_primary_dx_missing_returns_empty():
    """无病案首页 + 无 loader → 返回空字符串 (不报错)."""
    assert po.get_primary_dx("__NO_SUCH_PATIENT__") == ""


def test_get_primary_dx_note_fallback(tmp_path):
    """无病案首页但有文书出院诊断 → note 派生兜底."""
    notes = pd.DataFrame(
        {
            "住院号": ["TESTPT001"],
            "阶段": ["住院"],
            "子阶段": ["出院诊断"],
            "内容": ["甲状腺乳头状癌"],
        }
    )
    fees = pd.DataFrame({"bah": ["HX-TESTPT001"], "det_item_fee_sumamt": ["1.0"]})
    notes_path = tmp_path / "notes.csv"
    fees_path = tmp_path / "fees.csv"
    notes.to_csv(notes_path, index=False)
    fees.to_csv(fees_path, index=False)
    loader = CsvLoader(notes_path, fees_path)
    # TESTPT001 不在真实 shi_zd → 走 note 兜底
    dx = po.get_primary_dx("TESTPT001", loader)
    assert dx == "甲状腺乳头状癌"


# =========================================================
# G3: 费用类别就地展开明细
# =========================================================
def test_build_overview_fee_category_items_consistency(synthetic_patient):
    """每类别 items 非空; 未截断类别的 items 金额之和 == 该类别汇总金额."""
    patient_id, loader = synthetic_patient
    ov = build_overview(patient_id, loader)
    cats = ov["fee_categories"]
    assert cats
    assert ov["fees_sum"] == pytest.approx(160.0)
    checked = 0
    for c in cats:
        assert c["items"], f"类别 {c['label']} 明细为空"
        assert c["items_total"] >= len(c["items"])
        if not c["items_truncated"]:
            items_sum = sum(it["sum"] for it in c["items"])
            assert items_sum == pytest.approx(c["sum"], abs=0.01), c["label"]
            checked += 1
    assert checked >= 1


# =========================================================
# boost-llm-efficiency: build_overview 真缓存
# =========================================================
def test_build_overview_cached_but_isolated_copies(synthetic_patient):
    """同 (patient_id, loader) 二次调用: 内层缓存不重算, 但外层返回独立深拷贝.

    fix-scan-residuals: 内层 _build_overview_cached 命中同一对象 (不重算), 公共
    build_overview 返回 deepcopy → 两次结果值相等但非同一对象, 改一个不污染另一个.
    """
    patient_id, loader = synthetic_patient
    first = build_overview(patient_id, loader)
    second = build_overview(patient_id, loader)
    assert first is not second                       # 独立对象
    assert first["patient_id"] == second["patient_id"]
    # 内层缓存命中 (不重算): 底层缓存 dict 是同一个
    assert (po._build_overview_cached(patient_id, loader)
            is po._build_overview_cached(patient_id, loader))
    # 改 first 不污染 second (跨请求串数据回归面)
    first["fee_categories"].append({"label": "污染"})
    assert not any(c.get("label") == "污染" for c in second["fee_categories"])


def test_build_overview_cache_cleared_by_reset(synthetic_patient):
    """reset_caches() 后重算 (新对象) — onboarding 载入新数据的失效钩子."""
    patient_id, loader = synthetic_patient
    cached_before = po._build_overview_cached(patient_id, loader)
    po.reset_caches()
    cached_after = po._build_overview_cached(patient_id, loader)
    assert cached_after is not cached_before
    assert cached_before["patient_id"] == cached_after["patient_id"] == patient_id
