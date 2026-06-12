# -*- coding: utf-8 -*-
"""Runner 测试 — mock LLM 模拟成功 / max_tool_calls / 格式错误 / 连接失败."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from javert.audit.result import AuditResult
from javert.audit.rule import Rule
from javert.audit.runner import Runner
from javert.config import JavertConfig, reset_config_cache
from javert.data.loader import DataLoader
from javert.tools.llm_provider import LlmUnavailableError
from javert.tools.tool_executor import ToolExecutor


class _StubLoader(DataLoader):
    def __init__(self):
        self._notes = pd.DataFrame({
            "住院号": ["J66252"],
            "事件时间": ["2026-01-01"],
            "阶段": ["入院"],
            "子阶段": ["入院诊断"],
            "内容": ["甲状腺乳头状癌"],
            "来源文件": ["test"],
        })
        self._fees = pd.DataFrame({
            "bah": ["H31010600042-J66252 "],
            "fee_ocur_time": ["2026-01-01"],
            "cnt": [1.0],
            "pric": [80.0],
            "det_item_fee_sumamt": [80.0],
            "medins_list_name": ["脑功能成像"],
        })

    def all_notes(self): return self._notes
    def all_fees(self): return self._fees
    def get_notes(self, pid): return self._notes
    def get_fees(self, pid): return self._fees


class FakeProvider:
    """脚本化的 LLM provider — 按预设序列返回 content."""

    def __init__(self, scripted_contents: list[str], raise_on_call: int | None = None):
        self.contents = list(scripted_contents)
        self.calls = 0
        self.raise_on_call = raise_on_call
        self.model_name = "fake-qwen"

    def chat_with_retry(self, messages, **kwargs):
        self.calls += 1
        if self.raise_on_call is not None and self.calls == self.raise_on_call:
            raise LlmUnavailableError("simulated outage")
        if not self.contents:
            return {"content": "", "reasoning_content": "", "usage": None, "raw_response": {}}
        return {
            "content": self.contents.pop(0),
            "reasoning_content": "",
            "usage": None,
            "raw_response": {},
        }


@pytest.fixture
def base_prompt(tmp_path: Path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "base.txt").write_text("you are auditor.", encoding="utf-8")
    return prompts


@pytest.fixture
def cfg(base_prompt, tmp_path: Path, monkeypatch):
    reset_config_cache()
    c = JavertConfig(
        prompts_dir=str(base_prompt),
        max_tool_calls=4,
        retry_budget=1,
        data_dir=str(tmp_path / "data"),
        rules_dir=str(tmp_path / "rules"),
        audit_db=str(tmp_path / "audit.sqlite"),
    )
    yield c
    reset_config_cache()


@pytest.fixture
def executor():
    from javert.tools.registry import build_executor
    return build_executor(_StubLoader())


def _make_rule() -> Rule:
    return Rule(
        rule_id="R191",
        domain="肿瘤",
        violation_type="重复收费",
        question="开展肿瘤全身断层显像, 重复收取人工报告费用.",
        example="",
    )


def test_happy_path_with_tool_then_verdict(cfg, executor):
    contents = [
        # 第 1 轮: 调用工具
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        # 第 2 轮: 输出 verdict
        '```json\n{"verdict": "VIOLATION", "confidence": 0.9, "evidence": [{"source":"note","locator":"入院诊断","text":"甲状腺乳头状癌"}], "reasoning": "ok"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "VIOLATION"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "search_notes"
    assert result.confidence == pytest.approx(0.9)


def test_max_tool_calls_returns_inconclusive(cfg, executor):
    # 全部回合都返回 tool_call, 永远不 verdict; deadline turn 也返回 tool_call → malformed
    contents = ['<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>'] * 10
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "INCONCLUSIVE"
    # 新文案 (deadline retry 引入): "tool budget exhausted (deadline verdict malformed)"
    assert "exhausted" in result.reasoning.lower()


def test_deadline_retry_accepts_verdict_on_budget_exhaust(cfg, executor):
    """tc 触顶后, deadline turn 出有效 verdict → 采纳, 不走 conf=0.0 兜底."""
    # max_tool_calls=4 (cfg fixture); 前 4 轮全 tool_call, 第 5 (deadline) 轮出 JSON
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252", "keyword": "甲状腺"}}</tool_call>',
        '<tool_call>{"name": "search_fees", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '<tool_call>{"name": "note_diagnosis", "arguments": {"patient_id": "J66252"}}</tool_call>',
        # deadline turn: LLM 收到 "请立即输出 verdict" 后给出
        '```json\n{"verdict": "INCONCLUSIVE", "confidence": 0.55, '
        '"evidence": [{"source":"note","locator":"诊断","text":"甲状腺癌"}], '
        '"reasoning": "工具已用尽, 信息不足判 V/C"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "INCONCLUSIVE"
    assert result.confidence == pytest.approx(0.55)  # 不是 0.0 兜底
    assert "deadline" in result.reasoning.lower() or len(result.evidence) > 0
    assert len(result.evidence) == 1
    assert len(result.tool_calls) == 4  # 触顶时已记录的 tc


def test_deadline_retry_accepts_bare_json(cfg, executor):
    """deadline turn 直接吐裸 JSON (无 ```json 围栏) 也应被采纳, 不丢成 conf=0.0.

    回归: 旧 _parse_verdict_block 只认 fenced 块, deadline turn 常输出裸 {...} →
    合法 verdict (conf 0.55) 被当 malformed 丢弃成 conf=0.00.
    """
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '<tool_call>{"name": "search_fees", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '<tool_call>{"name": "note_diagnosis", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252", "keyword": "甲状腺"}}</tool_call>',
        # deadline turn: 裸 JSON, 无围栏
        '{"verdict": "CLEAN", "confidence": 0.85, '
        '"evidence": [{"source":"drug_audit_lookup","locator":"甲状腺片","text":"甲状腺癌术后替代治疗"}], '
        '"reasoning": "诊断落在依据内, 对症"}',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "CLEAN"
    assert result.confidence == pytest.approx(0.85)
    assert len(result.evidence) == 1


def test_deadline_retry_malformed_falls_back(cfg, executor):
    """deadline turn 仍输出 malformed JSON → 走旧兜底 conf=0.0 + 空 evidence."""
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '<tool_call>{"name": "search_fees", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '<tool_call>{"name": "note_diagnosis", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252", "section": "诊断"}}</tool_call>',
        # deadline turn: 仍不出有效 JSON
        '抱歉, 还需要更多工具.',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "INCONCLUSIVE"
    assert result.confidence == 0.0
    assert "malformed" in result.reasoning.lower()
    assert len(result.evidence) == 0


def test_deadline_retry_llm_unavailable(cfg, executor):
    """deadline turn 调用 LLM 时 outage → reasoning 标 unavailable, 走兜底."""
    contents = ['<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>'] * 4
    # 第 5 次 LLM call (deadline turn) 时 raise
    runner = Runner(
        executor=executor,
        provider=FakeProvider(contents, raise_on_call=5),
        config=cfg,
    )
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "INCONCLUSIVE"
    assert result.confidence == 0.0
    assert "unavailable" in result.reasoning.lower()


def test_deadline_skipped_when_no_tool_called(cfg, executor):
    """tc 触顶但 tool_records 为空 (理论上极少, 因为 base prompt 强制 ≥1 tool) → 不发 deadline.

    模拟: 每轮 LLM 输出非 JSON 非 tool_call 文本, 触发 repair, repair 也失败.
    实际上 repair 失败会 break (不走 for-else). 这条用 for-else 路径需要构造一种特别情形:
    每轮输出 tool_call 但工具 fail 不计 records. 这太人造了, 直接跳过这个 case.
    """
    pytest.skip("for-else 分支 + tool_records 为空 在当前 runner 结构下不可达")


def test_malformed_json_repaired(cfg, executor):
    contents = [
        # 1 轮: 工具
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        # 2 轮: 烂 JSON
        '```json\n{verdict: BROKEN}\n```',
        # 3 轮 (repair): 修好的 JSON
        '```json\n{"verdict": "CLEAN", "confidence": 0.6, "evidence": [], "reasoning": "no evidence"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "CLEAN"


def test_repair_also_fails_inconclusive(cfg, executor):
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '不是 JSON 也不是 tool_call',
        '还是不是 JSON',  # repair turn 也失败
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "INCONCLUSIVE"
    assert "malformed" in result.reasoning.lower()


def test_llm_unavailable_propagates(cfg, executor):
    runner = Runner(
        executor=executor,
        provider=FakeProvider(["x"] * 5, raise_on_call=1),
        config=cfg,
    )
    with pytest.raises(LlmUnavailableError):
        runner.audit(_make_rule(), "J66252")


def test_reset_cache_false_shares_across_audits(cfg, executor):
    """同 executor 跨两次 audit, 第二次 reset_cache=False 时同参数 tool 命中 cached."""
    contents_a = [
        '<tool_call>{"name": "note_diagnosis", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"诊断","text":"癌"}], "reasoning": "ok"}\n```',
    ]
    contents_b = [
        '<tool_call>{"name": "note_diagnosis", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"诊断","text":"癌"}], "reasoning": "ok"}\n```',
    ]
    provider = FakeProvider(contents_a + contents_b)
    runner = Runner(executor=executor, provider=provider, config=cfg)
    r1 = runner.audit(_make_rule(), "J66252", reset_cache=True)
    r2 = runner.audit(_make_rule(), "J66252", reset_cache=False)
    assert r1.tool_calls[0].cached is False  # 首次冷
    assert r2.tool_calls[0].cached is True   # 第二次命中


def test_reset_cache_true_default_invalidates(cfg, executor):
    """默认 reset_cache=True: 每次 audit 开头清缓存, 跨 audit 同参数也未命中."""
    contents_a = [
        '<tool_call>{"name": "note_diagnosis", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"诊断","text":"癌"}], "reasoning": "ok"}\n```',
    ]
    contents_b = list(contents_a)
    provider = FakeProvider(contents_a + contents_b)
    runner = Runner(executor=executor, provider=provider, config=cfg)
    r1 = runner.audit(_make_rule(), "J66252")  # default reset_cache=True
    r2 = runner.audit(_make_rule(), "J66252")  # default reset_cache=True
    assert r1.tool_calls[0].cached is False
    assert r2.tool_calls[0].cached is False


def test_audit_manage_context_true_sets_and_clears(cfg, executor):
    """默认 manage_patient_context=True: audit 后 executor._patient_context == None."""
    contents = [
        '<tool_call>{"name": "note_diagnosis", "arguments": {}}</tool_call>',
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"x","text":"y"}], "reasoning": "ok"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    assert executor._patient_context is None
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "CLEAN"
    assert executor._patient_context is None  # 已 clear


def test_audit_manage_context_false_preserves_pre_set_value(cfg, executor):
    """manage_patient_context=False: audit 不动 executor 的 patient_context."""
    executor.set_patient_context("PRE_SET_VALUE")
    contents = [
        '<tool_call>{"name": "note_diagnosis", "arguments": {"patient_id": "OTHER"}}</tool_call>',
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"x","text":"y"}], "reasoning": "ok"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    runner.audit(_make_rule(), "OTHER", manage_patient_context=False)
    assert executor._patient_context == "PRE_SET_VALUE"  # 不动


