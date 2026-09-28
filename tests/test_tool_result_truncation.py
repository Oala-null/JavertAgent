# -*- coding: utf-8 -*-
"""分段截断 (fix-drug-audit-precision 1a) 单测.

覆盖:
  - _truncate 含标记: 头部整段保全, 只截明细, 尾附截断提示
  - _truncate 头部本身超预算: 头部硬截到上限 (fix-scan-residuals, 防 context 溢出)
  - _truncate 无标记: 旧尾截断回归
  - drug_audit_lookup bulk: ground truth 诊断在标记前 (必留段), 超长仍完整
  - search_notes: 反向语义告警在标记前 (必留段)
"""

from __future__ import annotations

from javert.audit.runner import RETAIN_HEAD_MARKER, _truncate


def test_truncate_marker_keeps_head_cuts_detail():
    head = "必留头部诊断: 2型糖尿病\n" + RETAIN_HEAD_MARKER
    detail = "\n明细" + "药" * 5000
    out = _truncate(head + detail, limit=200)
    assert head in out                       # 头部逐字保全 (含标记行)
    assert "2型糖尿病" in out
    assert "明细已截断" in out               # 截断提示
    assert str(len(detail)) in out           # 含被截明细字符数
    assert len(out) < len(head + detail)


def test_truncate_head_over_budget_hard_cut():
    # fix-scan-residuals: 头部超总预算时对头部也硬截 (防 context 溢出 400),
    # 推翻 fix-drug-audit-precision 的「头部无条件全留」旧断言.
    head = "长头部" * 200 + RETAIN_HEAD_MARKER  # 头部已超 limit
    out = _truncate(head + "明细内容", limit=50)
    assert "明细内容" not in out             # 明细全砍
    assert "头部超总预算" in out             # 头部截断提示
    assert str(len(head)) in out             # 含原头部长度
    assert len(out) <= 50 + 40               # 总长有界 (limit + 提示行)


def test_truncate_no_marker_tail_cut_regression():
    text = "无标记内容" * 1000
    out = _truncate(text, limit=100)
    assert out == text[:100] + f"\n...[已截断, 原长 {len(text)}]"


def test_truncate_under_limit_untouched():
    text = "短文本" + RETAIN_HEAD_MARKER + "明细"
    assert _truncate(text, limit=10_000) == text


def test_drug_bulk_ground_truth_survives_truncation():
    import javert.tools.drug_audit_lookup as dal

    # 很多命中药 (明细), 一条主诊 (ground truth 必留段)
    result = {
        "mode": "bulk",
        "patient_id": "P1",
        "rule_type_filter": "",
        "total_drug_fees": 60,
        "matches": [
            {
                "generic_name": f"药{i:03d}",
                "rule_type": "限适应症",
                "basis": "限某某适应症" * 20,
                "detect_logic": "",
                "fee_names": [f"注射用药{i:03d}"],
                "needs_review": False,
            }
            for i in range(60)
        ],
        "diagnoses": [{"name": "2型糖尿病", "code": "E11.900", "is_main": True}],
        "zd_available": True,
    }
    text = dal.format_for_agent(result)
    # 诊断在标记之前 (必留段)
    assert RETAIN_HEAD_MARKER in text
    assert text.index("2型糖尿病") < text.index(RETAIN_HEAD_MARKER)
    assert text.index("命中药品") > text.index(RETAIN_HEAD_MARKER)
    # 截断到小预算后, ground truth 仍完整可见
    out = _truncate(text, limit=200)
    assert "[主诊] 2型糖尿病" in out
    assert "明细已截断" in out


def test_search_notes_reverse_warning_before_marker():
    import pandas as pd

    from javert.data.loader import DataLoader
    from javert.tools.search_notes import create_executor

    class _L(DataLoader):
        def __init__(self, notes):
            self._n = notes

        def all_notes(self):
            return self._n

        def all_fees(self):
            return pd.DataFrame()

        def get_notes(self, pid):
            return self._n

        def get_fees(self, pid):
            return pd.DataFrame()

    notes = pd.DataFrame(
        {
            "住院号": ["J001"],
            "子阶段": ["既往史"],
            "内容": ["否认高血压、糖尿病、冠心病、房颤等慢性病史"],
        }
    )
    out = create_executor(_L(notes))("J001", keyword="冠心病")
    assert "[否认段]" in out
    assert RETAIN_HEAD_MARKER in out
    # 反向语义告警在必留头部 (标记之前)
    assert out.index("反向语义") < out.index(RETAIN_HEAD_MARKER)
