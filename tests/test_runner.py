# -*- coding: utf-8 -*-
"""Runner 测试 — mock LLM 模拟成功 / max_tool_calls / 格式错误 / 连接失败."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from javert.audit.result import AuditResult, TOOL_FAILURE_GATE_TAG
from javert.audit.rule import PrecheckSpec, Rule
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

    def __init__(
        self,
        scripted_contents: list[str],
        raise_on_call: int | None = None,
        finish_reasons: list[str | None] | None = None,
    ):
        self.contents = list(scripted_contents)
        self.calls = 0
        self.raise_on_call = raise_on_call
        self.finish_reasons = list(finish_reasons or [])
        self.model_name = "fake-qwen"
        self.seen_user_msgs: list[str] = []  # 每次调用时最后一条 user 消息内容
        self.seen_messages: list[list[dict[str, str]]] = []
        self.seen_kwargs: list[dict[str, Any]] = []

    def chat_with_retry(self, messages, **kwargs):
        self.calls += 1
        if messages:
            self.seen_user_msgs.append(messages[-1].get("content", ""))
        self.seen_messages.append([dict(message) for message in messages])
        self.seen_kwargs.append(dict(kwargs))
        if self.raise_on_call is not None and self.calls == self.raise_on_call:
            raise LlmUnavailableError("simulated outage")
        finish_reason = self.finish_reasons.pop(0) if self.finish_reasons else None
        if not self.contents:
            return {
                "content": "", "reasoning_content": "", "usage": None,
                "finish_reason": finish_reason, "raw_response": {},
            }
        return {
            "content": self.contents.pop(0),
            "reasoning_content": "",
            "usage": None,
            "finish_reason": finish_reason,
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


def _make_drug_rule() -> Rule:
    return Rule(
        rule_id="RD04",
        domain="药品",
        violation_type="超医保限定支付适应症用药",
        question="肿瘤药超医保限定支付.",
        example="",
        drug_rule_type="限适应症",
    )


def test_drug_violation_appends_data_blindspot_notice(cfg, executor):
    """药品类 V 确定性追加病理/自费提醒 (不依赖 LLM 记性)."""
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "VIOLATION", "confidence": 0.9, '
        '"evidence": [{"source":"note","locator":"诊断","text":"鼻咽癌"}], '
        '"reasoning": "诊断不在限定内"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_drug_rule(), "J66252")
    assert result.verdict == "VIOLATION"
    assert "建议复查病理文书及该项目是否自费" in result.reasoning


def test_drug_violation_notice_not_duplicated_when_llm_already_added(cfg, executor):
    """LLM 已自带提醒 → 不重复追加."""
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "VIOLATION", "confidence": 0.9, '
        '"evidence": [{"source":"note","locator":"诊断","text":"鼻咽癌"}], '
        '"reasoning": "诊断不在限定内. 建议复查病理文书及该项目是否自费后再定性."}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_drug_rule(), "J66252")
    assert result.reasoning.count("建议复查病理") == 1


def test_non_drug_violation_no_notice(cfg, executor):
    """非药品规则 V 不追加提醒."""
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "VIOLATION", "confidence": 0.9, '
        '"evidence": [{"source":"note","locator":"诊断","text":"x"}], '
        '"reasoning": "重复收费"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert "建议复查病理" not in result.reasoning


def test_gate_downgrade_normalizes_confidence(cfg, executor):
    """fix-scan-residuals: gate 降级 (V→I) 时 conf 归一 0.5, 原值进 [gate: ...] 注记.

    conf=0.80 的 V 触发 ③低置信闸 (< 0.85 ceiling) → INCONCLUSIVE.
    """
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "VIOLATION", "confidence": 0.80, '
        '"evidence": [{"source":"note","locator":"入院诊断","text":"甲状腺乳头状癌"}], '
        '"reasoning": "初判违规"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "INCONCLUSIVE"          # 被 gate 降级
    assert result.confidence == pytest.approx(0.5)   # 归一, 不再是 0.80
    assert "[gate:" in result.reasoning
    assert "原 conf=0.80" in result.reasoning        # 原值留档
    assert "初判违规" in result.reasoning            # 原 reasoning 保留


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


def test_length_truncated_tool_call_uses_bounded_repair_without_echo(cfg, executor):
    """length 截断的长正文不回灌；短 repair 可返回工具并继续主循环。"""
    truncated = '<tool_call>{"name":"search_notes","arguments":{"keyword":"' + "x" * 5000
    provider = FakeProvider(
        [
            truncated,
            '<tool_call>{"name":"search_notes","arguments":{"keyword":"甲状腺"}}</tool_call>',
            '```json\n{"verdict":"CLEAN","confidence":0.9,"evidence":[],"reasoning":"已核实"}\n```',
        ],
        finish_reasons=["length", "stop", "stop"],
    )
    result = Runner(executor=executor, provider=provider, config=cfg).audit(
        _make_rule(), "J66252"
    )

    assert result.verdict == "CLEAN"
    assert len(result.tool_calls) == 1
    assert provider.seen_kwargs[1]["max_tokens"] <= 512
    assert all(
        truncated not in message["content"]
        for message in provider.seen_messages[1]
    )
    assert "只输出一个" in provider.seen_user_msgs[1]


def test_length_truncated_verdict_after_tool_uses_short_json_repair(cfg, executor):
    """已有成功工具时，length repair 只要求短 verdict JSON。"""
    truncated = '```json\n{"verdict":"CLEAN","reasoning":"' + "x" * 5000
    provider = FakeProvider(
        [
            '<tool_call>{"name":"search_notes","arguments":{"keyword":"甲状腺"}}</tool_call>',
            truncated,
            '```json\n{"verdict":"CLEAN","confidence":0.9,"evidence":[],"reasoning":"已核实"}\n```',
        ],
        finish_reasons=["stop", "length", "stop"],
    )
    result = Runner(executor=executor, provider=provider, config=cfg).audit(
        _make_rule(), "J66252"
    )

    assert result.verdict == "CLEAN"
    assert provider.seen_kwargs[2]["max_tokens"] <= 512
    assert all(
        truncated not in message["content"]
        for message in provider.seen_messages[2]
    )
    assert "只输出" in provider.seen_user_msgs[2]
    assert "JSON" in provider.seen_user_msgs[2]


def test_length_repair_failure_is_truncated_inconclusive(cfg, executor):
    """有界恢复仍被截断时安全落 I，并留下可区分的内部 reason。"""
    provider = FakeProvider(
        ["<tool_call>{", "<tool_call>{"],
        finish_reasons=["length", "length"],
    )
    result = Runner(executor=executor, provider=provider, config=cfg).audit(
        _make_rule(), "J66252"
    )

    assert result.verdict == "INCONCLUSIVE"
    assert result.confidence == 0.0
    assert "truncated" in result.reasoning.lower()
    assert result.tool_calls == []
    assert provider.calls == 2


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


def test_tool_failure_cannot_be_published_as_suspicion(cfg):
    """技术异常不能成为患者疑似判定或公开证据。"""
    broken = ToolExecutor()

    def fail_fees(**_kwargs):
        raise TypeError("argument of type 'float' is not iterable")

    broken.register("search_fees", fail_fees, requires_patient_id=True)
    broken.register(
        "search_notes",
        lambda **_kwargs: "病历文书中未检索到相关执行记录",
        requires_patient_id=True,
    )
    contents = [
        (
            '<tool_call>{"name":"search_fees","arguments":{}}</tool_call>'
            '<tool_call>{"name":"search_notes","arguments":{}}</tool_call>'
        ),
        (
            '```json\n{"verdict":"INCONCLUSIVE","confidence":0.5,'
            '"evidence":[{"source":"etl_warning","locator":"费用明细",'
            '"text":"search_fees 执行失败: argument of type float is not iterable"}],'
            '"reasoning":"费用明细工具调用错误，无法确认"}\n```'
        ),
    ]
    runner = Runner(
        executor=broken,
        provider=FakeProvider(contents),
        config=cfg,
        loader=_StubLoader(),
    )

    result = runner.audit(_make_rule(), "J66252")

    assert result.verdict == "CLEAN"
    assert result.gate_tag == TOOL_FAILURE_GATE_TAG
    assert result.evidence == []
    assert "执行失败" not in result.reasoning
    assert "工具调用错误" not in result.reasoning


# ==== harden-agent-loop ====

def test_repair_tool_call_continues_not_inconclusive(cfg, executor):
    """change 1: repair 响应含 tool_call 时应执行并续跑, 不再丢弃直接 INCONCLUSIVE."""
    contents = [
        # 1 轮: 既非 tool_call 也非 verdict → 触发 repair
        "让我想想该查什么...",
        # repair 响应: 一个合法 tool_call (旧版会丢弃 → INCONCLUSIVE conf 0)
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        # 2 轮 (续跑): 出 verdict
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"入院诊断","text":"x"}], "reasoning": "checked"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "CLEAN"          # 不是丢成 INCONCLUSIVE conf 0
    assert result.confidence == pytest.approx(0.7)
    assert len(result.tool_calls) == 1        # repair 里的 tool_call 被执行并记录


def test_malformed_tool_call_gets_targeted_feedback(cfg, executor):
    """change 3: 畸形 tool_call JSON → 回传具体解析错误 (而非泛化 repair 提示)."""
    provider = FakeProvider([
        # 1 轮: 缺右括号的 tool_call (json.loads 失败)
        '<tool_call>{"name": "search_notes", "arguments": {}</tool_call>',
        # repair 响应: 合法 tool_call → 续跑
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        # 2 轮: verdict
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"入院诊断","text":"x"}], "reasoning": "ok"}\n```',
    ])
    emitted: list[str] = []
    runner = Runner(executor=executor, provider=provider, config=cfg, emit=emitted.append)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "CLEAN"
    # emit 里出现「畸形」诊断
    assert any("畸形" in m for m in emitted)
    # 模型收到的 repair 提示里含 tool_call JSON 非法字样 (第 2 次 LLM 调用前的 user 消息)
    assert any("tool_call JSON 非法" in m for m in provider.seen_user_msgs)


def test_all_tool_calls_failed_does_not_unlock_verdict(cfg, executor):
    """工具全部失败时不解锁裁决，也不把系统故障发布成疑似。"""
    contents = [
        # 1 轮: 未知工具 → 执行失败 (不计成功)
        '<tool_call>{"name": "no_such_tool", "arguments": {}}</tool_call>',
        # 2 轮: 想直接判 VIOLATION → 应被拒绝 (n_success==0)
        '```json\n{"verdict": "VIOLATION", "confidence": 0.95, "evidence": [], "reasoning": "凭空判违规"}\n```',
        # 3 轮: 再次未知工具失败
        '<tool_call>{"name": "no_such_tool", "arguments": {}}</tool_call>',
        # 4 轮: 又想判 V → 再拒绝, budget 耗尽走 deadline (无更多 content → malformed)
        '```json\n{"verdict": "VIOLATION", "confidence": 0.9, "evidence": [], "reasoning": "还是判违规"}\n```',
    ]
    emitted: list[str] = []
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg, emit=emitted.append)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict != "VIOLATION"      # 全失败的 V 不被接受
    assert result.verdict == "CLEAN"
    assert result.gate_tag == TOOL_FAILURE_GATE_TAG
    assert any("无成功 tool_call" in m for m in emitted)


def test_one_successful_tool_call_unlocks_verdict(cfg, executor):
    """change 4: 只要有 1 次成功 tool_call, 后续 verdict 即可裁决."""
    contents = [
        # 1 轮: 未知工具失败
        '<tool_call>{"name": "no_such_tool", "arguments": {}}</tool_call>',
        # 2 轮: 成功工具
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        # 3 轮: verdict → 应被接受 (n_success==1)
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"入院诊断","text":"x"}], "reasoning": "查过了"}\n```',
    ]
    runner = Runner(executor=executor, provider=FakeProvider(contents), config=cfg)
    result = runner.audit(_make_rule(), "J66252")
    assert result.verdict == "CLEAN"
    assert len(result.tool_calls) == 2        # 1 失败 + 1 成功都记录


def test_parse_verdict_bare_json_with_braces_in_reasoning():
    """change 2: reasoning 含花括号时括号平衡扫描仍能取到末尾合法 verdict 块."""
    from javert.audit.runner import _parse_verdict_block
    text = '思考{中间有}花括号\n{"verdict": "CLEAN", "confidence": 0.8, "reasoning": "r"}'
    data = _parse_verdict_block(text)
    assert data is not None
    assert data["verdict"] == "CLEAN"
    assert data["confidence"] == 0.8


def test_parse_verdict_multi_candidate_takes_last_valid():
    """change 2: 多个平衡对象, 从后往前取首个含合法 verdict 字段的块."""
    from javert.audit.runner import _parse_verdict_block
    text = (
        '{"verdict": "VIOLATION", "confidence": 0.9}\n'
        '一些解释 {"noise": 1}\n'
        '{"verdict": "CLEAN", "confidence": 0.6, "reasoning": "final"}'
    )
    data = _parse_verdict_block(text)
    assert data is not None
    assert data["verdict"] == "CLEAN"   # 取末尾那个


def test_parse_verdict_no_valid_returns_none():
    """change 2: 所有平衡对象都无合法 verdict 字段 → None."""
    from javert.audit.runner import _parse_verdict_block
    assert _parse_verdict_block("纯文本 {没有} 合法 {json:1} verdict") is None


# ========== 确定性预检 (pilot-deterministic-precheck) ==========
def _precheck_rule(a_items, b_items) -> Rule:
    return Rule(
        rule_id="R191", domain="肿瘤", violation_type="重复收费",
        question="q", derived_from_template="M1",
        precheck=PrecheckSpec(a_items=a_items, b_items=b_items),
    )


class _ABLoader(_StubLoader):
    """费用含 A(全身断层) + B(图文报告) 两项, 供 precheck facts 路径."""

    def __init__(self):
        super().__init__()
        self._fees = pd.DataFrame({
            "bah": ["H-J66252 ", "H-J66252 "],
            "fee_ocur_time": ["2026-01-01", "2026-01-01"],
            "cnt": [1.0, 1.0],
            "det_item_fee_sumamt": [3000.0, 50.0],
            "medins_list_name": ["PET-CT全身断层显像", "PET-CT图文报告费"],
        })


def test_precheck_clean_short_circuits_no_llm(cfg, executor):
    """A 项不在费用里 → precheck 短路 CLEAN, provider 0 次调用."""
    provider = FakeProvider([])  # 若被误调返回空 → 便于发现
    runner = Runner(executor=executor, provider=provider, config=cfg, loader=_StubLoader())
    result = runner.audit(_precheck_rule(a_items=["全身断层"], b_items=["图文报告"]), "J66252")
    assert result.verdict == "CLEAN"
    assert result.precheck_tag == "无A项"
    assert provider.calls == 0          # 关键: 没调 LLM
    assert result.tool_calls == []


def test_precheck_facts_injects_and_merges_evidence(cfg, executor):
    """A∩B 并存 → 注入事实块, LLM 判 V → 合并 precheck 费用锚点."""
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "VIOLATION", "confidence": 0.9, "evidence": [{"source":"note","locator":"手术记录","text":"无分次说明"}], "reasoning": "无反证"}\n```',
    ]
    provider = FakeProvider(contents)
    runner = Runner(executor=executor, provider=provider, config=cfg, loader=_ABLoader())
    result = runner.audit(_precheck_rule(a_items=["全身断层"], b_items=["图文报告"]), "J66252")
    assert result.verdict == "VIOLATION"
    assert result.precheck_tag == "A∩B并存待核反证"
    assert any("系统预检费用事实" in m for m in provider.seen_user_msgs)
    fee_locs = {e.locator for e in result.evidence if "fee" in e.source}
    assert "PET-CT全身断层显像" in fee_locs and "PET-CT图文报告费" in fee_locs


def test_precheck_off_runs_normal_path(cfg, executor):
    """JAVERT_PRECHECK=off → 带 precheck 的 M1 也走原 LLM 路径 (不短路)."""
    cfg.precheck = "off"
    contents = [
        '<tool_call>{"name": "search_notes", "arguments": {"patient_id": "J66252"}}</tool_call>',
        '```json\n{"verdict": "CLEAN", "confidence": 0.7, "evidence": [{"source":"note","locator":"x","text":"y"}], "reasoning": "ok"}\n```',
    ]
    provider = FakeProvider(contents)
    runner = Runner(executor=executor, provider=provider, config=cfg, loader=_StubLoader())
    result = runner.audit(_precheck_rule(a_items=["全身断层"], b_items=["图文报告"]), "J66252")
    assert provider.calls >= 1          # 走了 LLM
    assert result.precheck_tag == ""