def test_audit_finally_clears_on_exception(cfg, executor):
    """LlmUnavailableError 路径 finally 仍 clear context."""
    runner = Runner(
        executor=executor,
        provider=FakeProvider([], raise_on_call=1),
        config=cfg,
    )
    assert executor._patient_context is None
    with pytest.raises(LlmUnavailableError):
        runner.audit(_make_rule(), "J66252")
    assert executor._patient_context is None  # finally 已清


def test_audit_injects_patient_id_when_llm_omits_it(cfg, executor):
    """LLM 没传 patient_id 时, ToolExecutor 应自动注入, 工具不会 TypeError."""
    contents = [
        # LLM 故意不传 patient_id
        '<tool_call>{"name": "note_diagnosis", "arguments": {}}</tool_call>',
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"x","text":"y"}], "reasoning": "ok"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    # 没崩 + 拿到 verdict 就证明注入生效 (旧版会让 note_diagnosis raise TypeError)
    assert result.verdict == "CLEAN"
    assert len(result.tool_calls) == 1
    # 工具记录里 arguments 是 LLM 原始传的 {} (cache key 算的是注入后的)
    assert result.tool_calls[0].arguments == {}


def _m2_rule() -> Rule:
    return Rule(
        rule_id="R141",
        domain="临床检验",
        violation_type="过度检查",
        question="过度检查",
        derived_from_template="M2",
        prompt_addon='检索关键词: "脑功能成像"',
    )


