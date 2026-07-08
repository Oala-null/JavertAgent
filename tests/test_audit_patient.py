# -*- coding: utf-8 -*-
"""audit-patient CLI 集成测试 — 用 Click CliRunner + 注入 FakeProvider."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from click.testing import CliRunner

from javert.audit.rule import Rule
from javert.audit.rule_writer import write_rule
from javert.cli import main
from javert.tools.llm_provider import LlmUnavailableError


# ============================================================
# Test fixtures
# ============================================================

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
    """构造 rules + data csv + audit db 的最小项目."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = tmp_path / "audit.sqlite"

    # 写 3 个 P0 rule + 1 个 P1 + 1 个 P0 abandoned
    write_rule(_make_rule("R045", "P0"), rules_dir / "R045.yaml")
    write_rule(_make_rule("R191", "P0"), rules_dir / "R191.yaml")
    write_rule(_make_rule("R300", "P0"), rules_dir / "R300.yaml")
    write_rule(_make_rule("R312", "P0", status="abandoned"), rules_dir / "R312.yaml")
    write_rule(_make_rule("R154", "P1"), rules_dir / "R154.yaml")

    # 写最小患者数据
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
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "false")  # 关 142 双写
    monkeypatch.setenv("JAVERT_RETRY_BUDGET", "1")    # 加速重试

    from javert.config import reset_config_cache
    reset_config_cache()
    yield {"root": tmp_path, "db": db_path}
    reset_config_cache()


class _ScriptedProvider:
    """脚本化 LLM provider — 按预设序列返回 content; 可在指定 call 抛 LlmUnavailableError."""

    def __init__(
        self,
        scripts: list[str] | None = None,
        per_call: list[str] | None = None,
        raise_on_call: int | None = None,
    ):
        self.scripts = list(per_call or scripts or [])
        self.call_idx = 0
        self.raise_on_call = raise_on_call
        self.model_name = "fake-qwen"

    def chat_with_retry(self, messages, **kwargs):
        self.call_idx += 1
        if self.raise_on_call is not None and self.call_idx == self.raise_on_call:
            raise LlmUnavailableError("simulated outage")
        if not self.scripts:
            raise RuntimeError(f"FakeProvider 脚本用尽 (call #{self.call_idx})")
        return {
            "content": self.scripts.pop(0),
            "reasoning_content": "",
            "usage": None,
            "raw_response": {},
        }


def _patch_provider(monkeypatch, provider: _ScriptedProvider) -> _ScriptedProvider:
    """把 Runner 内的 Qwen35Provider 替换成给定脚本化 provider."""
    monkeypatch.setattr(
        "javert.audit.runner.Qwen35Provider",
        lambda *args, **kwargs: provider,
    )
    return provider


# 通用 audit 脚本: 1 次 tool_call + 1 次 verdict
def _audit_script(verdict: str, tool: str = "note_diagnosis", arg: str = "JT001") -> list[str]:
    return [
        f'<tool_call>{{"name": "{tool}", "arguments": {{"patient_id": "{arg}"}}}}</tool_call>',
        '```json\n{"verdict": "' + verdict + '", "confidence": 0.9, '
        '"evidence": [{"source":"note","locator":"入院诊断","text":"测试诊断"}], '
        '"reasoning": "ok"}\n```',
    ]


# ============================================================
# 测试场景 7.1–7.6
# ============================================================

def test_audit_patient_runs_three_p0_rules(audit_project, monkeypatch):
    """7.1 + 7.2: 默认 P0 跑 3 条 (R045/R191/R300), 跳 R312 (abandoned), 不跑 R154 (P1)."""
    scripts: list[str] = []
    for _ in range(3):
        scripts += _audit_script("CLEAN")
    _patch_provider(monkeypatch, _ScriptedProvider(per_call=scripts))

    runner = CliRunner()
    result = runner.invoke(main, ["audit-patient", PT_ID])
    assert result.exit_code == 0, f"stderr=\n{result.stderr}\nstdout=\n{result.stdout}"

    # stderr 有 3 条进度
    assert "[1/3] R045" in result.stderr
    assert "[2/3] R191" in result.stderr
    assert "[3/3] R300" in result.stderr
    # R312 abandoned 不在「进度」里 (header 的 excluded 提示允许出现)
    progress_lines = [l for l in result.stderr.splitlines() if l.startswith("[")]
    assert not any("R312" in l for l in progress_lines)
    # R154 P1 也不在 (header 也不会提)
    assert "R154" not in result.stderr

    # stdout summary
    assert "audit-patient JT001 summary" in result.stdout
    assert "Verdicts: V=0 / C=3 / I=0" in result.stdout
    assert "3 completed" in result.stdout
    # selection_label 提示 abandoned 被排除
    assert "excluded R312 [abandoned]" in result.stdout

    # DB 应有 3 行 audit_runs
    conn = sqlite3.connect(audit_project["db"])
    n = conn.execute(
        "SELECT COUNT(*) FROM audit_runs WHERE patient_id = ?", (PT_ID,)
    ).fetchone()[0]
    conn.close()
    assert n == 3


