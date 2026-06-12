# -*- coding: utf-8 -*-
"""search_notes v0.3 边界修复单测.

覆盖:
  1. keyword 模式默认过滤噪音段 (手术中或手术后可能发生的并发症 / 医方告知 等).
  2. keyword hit 在 "否认 X" 上下文 → 标注 [否认段].
  3. keyword hit 在 □ 选项框附近 → 标注 [选项框].
  4. section 模式命中 0 条 + 其他段有 "见 X" 引用 → 输出 ETL 缺失警告.
  5. include_noise=true 旁路过滤.
"""

from __future__ import annotations

import pandas as pd
import pytest

from javert.data.loader import DataLoader
from javert.tools.search_notes import create_executor


class _StubLoader(DataLoader):
    def __init__(self, notes: pd.DataFrame, fees: pd.DataFrame | None = None):
        self._notes = notes
        self._fees = fees if fees is not None else pd.DataFrame()

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        return self._notes[self._notes["住院号"].astype(str).str.strip() == patient_id]

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return self._fees

    def all_notes(self) -> pd.DataFrame:
        return self._notes

    def all_fees(self) -> pd.DataFrame:
        return self._fees


def _notes(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """rows: list of (patient_id, section, content)."""
    return pd.DataFrame(
        {
            "住院号": [r[0] for r in rows],
            "事件时间": ["2026-01-01"] * len(rows),
            "阶段": ["test"] * len(rows),
            "子阶段": [r[1] for r in rows],
            "内容": [r[2] for r in rows],
            "来源文件": ["test"] * len(rows),
        }
    )


# ---------- 1. 噪音段默认过滤 ----------


def test_keyword_filters_noise_section_complications():
    """关键词命中在 '手术中或手术后可能发生的并发症' → 视为噪音, 默认过滤."""
    notes = _notes(
        [
            ("J001", "手术中或手术后可能发生的并发症：", "麻醉意外: 心搏骤停、药物过敏..."),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", keyword="心搏骤停")
    assert "仅在告知/风险噪音段命中" in out
    assert "手术中或手术后可能发生的并发症" in out


def test_keyword_filters_noise_section_transfusion_notice():
    """'医方告知' 段含 '感染肝炎' → 默认过滤 (R153 AFP 误读修复)."""
    notes = _notes(
        [
            ("J001", "医方告知", "异体输血可能感染肝炎(乙肝、丙肝等)、艾滋病、梅毒..."),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", keyword="肝炎")
    assert "仅在告知/风险噪音段命中" in out


def test_keyword_real_hit_preserved_alongside_noise():
    """真段命中 + 噪音段命中并存 → 真段保留, 噪音段被过滤, 末尾提示数量."""
    notes = _notes(
        [
            ("J001", "现病史", "患者有冠心病病史, 长期服药"),
            ("J001", "手术中或手术后可能发生的并发症：", "心搏骤停、冠心病急性发作..."),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", keyword="冠心病")
    assert "现病史" in out
    assert "已过滤 1 条噪音段" in out


def test_keyword_include_noise_bypass():
    """include_noise=true 时不过滤."""
    notes = _notes(
        [
            ("J001", "手术中或手术后可能发生的并发症：", "心搏骤停、药物过敏..."),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", keyword="心搏骤停", include_noise=True)
    assert "手术中或手术后可能发生的并发症" in out
    assert "仅在告知/风险噪音段命中" not in out


# ---------- 2. 否认上下文标注 ----------


def test_keyword_denial_context_annotated():
    """既往史中 '否认冠心病' → 命中应标 [否认段]."""
    notes = _notes(
        [
            ("J001", "既往史", "否认高血压、糖尿病、冠心病、房颤等慢性病史"),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", keyword="冠心病")
    assert "[否认段]" in out


def test_keyword_no_denial_when_far_away():
    """否认词在窗口外 → 不应误标 [否认段]."""
    long_prefix = "患者有明显胸闷、活动后心悸, 既往" + "其他主诉 " * 30 + ", 现诊断为冠心病."
    notes = _notes([("J001", "现病史", long_prefix)])
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", keyword="冠心病")
    assert "[否认段]" not in out


# ---------- 3. 选项框标注 ----------


def test_keyword_option_box_annotated():
    """关键词附近有 □ → 标 [选项框]."""
    notes = _notes(
        [
            ("J001", "患者离开手术室前", "患者: □ 恢复室 □ 病房 □ ICU 病房 □ 急诊 □ 离院"),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", keyword="ICU")
    assert "[选项框]" in out


def test_keyword_denial_and_option_box_combined():
    """同时有否认前导 + 选项框 → 标 [否认段/选项框]."""
    notes = _notes(
        [
            ("J001", "核查单", "未见明显异常: □ 心电图 □ 心脏超声 □ 胸片"),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", keyword="心电图")
    # 未见在前导窗口内 + □ 在邻近 → 双标
    assert "[否认段/选项框]" in out or ("[否认段]" in out and "[选项框]" in out)


# ---------- 4. section ETL 缺失探测 ----------


def test_section_etl_gap_detected_when_cross_referenced():
    """search_notes(section='麻醉记录') 0 条 + 手术经过段含 '见麻醉记录' → ETL 警告."""
    notes = _notes(
        [
            ("J001", "手术经过", "术中出血 200ml, 见麻醉记录, 输血 1U."),
            ("J001", "手术信息", "行肩关节置换术."),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", section="麻醉记录")
    assert "ETL 数据完整性警告" in out
    assert "手术经过" in out
    assert "见麻醉记录" in out
    assert "INCONCLUSIVE" in out
    assert "etl_warning" in out


def test_section_not_found_no_etl_when_no_reference():
    """section 不存在 + 全文也无引用 → 走旧的 '未找到该子阶段' 路径."""
    notes = _notes(
        [
            ("J001", "手术经过", "术中出血 200ml, 输血 1U."),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", section="麻醉记录")
    assert "未找到该子阶段" in out
    assert "ETL" not in out


def test_section_etl_gap_supports_detailed_prefix():
    """'详见 X' 也算 ETL 引用."""
    notes = _notes(
        [
            ("J001", "出院小结", "麻醉过程详见麻醉记录单."),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", section="麻醉记录")
    assert "ETL 数据完整性警告" in out


# ---------- 5. 现有行为不破坏 ----------


def test_keyword_existing_real_hits_still_work():
    """真段命中 + 无噪音 → 行为与旧版本一致."""
    notes = _notes(
        [
            ("J001", "入院诊断", "甲状腺乳头状癌"),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001", keyword="甲状腺")
    assert "搜索结果" in out
    assert "入院诊断" in out


def test_directory_mode_unchanged():
    """无参数 → 目录模式, 行为不变."""
    notes = _notes(
        [
            ("J001", "入院诊断", "甲状腺乳头状癌"),
            ("J001", "出院诊断", "甲状腺癌术后"),
        ]
    )
    fn = create_executor(_StubLoader(notes))
    out = fn(patient_id="J001")
    assert "文书目录" in out
    assert "入院诊断" in out
    assert "出院诊断" in out
