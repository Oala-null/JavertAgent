# -*- coding: utf-8 -*-
"""_summary_to_notes: 标准表 LEAVEHOSPITAL_SUMMARY → case_notes 重建 (46表标准化)."""
import pandas as pd

from javert.data.hub_source import SUMMARY_COL2SEC, _summary_to_notes, fetch_basics

_COLS = ["JZLSH", "CYSJ", "YYZTBBT1", "YYZTB1", "YYZTBBT2", "YYZTB2"] + [c for c, _ in SUMMARY_COL2SEC]


def _row(**kw) -> pd.DataFrame:
    base = {c: "-" for c in _COLS}
    base.update(JZLSH="P1", CYSJ="2026-01-05 10:00:00", YYZTBBT1="", YYZTB1="", YYZTBBT2="", YYZTB2="")
    base.update(kw)
    return pd.DataFrame([base], columns=_COLS)


def test_columns_become_sections():
    rows = _summary_to_notes(_row(RYZD="脑梗死", CYYZ="随访"))
    assert {(r["子阶段"], r["内容"]) for r in rows} == {("入院诊断", "脑梗死"), ("出院医嘱", "随访")}
    assert all(r["阶段"] == "出院小结" and r["住院号"] == "P1" and r["事件时间"] == "2026-01-05 10:00:00"
               for r in rows)


def test_dash_and_empty_skipped_sentinel_time_blanked():
    rows = _summary_to_notes(_row(RYZD="x", CYSJ="1900-01-01 00:00:00"))
    assert len(rows) == 1 and rows[0]["事件时间"] == ""


def test_dynamic_title_blocks():
    rows = _summary_to_notes(_row(YYZTBBT1="健康教育", YYZTB1="低盐饮食"))
    assert rows == [{"住院号": "P1", "事件时间": "2026-01-05 10:00:00", "阶段": "出院小结",
                     "子阶段": "健康教育", "内容": "低盐饮食", "来源文件": "data_hub"}]


def test_fetch_basics_uses_standard_summary_fields(monkeypatch):
    import javert.data.hub_source as hs

    row = pd.DataFrame([{
        "JZLSH": "P1",
        "BRXB": "2",
        "BRNL": "45",
        "RYSJ": "2026-01-01 08:00:00",
        "CYSJ": "2026-01-03 09:00:00",
        "ZYTS": "3",
        "KS": "测试科",
        "ZZYSRYXM": "",
        "ZYYSYHRYXM": "住院医生",
    }])
    monkeypatch.setattr(hs, "q", lambda cn, sql, params=(): row)

    basics = fetch_basics(None, ["P1"], table_prefix="desus_")

    assert basics.iloc[0].to_dict() == {
        "patient_id": "P1",
        "gender": "女",
        "age": "45",
        "admission_date": "2026-01-01",
        "discharge_date": "2026-01-03",
        "los_days": "3",
        "department": "测试科",
        "doctor": "住院医生",
    }


# ── v2: fetch_notes 合流规则 (WSLB 可空按 WSMC 派生 05; 扩展表优先, 标准表补缺) ──

def _fake_q(doc_rows, summ_rows):
    doc_cols = ["JZLSH", "JLSJ", "WSMC", "WSLB", "DLBT", "ZW"]
    def q(cn, sql, params=()):
        if "TB_CIS_MEDICAL_DOCUMENT" in sql:
            return pd.DataFrame(doc_rows, columns=doc_cols)
        return pd.DataFrame(summ_rows, columns=_COLS)
    return q


def test_fetch_notes_wslb_empty_derived_from_wsmc(monkeypatch):
    import javert.data.hub_source as hs
    # P1: 扩展表有出院小结但 WSLB 空 (医院最小形态) + 标准表也有行 → 扩展表优先, 不重复
    doc = [["P1", "", "出院小结", "", "", "全文……"]]
    summ = _row(RYZD="脑梗死").values.tolist()
    monkeypatch.setattr(hs, "q", _fake_q(doc, summ))
    notes = hs.fetch_notes(None, ["P1"])
    d = notes[notes["阶段"] == "出院小结"]
    assert len(d) == 1 and d.iloc[0]["内容"] == "全文……"  # 无标准表重复行


def test_fetch_notes_standard_only_patient_reconstructed(monkeypatch):
    import javert.data.hub_source as hs
    # P1: 扩展表只有病程 (无 05) → 出院小结从标准表重建
    doc = [["P1", "", "首次病程记录", "03", "", "病程……"]]
    summ = _row(RYZD="脑梗死", CYYZ="随访").values.tolist()
    monkeypatch.setattr(hs, "q", _fake_q(doc, summ))
    notes = hs.fetch_notes(None, ["P1"])
    assert len(notes[notes["阶段"] == "出院小结"]) == 2
    assert len(notes[notes["阶段"] == "首次病程记录"]) == 1
