# -*- coding: utf-8 -*-
"""4 个工具的 smoke test."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from javert.data.loader import DataLoader
from javert.tools.registry import build_executor


class _StubLoader(DataLoader):
    """In-memory loader, 替代 CsvLoader 让测试无 I/O."""

    def __init__(self, notes: pd.DataFrame, fees: pd.DataFrame):
        self._notes = notes
        self._fees = fees

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        return self._notes[self._notes["住院号"].astype(str).str.strip() == patient_id]

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return self._fees[self._fees["bah"].astype(str).str.contains(patient_id, na=False)]

    def all_notes(self) -> pd.DataFrame:
        return self._notes

    def all_fees(self) -> pd.DataFrame:
        return self._fees


@pytest.fixture
def stub_loader() -> _StubLoader:
    notes = pd.DataFrame({
        "住院号": ["J66252"] * 4,
        "事件时间": ["2026-01-01"] * 4,
        "阶段": ["入院", "入院", "手术", "出院"],
        "子阶段": ["入院诊断", "出院诊断", "手术信息", "出院诊断"],
        "内容": [
            "甲状腺乳头状癌; 高血压",
            "甲状腺癌术后",
            "行甲状腺癌根治术 + 中央区淋巴清扫",
            "1.甲状腺乳头状癌\n2.高血压",
        ],
        "来源文件": ["test"] * 4,
    })
    fees = pd.DataFrame({
        "bah": ["H31010600042-J66252 "] * 4,
        "fee_ocur_time": ["2026-01-01"] * 4,
        "cnt": [1.0] * 4,
        "pric": [80.0, 200.0, 300.0, 50.0],
        "det_item_fee_sumamt": [80.0, 200.0, 300.0, 50.0],
        "medins_list_name": ["甲状腺癌根治术", "CT平扫", "病理检查", "胸腺肽注射"],
    })
    return _StubLoader(notes, fees)


@pytest.fixture
def drug_map_tmp(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "configs"
    cfg_path.mkdir()
    drug_map = {
        "version": "test",
        "drugs": [
            {
                "pattern": "胸腺肽",
                "tier": 2,
                "indication": "免疫调节; 慢性肝炎",
                "icd_candidates": ["B18", "B19"],
                "generic_name": "胸腺肽",
            }
        ],
        "exclusions": [],
    }
    (cfg_path / "drug_indication_map.json").write_text(
        json.dumps(drug_map, ensure_ascii=False), encoding="utf-8"
    )
    # 让 cfg.resolve('configs') 指向 tmp_path/configs
    from javert import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "PROJECT_ROOT", tmp_path)
    cfg_mod.reset_config_cache()
    # 同时清掉 drug_indication 模块的 cache
    from javert.tools import drug_indication as di
    di._drug_map_cache = None
    return cfg_path


def test_search_notes_directory_mode(stub_loader, drug_map_tmp):
    executor = build_executor(stub_loader)
    out = executor.execute({"name": "search_notes", "arguments": {"patient_id": "J66252"}})[0]
    assert "文书目录" in out
    assert "入院诊断" in out


def test_search_notes_keyword(stub_loader, drug_map_tmp):
    executor = build_executor(stub_loader)
    out = executor.execute({"name": "search_notes", "arguments": {"patient_id": "J66252", "keyword": "甲状腺"}})[0]
    assert "甲状腺" in out
    assert "搜索结果" in out


def test_search_fees_directory(stub_loader, drug_map_tmp):
    executor = build_executor(stub_loader)
    out = executor.execute({"name": "search_fees", "arguments": {"patient_id": "J66252"}})[0]
    assert "费用分类目录" in out


def test_note_diagnosis(stub_loader, drug_map_tmp):
    executor = build_executor(stub_loader)
    out = executor.execute({"name": "note_diagnosis", "arguments": {"patient_id": "J66252"}})[0]
    assert "甲状腺乳头状癌" in out


def test_drug_indication_local_hit(stub_loader, drug_map_tmp):
    executor = build_executor(stub_loader)
    out = executor.execute({"name": "drug_indication", "arguments": {"drug_name": "胸腺肽注射液"}})[0]
    assert "胸腺肽" in out
    assert "适应症" in out


def test_drug_indication_miss(stub_loader, drug_map_tmp):
    executor = build_executor(stub_loader)
    out = executor.execute({"name": "drug_indication", "arguments": {"drug_name": "不存在的药品xyz"}})[0]
    assert "未在本地映射表" in out


def test_unknown_patient_returns_empty_message(stub_loader, drug_map_tmp):
    executor = build_executor(stub_loader)
    out = executor.execute({"name": "search_notes", "arguments": {"patient_id": "Z99999"}})[0]
    assert "无文书记录" in out


def test_executor_lists_tools(stub_loader, drug_map_tmp):
    executor = build_executor(stub_loader)
    assert set(executor.list_tools()) == {
        "search_notes",
        "search_fees",
        "note_diagnosis",
        "drug_indication",
        "drug_audit_lookup",  # v0.8 药品违规审计 (与 drug_indication 并存)
        "search_examinations",
        "search_lab_results",
        "search_anesthesia",  # add-visual-schema-onboarding view 工具
        "search_pathology",   # add-visual-schema-onboarding view 工具
        "scan_progress_indications",  # add-verdict-gate-layer 病程指征扫描
    }


# ==== harden-agent-loop: ToolExecutor 韧性原语 ====

def test_parse_errors_surfaces_malformed_tool_call():
    """parse_errors 暴露 <tool_call> 标签内 JSON 解析错误 (parse_tool_calls 静默丢弃)."""
    from javert.tools.tool_executor import ToolExecutor
    ex = ToolExecutor()
    # 缺右括号 → json.loads 失败
    errs = ex.parse_errors('<tool_call>{"name": "x", "arguments": {}</tool_call>')
    assert len(errs) == 1
    # 合法 tool_call → 无错误
    assert ex.parse_errors('<tool_call>{"name": "x", "arguments": {}}</tool_call>') == []
    # 无 tool_call 标签 → 无错误
    assert ex.parse_errors("纯文本没有工具调用") == []


def test_is_error_result_classifies_execute_output():
    """is_error_result 认得 execute() 生成的两种错误串, 不误判真实输出."""
    from javert.tools.tool_executor import ToolExecutor
    ex = ToolExecutor()
    # 未知工具错误
    unknown, _ = ex.execute({"name": "no_such_tool", "arguments": {}})
    assert ToolExecutor.is_error_result(unknown) is True
    # 抛异常的工具
    ex.register("boom", lambda **kw: (_ for _ in ()).throw(RuntimeError("炸了")))
    fail, _ = ex.execute({"name": "boom", "arguments": {}})
    assert ToolExecutor.is_error_result(fail) is True
    # 真实工具输出不被误判
    ex.register("ok", lambda **kw: "正常的检索结果")
    good, _ = ex.execute({"name": "ok", "arguments": {}})
    assert ToolExecutor.is_error_result(good) is False
