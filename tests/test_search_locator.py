# -*- coding: utf-8 -*-
"""tests for search 工具 locator 增强 + Evidence.anchor 前向字段 (evidence-anchoring G8).

工具 keyword 模式吐 char/行定位 (不破坏纯文本契约) + Evidence.anchor 可选默认 None +
resolve_hits 优先采用 evidence.anchor (老 evidence 无此字段走匹配阶梯兜底).
"""

from __future__ import annotations

import json

import pandas as pd

from javert.audit.result import Evidence
from javert.data.loader import DataLoader
from javert.tools.search_fees import create_executor as fees_executor
from javert.tools.search_notes import create_executor as notes_executor
from javert.web.hit_resolver import resolve_hits_from_json


class _Stub(DataLoader):
    def __init__(self, notes, fees):
        self._n, self._f = notes, fees

    def get_notes(self, pid):
        return self._n[self._n["住院号"].astype(str).str.strip() == pid]

    def get_fees(self, pid):
        return self._f[self._f["bah"].astype(str).str.contains(pid, na=False)]

    def all_notes(self):
        return self._n

    def all_fees(self):
        return self._f


def _loader():
    notes = pd.DataFrame({
        "住院号": ["J1", "J1"],
        "阶段": ["入院", "出院"],
        "子阶段": ["入院诊断", "出院诊断"],
        "内容": ["甲状腺乳头状癌; 高血压", "1.甲状腺乳头状癌 术后"],
        "来源文件": ["t", "t"],
    })
    fees = pd.DataFrame({
        "bah": ["H-J1 ", "H-J1 "],
        "fee_ocur_time": ["2026-01-01", "2026-01-01"],
        "det_item_fee_sumamt": [200.0, 80.0],
        "pric": [200.0, 80.0],
        "medins_list_name": ["CT平扫", "甲状腺癌根治术"],
    })
    return _Stub(notes, fees)


# =========================================================
# 8.1 工具 locator 增强 (additive, 不破坏既有断言)
# =========================================================
def test_search_notes_emits_char_locator():
    out = notes_executor(_loader())(patient_id="J1", keyword="甲状腺乳头状癌")
    assert "搜索结果" in out          # 既有契约不破坏
    assert "⟨子阶段=入院诊断 char=0⟩" in out  # 入院诊断段 keyword 在 char 0
    # char 偏移确为 content.find(keyword)
    assert "char=0⟩" in out


def test_search_fees_emits_row_locator():
    out = fees_executor(_loader())(patient_id="J1", keyword="CT")
    assert "搜索结果" in out
    assert "⟨行=1 项目=CT平扫⟩" in out


# =========================================================
# 8.2 Evidence.anchor 前向字段
# =========================================================
def test_evidence_anchor_optional_default_none():
    e = Evidence(source="note", locator="出院诊断", text="x")
    assert e.anchor is None
    assert "anchor" in e.model_dump()


def test_evidence_old_json_without_anchor_parses():
    # 老数据 evidence_json 无 anchor key → 仍解析, anchor=None
    e = Evidence.model_validate({"source": "fee", "locator": "a", "text": "b"})
    assert e.anchor is None


def test_evidence_anchor_round_trips():
    e = Evidence.model_validate({
        "source": "note", "locator": "出院诊断", "text": "t",
        "anchor": {"tab": "notes", "subsection": "出院诊断",
                   "query": "甲状腺乳头状癌", "char_start": 2, "char_end": 8},
    })
    assert e.anchor["query"] == "甲状腺乳头状癌"


# =========================================================
# 8.3 resolve_hits 优先采用 evidence.anchor; 老 evidence 走阶梯
# =========================================================
def test_resolver_prefers_evidence_anchor():
    ev = json.dumps([{
        "source": "note_diagnosis", "locator": "患者诊断列表",
        "text": "诊断「腹腔肿瘤」",
        "anchor": {"tab": "notes", "subsection": "出院诊断",
                   "query": "甲状腺乳头状癌", "char_start": 5, "char_end": 11},
    }], ensure_ascii=False)
    h = resolve_hits_from_json(ev, "[]", None, None, {})[0]
    assert h.anchor.match_level == "evidence"
    assert h.anchor.query == "甲状腺乳头状癌"
    assert h.anchor.subsection == "出院诊断"


def test_resolver_old_evidence_no_anchor_uses_ladder():
    ev = json.dumps([{
        "source": "note_diagnosis", "locator": "出院诊断", "text": "诊断「甲状腺癌」",
    }], ensure_ascii=False)
    h = resolve_hits_from_json(ev, "[]", None, None, {})[0]
    assert h.anchor.match_level == "locator"
    assert h.anchor.subsection == "出院诊断"
