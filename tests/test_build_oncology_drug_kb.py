# -*- coding: utf-8 -*-
"""肿瘤药知识库的权威优先级与保守药名匹配测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_oncology_drug_kb.py"
SPEC = importlib.util.spec_from_file_location("build_oncology_drug_kb", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _guideline(indication: str = "HER2 阳性乳腺癌") -> list[dict]:
    return [
        {
            "system": "乳腺癌",
            "chapter_title": "曲妥珠单抗 Trastuzumab",
            "generic_name": "曲妥珠单抗",
            "indication": indication,
        }
    ]


def test_医保限定优先于指导原则() -> None:
    status, entry = MODULE.select_effective_entry(
        [
            {
                "catalog_status": "catalogued",
                "restriction": "限 HER2 阳性乳腺癌患者。",
                "status": "有效",
            }
        ],
        _guideline("HER2 阳性乳腺癌及胃癌"),
    )

    assert status == "restricted"
    assert entry["rule_type"] == "限适应症"
    assert entry["source_type"] == "insurance"
    assert entry["basis"] == "限 HER2 阳性乳腺癌患者。"


def test_确认目录内且医保限定为空时回退指导原则() -> None:
    status, entry = MODULE.select_effective_entry(
        [{"catalog_status": "catalogued", "restriction": "", "status": "有效"}],
        _guideline(),
    )

    assert status == "unrestricted"
    assert entry["rule_type"] == "超说明书"
    assert entry["source_type"] == "guideline"
    assert "【乳腺癌】HER2 阳性乳腺癌" in entry["basis"]


def test_目录属性未知时按指导原则兜底但保留医保核对标记() -> None:
    status, entry = MODULE.select_effective_entry(
        [{"catalog_status": "unknown", "restriction": "", "status": "有效"}],
        _guideline(),
    )

    assert status == "unknown"
    assert entry["source_type"] == "guideline"
    assert entry["requires_insurance_review"] is True


def test_同组产品并非全部确认入目录时保留医保核对标记() -> None:
    status, entry = MODULE.select_effective_entry(
        [
            {"catalog_status": "catalogued", "restriction": "", "status": "有效"},
            {"catalog_status": "unknown", "restriction": "", "status": "有效"},
        ],
        _guideline(),
    )

    assert status == "unknown"
    assert entry["source_type"] == "guideline"
    assert entry["requires_insurance_review"] is True


def test_保守归一只做全等不让相似单抗串味() -> None:
    assert MODULE.normalize_generic_name("注射用曲妥珠单抗") == "曲妥珠单抗"
    assert MODULE.normalize_generic_name("甲磺酸奥希替尼片") == "奥希替尼"
    assert MODULE.normalize_generic_name("醋酸阿比特龙片(Ⅱ)") == "阿比特龙"
    assert MODULE.names_match("曲妥珠单抗", "注射用曲妥珠单抗")
    assert not MODULE.names_match("曲妥珠单抗", "德曲妥珠单抗")
    assert not MODULE.names_match("西妥昔单抗", "西妥昔单抗β")


def test_指导原则页首星号不吞掉下一药章节() -> None:
    chapters = MODULE.parse_guideline_pages(
        [
            (
                394,
                "泌尿系统肿瘤用药\n"
                "※十五、他拉唑帕利 Talazoparib\n"
                "制剂与规格：胶囊：0.25mg\n"
                "适应证：HRR 基因突变的前列腺癌。\n"
                "合理用药要点：1.用药前检测 HRR。\n",
            )
        ]
    )

    assert len(chapters) == 1
    assert chapters[0]["generic_name"] == "他拉唑帕利"
    assert chapters[0]["system"] == "泌尿系统肿瘤"


def test_同一canonical的医院原始通用名必须隔离医保限定和产品码() -> None:
    kb = MODULE.assemble_knowledge_base(
        [
            {
                "section": "regular",
                "category_code": "XL01",
                "catalog_number": "1",
                "generic_name": "曲妥珠单抗注射液",
                "restriction": "国家目录限定不得覆盖医院原始名分组",
                "pdf_page": 1,
            }
        ],
        [
            {
                "drug_code": "XL01-RESTRICTED",
                "generic_name": "曲妥珠单抗注射液",
                "catalog_status": "catalogued",
                "restriction": "限 HER2 阳性乳腺癌患者。",
                "status": "有效",
            },
            {
                "drug_code": "XL01-UNRESTRICTED",
                "generic_name": "注射用曲妥珠单抗",
                "catalog_status": "catalogued",
                "restriction": "",
                "status": "有效",
            },
        ],
        _guideline(),
    )

    restricted = kb["drugs"]["曲妥珠单抗注射液"]
    unrestricted = kb["drugs"]["注射用曲妥珠单抗"]
    assert restricted["canonical_name"] == unrestricted["canonical_name"] == "曲妥珠单抗"
    assert len(restricted["sources"]["national_catalog"]) == 1
    assert len(unrestricted["sources"]["national_catalog"]) == 1
    assert restricted["codes"] == ["XL01-RESTRICTED"]
    assert restricted["effective"]["source_type"] == "insurance"
    assert restricted["effective"]["basis"] == "限 HER2 阳性乳腺癌患者。"
    assert restricted["effective"]["source_refs"] == ["hospital:XL01-RESTRICTED"]
    assert unrestricted["codes"] == ["XL01-UNRESTRICTED"]
    assert unrestricted["effective"]["source_type"] == "guideline"
    assert "医保无限定时兜底" in unrestricted["effective"]["source_label"]
    assert len(kb["drugs"]) == 2


def test_无医院产品的国家目录多剂型只留证据不生成生效条目() -> None:
    national = [
        {
            "section": "regular",
            "category_code": "XL01",
            "catalog_number": str(index),
            "generic_name": "测试药",
            "dosage_form": dosage,
            "restriction": "同一医保限定",
            "pdf_page": 1,
        }
        for index, dosage in enumerate(["片剂", "注射剂"], start=1)
    ]
    kb = MODULE.assemble_knowledge_base(
        national,
        [],
        [
            {
                "system": "测试肿瘤",
                "chapter_title": "测试药 Test",
                "generic_name": "测试药",
                "indication": "测试适应证",
            }
        ],
    )

    drug = kb["drugs"]["测试药"]
    assert drug["entity_type"] == "canonical_only"
    assert drug["insurance_status"] == "conflict"
    assert drug["entries"] == []
    assert drug["effective"] is None
    assert "国家目录存在多剂型/限定冲突" in MODULE.build_review_rows(kb)[0]["人工核对项"]


def test_医院限定为空时同原始名国家医保限定仍优先() -> None:
    kb = MODULE.assemble_knowledge_base(
        [{
            "section": "negotiated",
            "category_code": "XL01",
            "catalog_number": "8",
            "generic_name": "测试药片",
            "restriction": "限特定肿瘤。",
            "pdf_page": 8,
        }],
        [{
            "drug_code": "XL01-TEST",
            "generic_name": "测试药片",
            "catalog_status": "catalogued",
            "restriction": "",
            "status": "有效",
        }],
        [{
            "system": "测试肿瘤",
            "chapter_title": "测试药 Test",
            "generic_name": "测试药",
            "indication": "较宽的指导原则适应证。",
        }],
    )

    drug = kb["drugs"]["测试药片"]
    assert drug["effective"]["source_type"] == "insurance"
    assert drug["effective"]["basis"] == "限特定肿瘤。"
    assert drug["effective"]["source_refs"] == [
        "national:negotiated:8:pdf_page_8"
    ]
