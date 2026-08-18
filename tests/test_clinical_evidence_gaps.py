# -*- coding: utf-8 -*-
"""临床证据链缺口回归：费用单位、结构化医嘱、OCR 报告全文兜底。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from javert.data import hub_source as hs
from javert.data.examination_loader import ExaminationLoader
from javert.data.lab_loader import LabLoader
from javert.data.loader import DataLoader
from javert.tools import catalog_lookup, search_examinations, search_lab_results, search_orders


class _NotesLoader(DataLoader):
    def __init__(self, notes: pd.DataFrame):
        self._notes = notes

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        return self._notes[self._notes["住院号"] == patient_id]

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return pd.DataFrame()

    def all_notes(self) -> pd.DataFrame:
        return self._notes

    def all_fees(self) -> pd.DataFrame:
        return pd.DataFrame()


def test_fetch_fees_preserves_unit_and_order_id(monkeypatch):
    fee_row = pd.DataFrame([{
        "YLJGYQDM": "OCR", "SFMXID": "F1", "STFBZ": "0", "JZLSH": "P1",
        "YZID": "O1", "MXFYLB": "03", "FYFSSJ": "2026-04-20",
        "MXXMBM": "", "MXXMBMYB": "012403000050000",
        "MXXMMC": "眼压检查费/单侧", "MXXMDW": "次", "MXXMDJ": "12",
        "MXXMSL": "4", "MXXMJE": "48",
    }])

    def fake_q(cn, sql, params=()):
        if "sys.tables" in sql:
            return pd.DataFrame(columns=["x"])
        result = fee_row.copy()
        for column in hs._FEE_EXT_COLS:
            result[column] = ""
        return result

    monkeypatch.setattr(hs, "q", fake_q)

    fees = hs.fetch_fees(None, ["P1"], {"OCR": "OCR"})

    assert fees.iloc[0]["unit"] == "次"
    assert fees.iloc[0]["order_id"] == "O1"


def test_fetch_notes_merges_structured_advice(monkeypatch):
    doc = pd.DataFrame([{
        "JZLSH": "P1", "JLSJ": "2026-04-20 09:00:00", "WSMC": "病程记录",
        "WSLB": "03", "DLBT": "", "ZW": "一般情况稳定",
    }])
    summary_columns = [
        "JZLSH", "CYSJ", "YYZTBBT1", "YYZTB1", "YYZTBBT2", "YYZTB2",
        *(column for column, _ in hs.SUMMARY_COL2SEC),
    ]
    advice = pd.DataFrame([{
        "JZLSH": "P1", "YZZH": "G1", "YZSM": "", "MXXMMC": "结膜囊冲洗费",
        "YZXDSJ": "2026-04-20 08:55:00", "YZZXSJ": "2026-04-20 08:58:00",
        "YZZZSJ": "", "YZLB": "ST", "XMMXSL": "1", "XMMXDW": "次",
    }])

    def fake_q(cn, sql, params=()):
        if "sys.tables" in sql:
            return pd.DataFrame([{"x": "1"}])
        if "DRADVICE_DETAIL" in sql:
            return advice
        if "MEDICAL_DOCUMENT" in sql:
            return doc
        return pd.DataFrame(columns=summary_columns)

    monkeypatch.setattr(hs, "q", fake_q)

    notes = hs.fetch_notes(None, ["P1"])
    order = notes[notes["来源文件"] == "data_hub_advice"].iloc[0]

    assert order["阶段"] == "医嘱单"
    assert "类型=ST" in order["内容"]
    assert "项目=结膜囊冲洗费" in order["内容"]
    assert "数量=1次" in order["内容"]


def test_examination_tool_falls_back_to_report_notes(tmp_path: Path):
    exam_loader = ExaminationLoader(tmp_path / "missing.csv")
    notes_loader = _NotesLoader(pd.DataFrame([{
        "住院号": "SYNTH-EYE-001", "事件时间": "2026-04-20",
        "阶段": "化验/检查/病理报告", "子阶段": "",
        "内容": "眼科AB型超声检查报告单：A型与B型超声数据。", "来源文件": "synthetic",
    }]))

    execute = search_examinations.create_executor(exam_loader, notes_loader)
    output = execute("SYNTH-EYE-001", keyword="AB型超声")

    assert "非结构化报告候选" in output
    assert "眼科AB型超声检查报告单" in output


def test_lab_tool_falls_back_to_report_notes(tmp_path: Path):
    lab_loader = LabLoader(tmp_path / "missing.csv")
    notes_loader = _NotesLoader(pd.DataFrame([{
        "住院号": "SYNTH-LAB-001", "事件时间": "2026-04-20",
        "阶段": "化验/检查/病理报告", "子阶段": "",
        "内容": "尿常规检验报告：白细胞计数正常。", "来源文件": "synthetic",
    }]))

    execute = search_lab_results.create_executor(lab_loader, notes_loader)
    output = execute("SYNTH-LAB-001", item_keyword="尿常规")

    assert "非结构化报告候选" in output
    assert "尿常规检验报告" in output


def test_search_orders_prefers_structured_rows():
    notes = pd.DataFrame([
        {
            "住院号": "SYNTH-ORDER-001", "事件时间": "2026-04-20",
            "阶段": "医嘱单", "子阶段": "临时医嘱",
            "内容": "类型=ST；项目=结膜囊冲洗费；数量=1次；执行=2026-04-20 08:58:00",
            "来源文件": "data_hub_advice",
        },
        {
            "住院号": "SYNTH-ORDER-001", "事件时间": "2026-04-20",
            "阶段": "长期嘱单", "子阶段": "",
            "内容": "扫描页重复出现结膜囊冲洗费 st", "来源文件": "synthetic",
        },
    ])

    output = search_orders.create_executor(_NotesLoader(notes))(
        "SYNTH-ORDER-001", keyword="结膜囊冲洗"
    )

    assert "结构化医嘱（匹配 1 行" in output
    assert "扫描页重复" not in output
    assert "数量=1次" in output


def test_search_orders_ocr_text_is_explicitly_weak():
    notes = pd.DataFrame([{
        "住院号": "SYNTH-ORDER-002", "事件时间": "2026-04-20",
        "阶段": "长期嘱单", "子阶段": "",
        "内容": "结膜囊冲洗费 st，眼压检查费。", "来源文件": "synthetic",
    }])

    output = search_orders.create_executor(_NotesLoader(notes))(
        "SYNTH-ORDER-002", keyword="结膜囊冲洗"
    )

    assert "非结构化医嘱候选" in output
    assert "文本命中次数不等于独立医嘱次数" in output


def test_catalog_lookup_honors_service_date(tmp_path: Path):
    old_path = tmp_path / "catalog-old.xlsx"
    new_path = tmp_path / "catalog-new.xlsx"
    base = {
        "医保编码": "", "状态": "有效", "信息失效日期": "99991231",
        "项目编码": "", "项目内涵": "", "收费标准": "", "备注": "",
        "限定内容": "", "费用类别": "检查费",
    }
    pd.DataFrame([{**base, "信息起效日期": "20110120", "项目名称": "眼压检查",
                   "计价单位": "次", "国家医保编码": "003103000270000"}]).to_excel(old_path, index=False)
    pd.DataFrame([{**base, "信息起效日期": "20260630", "项目名称": "眼压检查费",
                   "计价单位": "单侧", "国家医保编码": "012403000050000"}]).to_excel(new_path, index=False)

    execute = catalog_lookup.create_executor([old_path, new_path])
    historical = execute(item_name="眼压检查费/单侧", service_date="2026-04-20")
    current = execute(item_code="012403000050000", service_date="2026-07-01")

    assert "计价单位=次" in historical
    assert "计价单位=单侧" not in historical
    assert "计价单位=单侧" in current