def test_gate_single_instance_downgrades_violation_to_clean(cfg, executor):
    """M2 派生 + 净次数 1 (stub fee 单日 1 次) → gate ② 降 V→C, 写 gate_tag."""
    contents = [
        '<tool_call>{"name": "search_fees", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "VIOLATION", "confidence": 0.95, "evidence": [{"source":"fee","locator":"脑功能成像","text":"1 次"}], "reasoning": "无指征"}\n```',
    ]
    runner = Runner(
        executor=executor, provider=FakeProvider(contents), config=cfg, loader=_StubLoader()
    )
    result = runner.audit(_m2_rule(), "J66252")
    assert result.verdict == "CLEAN"
    assert result.gate_tag == "单次放过"
    assert "gate:" in result.reasoning


def test_gate_off_passthrough(cfg, executor):
    """JAVERT_VERDICT_GATE=off → 低置信 V 不被降级 (gate 直通)."""
    cfg.verdict_gate = "off"
    contents = [
        '<tool_call>{"name": "search_fees", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "VIOLATION", "confidence": 0.80, "evidence": [{"source":"fee","locator":"x","text":"y"}], "reasoning": "ok"}\n```',
    ]
    runner = Runner(
        executor=executor, provider=FakeProvider(contents), config=cfg, loader=_StubLoader()
    )
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "VIOLATION"
    assert result.gate_tag == ""


def test_gate_conf_floor_downgrades_via_runner(cfg, executor):
    """gate ③: conf 0.80 的 V → I + 低置信降级 标签."""
    contents = [
        '<tool_call>{"name": "search_fees", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "VIOLATION", "confidence": 0.80, "evidence": [{"source":"fee","locator":"x","text":"y"}], "reasoning": "ok"}\n```',
    ]
    runner = Runner(
        executor=executor, provider=FakeProvider(contents), config=cfg, loader=_StubLoader()
    )
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "INCONCLUSIVE"
    assert result.gate_tag == "低置信降级"


def test_rejects_verdict_without_any_tool_call(cfg, executor):
    """没调用任何工具就给 verdict 应被拒绝, 强制 repair."""
    contents = [
        # 1 轮: 直接给 verdict (没工具)
        '```json\n{"verdict": "CLEAN", "confidence": 0.9, "evidence": [], "reasoning": "ok"}\n```',
        # 2 轮: 工具
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        # 3 轮: 修正后的 verdict
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"入院诊断","text":"x"}], "reasoning": "checked"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "CLEAN"
    assert len(result.tool_calls) == 1
