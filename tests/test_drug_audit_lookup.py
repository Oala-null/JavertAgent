# -*- coding: utf-8 -*-
"""drug_audit_lookup 工具单测: stem 匹配 / bulk / single / 复合键 / 诊断源."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from javert.data.loader import DataLoader
from javert.tools import drug_audit_lookup as dal


class _StubLoader(DataLoader):
    def __init__(self, fees: pd.DataFrame):
        self._fees = fees

    def get_notes(self, patient_id: str) -> pd.DataFrame:  # 未用
        return pd.DataFrame()

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return self._fees[self._fees["bah"].astype(str).str.contains(patient_id, na=False)]

    def all_notes(self) -> pd.DataFrame:
        return pd.DataFrame()

    def all_fees(self) -> pd.DataFrame:
        return self._fees


@pytest.fixture
def kb_path(tmp_path: Path) -> Path:
    kb = {
        "version": "t",
        "drugs": {
            "阿卡波糖片": [
                {"rule_type": "超说明书", "detect_logic": "诊断不符说明书", "basis": "用于2型糖尿病"}
            ],
            "人血白蛋白": [
                {"rule_type": "限适应症", "detect_logic": "诊断不符限定", "basis": "限抢救/重症/肝硬化胸腹水且白蛋白<30g/L"}
            ],
            "艾普拉唑肠溶片": [
                {"rule_type": "限二线", "detect_logic": "无一线失败证据", "basis": "限二线用药"},
                {"rule_type": "限适应症", "detect_logic": "诊断不符限定", "basis": "限十二指肠溃疡/反流性食管炎"},
            ],
        },
    }
    p = tmp_path / "drug_audit_kb.json"
    p.write_text(json.dumps(kb, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def zd_path(tmp_path: Path) -> Path:
    df = pd.DataFrame({
        "ba_id": ["H31010600042-J66252", "H31010600042-J66252"],
        "diag_name": ["2型糖尿病", "高血压"],
        "diag_code": ["E11.900", "I10.x00"],
        "maindiag_flag": ["1", "0"],
    })
    p = tmp_path / "shi_zd.csv"
    df.to_csv(p, index=False, encoding="utf-8")
    return p


@pytest.fixture
def loader() -> _StubLoader:
    fees = pd.DataFrame({
        "bah": ["H31010600042-J66252 "] * 4,
        "medins_list_name": [
            "(集)(基)阿卡波糖片(拜唐苹)",  # KB 命中 (剥前缀 + 跨剂型)
            "人血白蛋白(基)",               # KB 命中
            "0.9%氯化钠注射液",             # 不在 KB
            "维生素C片",                    # 不在 KB
        ],
        "medins_chrgitm_type": ["西药", "西药", "西药", "西药"],
    })
    return _StubLoader(fees)


# ─────────── stem 匹配纯函数 ───────────

def test_stem_match_strips_prefix_and_dosage_form():
    # task 3.6 canonical: (集)(基)阿卡波糖片(拜唐苹) ← 阿卡波糖片
    assert dal.stem_match("阿卡波糖片", "(集)(基)阿卡波糖片(拜唐苹)") is True
    # 跨剂型: KB 奥美拉唑肠溶片 命中 fee 奥美拉唑肠溶胶囊
    assert dal.stem_match("奥美拉唑肠溶片", "(基)奥美拉唑肠溶胶囊") is True


def test_fee_clean_strips_leading_markers():
    assert dal.fee_clean("(集)(基)阿卡波糖片(拜唐苹)") == "阿卡波糖片(拜唐苹)"
    assert dal.fee_clean("（国谈）某药") == "某药"


def test_kb_stem_guards_min_length():
    assert dal.kb_stem("阿卡波糖片") == "阿卡波糖"
    # 不会把短名剥到 <2 字
    assert len(dal.kb_stem("片")) >= 1


# ─────────── code_match 纯函数 (fix-drug-code-match) ───────────

def test_code_match_exact_intersection():
    assert dal.code_match({"XA02BCA211A012010100154"}, {"XA02BCA211A012010100154"}) is True
    assert dal.code_match({"X1", "X2"}, {"X2", "X3"}) is True
    assert dal.code_match(set(), {"X1"}) is False
    assert dal.code_match({"X1"}, set()) is False


def test_code_match_similar_names_disjoint():
    """串味反例: 子串相似但国家码不同 → code_match 一律 False."""
    # 奥美拉唑 vs 艾普拉唑 / 艾司奥美拉唑 — 码集合不相交
    omeprazole = {"XA02BCA211A012010100154", "XA02BCA211A012010100228"}
    ilaprazole = {"XA02BCH101A001010100", "XA02BCH101A001010200"}
    esomeprazole = {"XA02BCE205A001010100"}
    assert dal.code_match(omeprazole, ilaprazole) is False
    assert dal.code_match(omeprazole, esomeprazole) is False
    # 丁苯那嗪 vs 氘丁苯那嗪 (设计反例: 名互为子串, 码不同)
    tetrabenazine = {"XN07XXT001A001010100"}
    deutetrabenazine = {"XN07XXD999A001010100"}
    assert dal.code_match(tetrabenazine, deutetrabenazine) is False


def test_code_match_real_kb_dingbennaqin_disjoint():
    """真 KB 接地: 丁苯那嗪片 ∩ 氘丁苯那嗪片 国家码 = ∅ (名子串会串味, 码不会)."""
    from pathlib import Path
    kb = json.loads(Path("configs/drug_audit_kb.json").read_text(encoding="utf-8"))["drugs"]
    if "丁苯那嗪片" not in kb or "氘丁苯那嗪片" not in kb:
        pytest.skip("真 KB 未含丁苯那嗪族 (KB 未重建)")
    a = set(dal.kb_codes(kb["丁苯那嗪片"]))
    b = set(dal.kb_codes(kb["氘丁苯那嗪片"]))
    assert a and b
    assert dal.code_match(a, b) is False  # 码不相交 → 不串味


# ─────────── bulk 码主路 (fix-drug-code-match) ───────────

@pytest.fixture
def kb_coded_path(tmp_path: Path) -> Path:
    """带国家码的新版 KB: 丁苯那嗪片 vs 氘丁苯那嗪片 (名互为子串, 码不同)."""
    kb = {
        "version": "2.0",
        "drugs": {
            "丁苯那嗪片": {
                "entries": [{"rule_type": "限适应症", "detect_logic": "", "basis": "限亨廷顿舞蹈病"}],
                "codes": ["XN07_DING_1", "XN07_DING_2"],
            },
            "氘丁苯那嗪片": {
                "entries": [{"rule_type": "限适应症", "detect_logic": "", "basis": "限迟发性运动障碍"}],
                "codes": ["XN07_DEU_1"],
            },
            "甘露醇注射液": {
                "entries": [{"rule_type": "限适应症", "detect_logic": "", "basis": "限脑水肿"}],
                "codes": ["XB05_GAN_1"],
            },
        },
    }
    p = tmp_path / "drug_audit_kb.json"
    p.write_text(json.dumps(kb, ensure_ascii=False), encoding="utf-8")
    return p


def test_bulk_code_first_no_crosstalk(kb_coded_path, zd_path):
    """患者只用氘丁苯那嗪片 (码 XN07_DEU_1) → 仅命中氘丁苯那嗪片, 不串味丁苯那嗪片."""
    fees = pd.DataFrame({
        "bah": ["H31010600042-J66252 "],
        "medins_list_name": ["(基)氘丁苯那嗪片"],
        "medins_chrgitm_type": ["西药"],
        "med_list_codg": ["XN07_DEU_1"],
    })
    r = dal.lookup_patient_drugs("J66252", _StubLoader(fees), kb_coded_path, zd_path)
    generics = {m["generic_name"] for m in r["matches"]}
    assert generics == {"氘丁苯那嗪片"}  # 丁苯那嗪片 (名子串) 被码精确排除
    assert all(m["needs_review"] is False for m in r["matches"])  # 码命中, 不需复核


def test_bulk_nocode_fee_falls_back_to_name_needs_review(kb_coded_path, zd_path):
    """fee 行 med_list_codg 空 → 退 stem 子串, 命中标 needs_review=True."""
    fees = pd.DataFrame({
        "bah": ["H31010600042-J66252 "],
        "medins_list_name": ["甘露醇注射液"],
        "medins_chrgitm_type": ["西药"],
        "med_list_codg": [""],  # 无国家码
    })
    r = dal.lookup_patient_drugs("J66252", _StubLoader(fees), kb_coded_path, zd_path)
    m = next(m for m in r["matches"] if m["generic_name"] == "甘露醇注射液")
    assert m["needs_review"] is True
    assert "(名兜底, 需复核)" in dal.format_for_agent(r)


def test_bulk_coded_fee_unknown_code_no_match(kb_coded_path, zd_path):
    """fee 行有码但不在任何知识点 code set → 不走名兜底, 不命中 (码权威)."""
    fees = pd.DataFrame({
        "bah": ["H31010600042-J66252 "],
        "medins_list_name": ["(基)氘丁苯那嗪片"],
        "medins_chrgitm_type": ["西药"],
        "med_list_codg": ["XZZ_UNKNOWN"],  # 码不在 KB
    })
    r = dal.lookup_patient_drugs("J66252", _StubLoader(fees), kb_coded_path, zd_path)
    assert r["matches"] == []


# ─────────── bulk ───────────

def test_bulk_returns_only_kb_matches_with_basis(loader, kb_path, zd_path):
    r = dal.lookup_patient_drugs("J66252", loader, kb_path, zd_path)
    generics = {m["generic_name"] for m in r["matches"]}
    assert generics == {"阿卡波糖片", "人血白蛋白"}  # 氯化钠/维C 被排除
    白 = next(m for m in r["matches"] if m["generic_name"] == "人血白蛋白")
    assert 白["rule_type"] == "限适应症"
    assert "白蛋白" in 白["basis"]
    # 原始 fee 名带出 (供复方/同名复核)
    assert 白["fee_names"] == ["人血白蛋白(基)"]


def test_bulk_excludes_fully_refunded_drug(kb_path, zd_path):
    """fix-fee-refund-netting: 完全充退 (净≤0) 的受监管药不进 matches."""
    fees = pd.DataFrame({
        "bah": ["H31010600042-J66252 "] * 4,
        "fee_ocur_time": ["1/8/2024", "2/8/2024", "3/8/2024", "4/8/2024"],
        "cnt": [1.0, -1.0, 1.0, 1.0],  # 阿卡波糖 +1/-1 净0; 人血白蛋白 净2
        "med_list_codg": ["AKB", "AKB", "RXB", "RXB"],
        "medins_list_name": [
            "(集)(基)阿卡波糖片(拜唐苹)",
            "(集)(基)阿卡波糖片(拜唐苹)",
            "人血白蛋白(基)",
            "人血白蛋白(基)",
        ],
        "medins_chrgitm_type": ["西药"] * 4,
    })
    r = dal.lookup_patient_drugs("J66252", _StubLoader(fees), kb_path, zd_path)
    generics = {m["generic_name"] for m in r["matches"]}
    assert "阿卡波糖片" not in generics  # 全退 → 不算用过, 不命中
    assert "人血白蛋白" in generics      # 净2 → 仍命中


def test_bulk_rule_type_filter(loader, kb_path, zd_path):
    r = dal.lookup_patient_drugs("J66252", loader, kb_path, zd_path, rule_type="限适应症")
    rts = {m["rule_type"] for m in r["matches"]}
    assert rts == {"限适应症"}
    assert {m["generic_name"] for m in r["matches"]} == {"人血白蛋白"}


def test_bulk_no_match_is_empty_not_error(kb_path, zd_path):
    fees = pd.DataFrame({
        "bah": ["H31010600042-J99999 "],
        "medins_list_name": ["0.9%氯化钠注射液"],
        "medins_chrgitm_type": ["西药"],
    })
    r = dal.lookup_patient_drugs("J99999", _StubLoader(fees), kb_path, zd_path)
    assert r["matches"] == []
    assert "无任何药品命中" in dal.format_for_agent(r)


def test_bulk_carries_shi_zd_diagnoses(loader, kb_path, zd_path):
    r = dal.lookup_patient_drugs("J66252", loader, kb_path, zd_path)
    names = {d["name"] for d in r["diagnoses"]}
    assert "2型糖尿病" in names
    main = [d for d in r["diagnoses"] if d["is_main"]]
    assert main and main[0]["name"] == "2型糖尿病"
    text = dal.format_for_agent(r)
    assert "病案首页诊断" in text and "[主诊] 2型糖尿病" in text


def test_bulk_composite_patient_key(loader, kb_path, zd_path):
    # patient_id "J66252" 命中复合键 bah "H31010600042-J66252 "
    r = dal.lookup_patient_drugs("J66252", loader, kb_path, zd_path)
    assert r["total_drug_fees"] == 4


# ─────────── single ───────────

def test_single_hit_returns_all_rule_types(kb_path):
    r = dal.lookup_single_drug("艾普拉唑肠溶片", kb_path)
    assert r["found"] is True
    rts = {e["rule_type"] for e in r["entries"]}
    assert rts == {"限二线", "限适应症"}


def test_single_miss_returns_not_found_no_network(kb_path):
    r = dal.lookup_single_drug("不存在的神药", kb_path)
    assert r["found"] is False
    assert "无联网回退" in r["note"]


# ─────────── executor 工厂 (模式判定) ───────────

def test_executor_single_mode_wins_when_drug_name_present(loader, kb_path, zd_path):
    ex = dal.create_executor(loader, kb_path, zd_path)
    # 即使 patient_id 被注入, drug_name 存在则走 single
    out = ex(patient_id="J66252", drug_name="人血白蛋白")
    assert "知识库事实" in out and "限适应症" in out


def test_executor_bulk_mode_default(loader, kb_path, zd_path):
    ex = dal.create_executor(loader, kb_path, zd_path)
    out = ex(patient_id="J66252")
    assert "命中" in out and "阿卡波糖片" in out


def test_source_priority_metadata_and_insurance_review_are_formatted(tmp_path, zd_path):
    kb = {
        "version": "oncology-test",
        "drugs": {
            "甲磺酸阿美替尼片": {
                "codes": ["XL01_TEST"],
                "entries": [{
                    "rule_type": "超说明书",
                    "detect_logic": "诊断不符合指导原则适应证",
                    "basis": "EGFR 敏感突变非小细胞肺癌。",
                    "source_type": "guideline",
                    "source_label": "指导原则适应证（医保状态待核对）",
                    "source_refs": ["guideline:161"],
                    "requires_insurance_review": True,
                }],
            }
        },
    }
    kb_file = tmp_path / "oncology.json"
    kb_file.write_text(json.dumps(kb, ensure_ascii=False), encoding="utf-8")
    fees = pd.DataFrame({
        "bah": ["H-J66252"],
        "medins_list_name": ["甲磺酸阿美替尼片"],
        "medins_chrgitm_type": ["西药"],
        "med_list_codg": ["XL01_TEST"],
    })

    result = dal.lookup_patient_drugs(
        "J66252", _StubLoader(fees), kb_file, zd_path, rule_type="超说明书"
    )

    assert result["matches"][0]["source_type"] == "guideline"
    assert result["matches"][0]["source_refs"] == ["guideline:161"]
    assert result["matches"][0]["requires_insurance_review"] is True
    rendered = dal.format_for_agent(result)
    assert "依据层级: 指导原则适应证（医保状态待核对）" in rendered
    assert "医保目录状态待人工核对" in rendered


def test_single_lookup_reports_ambiguous_product_entities(tmp_path):
    kb_file = tmp_path / "oncology-products.json"
    kb_file.write_text(
        json.dumps({
            "version": "3.0",
            "drugs": {
                "注射用曲妥珠单抗": {
                    "codes": ["A"],
                    "entries": [{"rule_type": "限适应症", "basis": "A 限定"}],
                },
                "曲妥珠单抗注射液": {
                    "codes": ["B"],
                    "entries": [{"rule_type": "超说明书", "basis": "B 适应证"}],
                },
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    ambiguous = dal.lookup_single_drug("曲妥珠单抗", kb_file)
    exact = dal.lookup_single_drug("注射用曲妥珠单抗", kb_file)

    assert ambiguous["found"] is False
    assert ambiguous["ambiguous"] is True
    assert ambiguous["candidates"] == ["曲妥珠单抗注射液", "注射用曲妥珠单抗"]
    assert "请提供完整通用名" in dal.format_for_agent(ambiguous)
    assert exact["found"] is True
    assert exact["entries"][0]["basis"] == "A 限定"
