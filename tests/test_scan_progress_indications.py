# -*- coding: utf-8 -*-
"""scan_progress_indications 单测 (add-verdict-gate-layer, 任务 3.3)."""

from __future__ import annotations

import pandas as pd

from javert.data.loader import DataLoader
from javert.tools.scan_progress_indications import create_executor


class _StubLoader(DataLoader):
    def __init__(self, notes: pd.DataFrame):
        self._notes = notes

    def all_notes(self):
        return self._notes

    def all_fees(self):
        return pd.DataFrame()

    def get_notes(self, pid):
        return self._notes

    def get_fees(self, pid):
        return pd.DataFrame()


def _notes(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame({
        "住院号": ["J66252"] * len(rows),
        "子阶段": [r[0] for r in rows],
        "内容": [r[1] for r in rows],
    })


def test_hits_symptom_in_progress_section():
    df = _notes([("病情及处理", "患者诉胸闷不适, 复查心电图")])
    fn = create_executor(_StubLoader(df))
    out = fn("J66252", symptom_kw_list=["胸闷", "气短"])
    assert "病情及处理" in out
    assert "胸闷" in out
    assert "阳性" in out  # 无否认/选项框 → 阳性指征


def test_denial_annotation():
    df = _notes([("病情及处理", "患者否认胸闷, 否认气短")])
    fn = create_executor(_StubLoader(df))
    out = fn("J66252", symptom_kw_list=["胸闷"])
    assert "[否认段]" in out
    # 全部命中带否认标注 → 无阳性
    assert "无真实阳性指征" in out or "0 处" in out or "阳性 0" in out


def test_scans_across_fragmented_sections():
    """症状散在不同病程类子阶段, 不依赖统一『日常查房记录』section."""
    df = _notes([
        ("入院诊断", "甲状腺癌"),          # 非病程族, 不扫
        ("诊疗经过", "病程中出现气短"),     # 病程族 hit
        ("目前情况", "下肢水肿明显"),       # 病程族 hit
    ])
    fn = create_executor(_StubLoader(df))
    out = fn("J66252", symptom_kw_list=["气短", "下肢水肿"])
    assert "诊疗经过" in out
    assert "目前情况" in out
    assert "气短" in out
    assert "下肢水肿" in out


def test_no_progress_sections_returns_warning():
    df = _notes([("入院诊断", "甲状腺癌, 胸闷")])  # 只有非病程 section
    fn = create_executor(_StubLoader(df))
    out = fn("J66252", symptom_kw_list=["胸闷"])
    assert "无病程" in out or "未数字化" in out


def test_empty_symptom_list():
    df = _notes([("病情及处理", "胸闷")])
    fn = create_executor(_StubLoader(df))
    out = fn("J66252", symptom_kw_list=[])
    assert "未提供" in out
