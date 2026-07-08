# -*- coding: utf-8 -*-
"""audit-patient --concurrency N 集成测试.

依赖: fix-tool-patient-id-default (manage_patient_context kwarg) + ToolExecutor lock.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pandas as pd
import pytest
from click.testing import CliRunner

from javert.audit.rule import Rule
from javert.audit.rule_writer import write_rule
from javert.cli import main
from javert.tools.llm_provider import LlmUnavailableError


PT_ID = "JT001"
NOTES_COLS = ["住院号", "事件时间", "阶段", "子阶段", "内容", "来源文件"]
FEES_COLS = ["bah", "fee_ocur_time", "cnt", "pric", "det_item_fee_sumamt", "medins_list_name"]


def _make_rule(rid: str, priority: str = "P0", status: str = "drafting") -> Rule:
    return Rule(
        rule_id=rid,
        domain="测试",
        violation_type="重复收费",
        question=f"测试问题 {rid}",
        example="",
        status=status,
        priority=priority,
    )


@pytest.fixture
def audit_project(tmp_path: Path, monkeypatch):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = tmp_path / "audit.sqlite"

    # 5 条 P0 rule
    for rid in ["R001", "R002", "R003", "R004", "R005"]:
        write_rule(_make_rule(rid, "P0"), rules_dir / f"{rid}.yaml")

    notes = pd.DataFrame([
        {"住院号": PT_ID, "事件时间": "2026-01-01", "阶段": "入院",
         "子阶段": "入院诊断", "内容": "测试诊断", "来源文件": "test"},
    ], columns=NOTES_COLS)
    notes.to_csv(data_dir / "case_notes.csv", index=False)

    fees = pd.DataFrame([
        {"bah": f"H0000-{PT_ID} ", "fee_ocur_time": "2026-01-01",
         "cnt": 1.0, "pric": 100.0, "det_item_fee_sumamt": 100.0,
         "medins_list_name": "测试费用项"},
    ], columns=FEES_COLS)
    fees.to_csv(data_dir / "shi_fee.csv", index=False)

    monkeypatch.setenv("JAVERT_RULES_DIR", str(rules_dir))
    monkeypatch.setenv("JAVERT_AUDIT_DB", str(db_path))
    monkeypatch.setenv("JAVERT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "false")
    monkeypatch.setenv("JAVERT_RETRY_BUDGET", "1")

    from javert.config import reset_config_cache
    reset_config_cache()
    yield {"root": tmp_path, "db": db_path}
    reset_config_cache()


class _ThreadSafeScriptedProvider:
    """脚本化 Provider, 按 rule_id 路由不同 verdict (并发安全).

    rule_id → list[content] 映射. chat 内根据 messages 里出现的 rule_id 选脚本.
    """

    def __init__(self, scripts_by_rule: dict[str, list[str]], raise_for: set[str] | None = None):
        self._scripts = scripts_by_rule
        self._raise_for = raise_for or set()
        self._lock = threading.Lock()
        self._call_counts: dict[str, int] = {r: 0 for r in scripts_by_rule}
        self.model_name = "fake-qwen-concurrent"

    def chat_with_retry(self, messages, **kwargs):
        # 从 messages 里找 rule_id (system prompt 包含 "当前审计规则: Rxxx")
        rule_id = None
        for msg in messages:
            content = msg.get("content", "")
            if "当前审计规则: " in content:
                idx = content.index("当前审计规则: ") + len("当前审计规则: ")
                rule_id = content[idx:idx + 4]
                break
        if rule_id is None:
            raise RuntimeError(f"无法从 messages 推断 rule_id: {messages[:1]}")

        if rule_id in self._raise_for:
            raise LlmUnavailableError(f"simulated outage for {rule_id}")

        with self._lock:
            scripts = self._scripts.get(rule_id, [])
            i = self._call_counts[rule_id]
            self._call_counts[rule_id] = i + 1
        if i >= len(scripts):
            raise RuntimeError(f"FakeProvider 脚本 {rule_id} 用尽 (call #{i + 1})")
        return {
            "content": scripts[i],
            "reasoning_content": "",
            "usage": None,
            "raw_response": {},
        }


def _make_audit_scripts(rule_ids, verdict="CLEAN", tool="note_diagnosis"):
    """给每条 rule_id 一个 2 步脚本: tool_call → verdict."""
    return {
        rid: [
            f'<tool_call>{{"name": "{tool}", "arguments": {{"patient_id": "{PT_ID}"}}}}</tool_call>',
            '```json\n{"verdict": "' + verdict + '", "confidence": 0.9, '
            '"evidence": [{"source":"note","locator":"入院诊断","text":"测试诊断"}], '
            '"reasoning": "ok"}\n```',
        ]
        for rid in rule_ids
    }


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr(
        "javert.audit.runner.Qwen35Provider",
        lambda *args, **kwargs: provider,
    )
    return provider


# ============================================================
# 测试场景
# ============================================================


def test_concurrency_3_runs_all_5_rules(audit_project, monkeypatch):
    """concurrency=3, 5 条 mock rules, 全部产 result."""
    scripts = _make_audit_scripts(["R001", "R002", "R003", "R004", "R005"])
    _patch_provider(monkeypatch, _ThreadSafeScriptedProvider(scripts))

    runner = CliRunner()
    result = runner.invoke(main, ["audit-patient", PT_ID, "--concurrency", "3"])
    assert result.exit_code == 0, f"stderr=\n{result.stderr}\nstdout=\n{result.stdout}"

    # 所有 5 条都出现在进度里 (顺序不定)
    for rid in ["R001", "R002", "R003", "R004", "R005"]:
        assert rid in result.stderr, f"{rid} 不在 stderr"

    # summary
    assert "Verdicts: V=0 / C=5 / I=0" in result.stdout
    assert "5 completed" in result.stdout
    assert "concurrency: 3" in result.stdout

    # DB 应有 5 行
    conn = sqlite3.connect(audit_project["db"])
    n = conn.execute("SELECT COUNT(*) FROM audit_runs WHERE patient_id = ?", (PT_ID,)).fetchone()[0]
    conn.close()
    assert n == 5


def test_concurrency_preserves_verdict_distribution(audit_project, monkeypatch):
    """并发与串行的 V/C/I 计数应一致 (mock 给定 → verdict 与并发顺序无关)."""
    # 给 R001 VIOLATION, R002-R005 CLEAN
    scripts = _make_audit_scripts(["R002", "R003", "R004", "R005"], verdict="CLEAN")
    scripts.update(_make_audit_scripts(["R001"], verdict="VIOLATION"))

    _patch_provider(monkeypatch, _ThreadSafeScriptedProvider(scripts))
    runner = CliRunner()
    result = runner.invoke(main, ["audit-patient", PT_ID, "--concurrency", "5"])
    assert result.exit_code == 0
    assert "Verdicts: V=1 / C=4 / I=0" in result.stdout


def test_concurrency_with_share_cache_yields_hits(audit_project, monkeypatch):
    """concurrency + share-tool-cache: 同 tool 同参数跨规则应有 cached."""
    # 5 条规则都调 note_diagnosis(patient_id=PT_ID), 同参数 → 至少 4 cached
    scripts = _make_audit_scripts(["R001", "R002", "R003", "R004", "R005"])
    _patch_provider(monkeypatch, _ThreadSafeScriptedProvider(scripts))

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["audit-patient", PT_ID, "--concurrency", "3", "--share-tool-cache"],
    )
    assert result.exit_code == 0

    # Total tool calls = 5; cached >= 4 (并发下首条冷, 其他 hit; 但极端情况下 2 条同时
    # 进 lock 时第二条进入 execute 也只算 1 miss, 因为 lock 是 serialized)
    # 解析 stdout "Tool calls: N total, M cached"
    import re
    m = re.search(r"Tool calls: (\d+) total, (\d+) cached", result.stdout)
    assert m, f"未找到 Tool calls 行: {result.stdout}"
    total, cached = int(m.group(1)), int(m.group(2))
    assert total == 5
    assert cached >= 4, f"cached={cached}, 期望 >=4 (5 条同 args 共享 cache)"


def test_concurrency_llm_fail_isolated(audit_project, monkeypatch):
    """并发模式下: 第 3 条 LlmUnavailableError, 其他 4 条仍跑完."""
    scripts = _make_audit_scripts(["R001", "R002", "R003", "R004", "R005"])
    # R003 抛错
    _patch_provider(monkeypatch, _ThreadSafeScriptedProvider(scripts, raise_for={"R003"}))

    runner = CliRunner()
    result = runner.invoke(main, ["audit-patient", PT_ID, "--concurrency", "3"])
    assert result.exit_code == 1, f"stderr=\n{result.stderr}\nstdout=\n{result.stdout}"

    # summary 应该说 4 completed, 1 failed
    assert "4 completed" in result.stdout
    assert "1 failed" in result.stdout
    assert "0 pending" in result.stdout
    assert "LLM unavailable rules: R003" in result.stdout

    # DB 4 行
    conn = sqlite3.connect(audit_project["db"])
    n = conn.execute("SELECT COUNT(*) FROM audit_runs WHERE patient_id = ?", (PT_ID,)).fetchone()[0]
    conn.close()
    assert n == 4


def test_concurrency_1_llm_fail_isolated_like_parallel(audit_project, monkeypatch):
    """concurrency=1 时 LlmUnavailableError 标 failed 继续 — 与并发模式对齐 (harden-onsite-redlines)."""
    scripts = _make_audit_scripts(["R001", "R002", "R003", "R004", "R005"])
    # R002 抛 → 仅第 2 条 failed, R003-R005 照常跑完
    _patch_provider(monkeypatch, _ThreadSafeScriptedProvider(scripts, raise_for={"R002"}))

    runner = CliRunner()
    result = runner.invoke(main, ["audit-patient", PT_ID])  # 默认 concurrency=1
    assert result.exit_code == 1

    assert "4 completed" in result.stdout
    assert "1 failed" in result.stdout
    assert "0 pending" in result.stdout
    assert "concurrency: 1" in result.stdout
    assert "LLM unavailable rules: R002" in result.stdout


def test_concurrency_max_workers_capped_by_rule_count(audit_project, monkeypatch):
    """concurrency=10 但只有 5 条规则, ThreadPoolExecutor max_workers 应为 5."""
    scripts = _make_audit_scripts(["R001", "R002", "R003", "R004", "R005"])
    _patch_provider(monkeypatch, _ThreadSafeScriptedProvider(scripts))

    runner = CliRunner()
    result = runner.invoke(main, ["audit-patient", PT_ID, "--concurrency", "10"])
    assert result.exit_code == 0
    # summary 仍报 concurrency: 10 (用户指定值, 实际 max_workers 由 min(10, 5) 算出来但不打印)
    assert "concurrency: 10" in result.stdout
    assert "5 completed" in result.stdout


def test_concurrency_invalid_value_rejected(audit_project):
    """concurrency=0 / 11 / abc 应被 click 拒绝."""
    runner = CliRunner()

    # 0 (低于下限)
    r1 = runner.invoke(main, ["audit-patient", PT_ID, "--concurrency", "0"])
    assert r1.exit_code == 2

    # 11 (超上限)
    r2 = runner.invoke(main, ["audit-patient", PT_ID, "--concurrency", "11"])
    assert r2.exit_code == 2

    # 非整数
    r3 = runner.invoke(main, ["audit-patient", PT_ID, "--concurrency", "abc"])
    assert r3.exit_code == 2