def test_audit_patient_explicit_rules_force_include_abandoned(audit_project, monkeypatch):
    """7.3: --rules R045,R312 强制纳入 abandoned, 实跑 2 条."""
    scripts: list[str] = _audit_script("VIOLATION") + _audit_script("CLEAN")
    _patch_provider(monkeypatch, _ScriptedProvider(per_call=scripts))

    runner = CliRunner()
    result = runner.invoke(
        main, ["audit-patient", PT_ID, "--rules", "R045,R312"],
    )
    assert result.exit_code == 0, f"stderr=\n{result.stderr}\nstdout=\n{result.stdout}"

    assert "[1/2] R045" in result.stderr
    assert "[2/2] R312" in result.stderr
    # 强制纳入提示
    assert "force-included abandoned: R312" in result.stdout
    assert "Verdicts: V=1 / C=1 / I=0" in result.stdout


def test_share_tool_cache_hits_on_second_rule(audit_project, monkeypatch):
    """7.4: --share-tool-cache 两条规则共享 cache, 第二条同参数 tool 命中."""
    # 两条规则各 1 tool + 1 verdict = 4 个 content. tool 参数完全一致 → 第二个应命中 cache.
    scripts = _audit_script("CLEAN") + _audit_script("CLEAN")
    _patch_provider(monkeypatch, _ScriptedProvider(per_call=scripts))

    runner = CliRunner()
    result = runner.invoke(
        main, ["audit-patient", PT_ID, "--rules", "R045,R191", "--share-tool-cache"],
    )
    assert result.exit_code == 0, f"stderr=\n{result.stderr}\nstdout=\n{result.stdout}"

    # 第一条 cached 0, 第二条 cached 1
    assert "[1/2] R045" in result.stderr and "cached 0)" in result.stderr.split("R045")[1].split("\n")[0]
    second_line = result.stderr.split("R191")[1].split("\n")[0]
    assert "cached 1)" in second_line
    # summary 显示 hit_rate
    assert "hit_rate=" in result.stdout
    # 2 tool calls 总, 1 cached → 50.0%
    assert "Tool calls: 2 total, 1 cached" in result.stdout
    assert "shared" in result.stdout  # cache mode


def test_invalid_priority_rejected_by_click(audit_project):
    """7.5: --priority P9 Click 拒绝 → exit 2."""
    runner = CliRunner()
    result = runner.invoke(main, ["audit-patient", PT_ID, "--priority", "P9"])
    assert result.exit_code == 2
    assert "Invalid value" in result.stderr or "P9" in result.stderr


def test_no_rules_match_priority_exits_2(audit_project, monkeypatch):
    """7.5 衍生: priority 合法但无匹配规则 → exit 2."""
    # fixture 中没有 P2 规则
    runner = CliRunner()
    result = runner.invoke(main, ["audit-patient", PT_ID, "--priority", "P2"])
    assert result.exit_code == 2
    assert "没有匹配的规则" in result.stderr


def test_llm_unavailable_mid_batch_continues_and_exits_1(audit_project, monkeypatch):
    """7.6 (harden-onsite-redlines 改): 第 2 条 LlmUnavailableError 标 failed 继续跑第 3 条."""
    # 第 1 条: 2 calls (tool + verdict). 第 2 条: 第 1 call (call_idx=3) raise.
    # 第 3 条: 继续消费 2 个 content (串行不再中断).
    scripts = _audit_script("CLEAN") + _audit_script("CLEAN")
    provider = _ScriptedProvider(per_call=scripts, raise_on_call=3)
    _patch_provider(monkeypatch, provider)

    runner = CliRunner()
    result = runner.invoke(
        main, ["audit-patient", PT_ID, "--rules", "R045,R191,R300"],
    )
    assert result.exit_code == 1

    # 第 1 条 verdict, 第 2 条标 LLM 不可用, 第 3 条照常跑 (失败不拖垮整患者)
    assert "[1/3] R045 → C" in result.stderr
    assert "[2/3] R191 → LLM 不可用" in result.stderr
    assert "[3/3] R300 → C" in result.stderr
    # summary: 2 完成 1 失败, 无 pending
    assert "2 completed" in result.stdout
    assert "1 failed" in result.stdout
    assert "0 pending" in result.stdout
    assert "LLM unavailable rules: R191" in result.stdout

    # DB 应有 2 行 (R045 + R300)
    conn = sqlite3.connect(audit_project["db"])
    n = conn.execute(
        "SELECT COUNT(*) FROM audit_runs WHERE patient_id = ?", (PT_ID,)
    ).fetchone()[0]
    conn.close()
    assert n == 2
