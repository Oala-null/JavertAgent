# -*- coding: utf-8 -*-
"""终局 Promise 与 gate/drift/store 组合契约。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from javert.audit.result import AuditResult
from javert.audit.rule_loader import load_rule
from javert.audit.runner import Runner
from javert.config import get_config
from javert.audit.verdict_gate import GateConfig, apply_gate
from javert.data.fee_netting import NetItem
from javert.promises.loader import load_repository
from javert.promises.models import PromiseTrace
from javert.promises.registry import TerminalEvaluation, evaluate_terminal_promises
from javert.store.audit_store import SqliteStore
from javert.store.result_persister import DRIFT_TAG, _apply_drift_guard
from javert.store.sqlserver_store import SqlServerStore


def _trace(*, historical: bool = False) -> PromiseTrace:
    definition = load_repository().active_definitions[0]
    return PromiseTrace(
        promise_id=definition.promise_id,
        version=definition.version,
        kind=definition.kind,
        finality=definition.finality,
        reason_code=definition.reason_code,
        facts={"net_qty": 1, "refund_count": 1},
        historical_conflict=historical,
    )


def _result(run_id: str, verdict: str, trace: PromiseTrace | None = None) -> AuditResult:
    return AuditResult(
        run_id=run_id,
        rule_id="R151",
        patient_id="CASE-PROMISE-STORE",
        verdict=verdict,
        confidence=1.0,
        reasoning="去标识测试",
        evidence=[],
        tool_calls=[],
        duration_ms=1,
        model="offline",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        promise_trace=trace,
    )


def _locked_violation_definition(rule_id: str):
    base = load_repository().active_definitions[0]
    return base.model_copy(
        update={
            "guarantee": "VIOLATION",
            "scope": base.scope.model_copy(update={"rule_ids": [rule_id]}),
        }
    )


def test_terminal_promises_detect_conflict_without_load_order_winner():
    clean = load_repository().active_definitions[0]
    violation = clean.model_copy(
        update={"promise_id": "PR-X001", "guarantee": "VIOLATION"}
    )
    item = NetItem(
        name="乙肝表面抗原测定",
        code="SYNTH-AB",
        net_qty=1,
        distinct_billing_dates=1,
        has_refund=True,
        refund_count=1,
    )
    outcome = evaluate_terminal_promises([violation, clean], "R151", {"x": item})
    assert outcome.match is None
    assert outcome.conflict_ids == ("PR-D001", "PR-X001")


class _Executor:
    def reset_cache(self): pass
    def set_patient_context(self, _patient_id): pass
    def clear_patient_context(self): pass
    def get_tools_prompt(self): return ""
    def parse_tool_calls(self, content):
        return [{"name": "search_fees", "arguments": {}}] if "<tool_call>" in content else []
    def execute(self, _call): return "去标识工具结果", False
    def parse_errors(self, _content): return []


class _Provider:
    model_name = "offline-test"

    def __init__(self):
        self.calls = 0

    def chat_with_retry(self, _messages, **_kwargs):
        self.calls += 1
        content = (
            '<tool_call>{"name":"search_fees","arguments":{}}</tool_call>'
            if self.calls == 1
            else '```json\n{"verdict":"CLEAN","confidence":0.9,"reasoning":"去标识普通路径","evidence":[]}\n```'
        )
        return {"content": content, "finish_reason": "stop"}


class _NoRefundLoader:
    def get_fees(self, _patient_id):
        import pandas as pd

        return pd.DataFrame({
            "medins_list_name": ["乙肝表面抗原测定"],
            "med_list_codg": ["SYNTH-AB"],
            "cnt": [1],
            "fee_ocur_time": ["2026-01-01"],
        })


def test_not_applicable_keeps_existing_tool_then_llm_path():
    provider = _Provider()
    cfg = get_config()
    result = Runner(
        executor=_Executor(), provider=provider, config=cfg, loader=_NoRefundLoader()
    ).audit(load_rule(cfg.rules_path / "R151.yaml"), "CASE-PROMISE-NEGATIVE")
    assert result.verdict == "CLEAN"
    assert result.promise_trace is None
    assert provider.calls == 2
    assert [call.tool_name for call in result.tool_calls] == ["search_fees"]


def test_runner_conflict_fails_closed_without_llm(monkeypatch):
    provider = _Provider()
    cfg = get_config()
    monkeypatch.setattr(
        "javert.audit.runner.evaluate_terminal_promises",
        lambda *_args, **_kwargs: TerminalEvaluation(conflict_ids=("PR-D001", "PR-X001")),
    )
    result = Runner(
        executor=_Executor(), provider=provider, config=cfg, loader=_NoRefundLoader()
    ).audit(load_rule(cfg.rules_path / "R151.yaml"), "CASE-PROMISE-CONFLICT")
    assert result.verdict == "INCONCLUSIVE"
    assert result.reasoning == "PROMISE_CONFLICT"
    assert result.tool_calls == []
    assert provider.calls == 0


def test_valid_locked_trace_bypasses_low_confidence_count_and_rule_specific_gates():
    trace = _trace()
    cases = [
        (
            "R999",
            GateConfig(conf_ceiling=0.85),
            None,
        ),
        (
            "R151",
            GateConfig(conf_ceiling=0.85),
            {
                "x": NetItem(
                    name="乙肝表面抗原测定",
                    code="SYNTH-AB",
                    net_qty=1,
                    distinct_billing_dates=1,
                    has_refund=True,
                )
            },
        ),
        (
            "R015",
            GateConfig(unconfirmable_doc_rules={"R015"}, conf_ceiling=0.85),
            None,
        ),
    ]
    for rule_id, config, net in cases:
        definition = _locked_violation_definition(rule_id)
        rule = SimpleNamespace(
            rule_id=rule_id,
            derived_from_template="M2" if rule_id == "R151" else None,
            exam_keywords=["乙肝表面抗原"],
            prompt_addon="",
            trigger_keywords=[],
        )
        out = apply_gate(
            {"verdict": "VIOLATION", "confidence": 0.1, "evidence": []},
            rule,
            net,
            config,
            promise_trace=trace,
            promise_definitions=(definition,),
        )
        assert out.verdict == "VIOLATION"
        assert out.changed is False


def test_forged_or_incomplete_locked_trace_does_not_bypass_gate():
    out = apply_gate(
        {"verdict": "VIOLATION", "confidence": 0.1, "evidence": []},
        SimpleNamespace(rule_id="R999", derived_from_template=None),
        None,
        GateConfig(conf_ceiling=0.85),
        promise_trace={"promise_id": "PR-D001", "finality": "LOCKED"},
        promise_definitions=load_repository().active_definitions,
    )
    assert out.verdict == "INCONCLUSIVE"
    assert out.changed is True


def test_drift_guard_keeps_locked_clean_and_marks_anonymous_conflict(tmp_path):
    store = SqliteStore(tmp_path / "audit.sqlite")
    store.init_schema()
    store.write(_result("aud_aaaaaaaaaaaa", "VIOLATION"))
    current = _result("aud_bbbbbbbbbbbb", "CLEAN", _trace())
    _apply_drift_guard(current, store, sql_enabled=False)
    assert current.verdict == "CLEAN"
    assert current.promise_trace is not None
    assert current.promise_trace.historical_conflict is True
    assert DRIFT_TAG not in current.reasoning

    ordinary = _result("aud_cccccccccccc", "CLEAN")
    _apply_drift_guard(ordinary, store, sql_enabled=False)
    assert ordinary.verdict == "INCONCLUSIVE"
    assert DRIFT_TAG in ordinary.reasoning
    store.close()


def test_sqlite_promise_trace_roundtrip_old_null_and_idempotent_migration(tmp_path):
    store = SqliteStore(tmp_path / "audit.sqlite")
    store.init_schema()
    store.init_schema()
    store.write(_result("aud_dddddddddddd", "CLEAN", _trace()))
    store.write(_result("aud_eeeeeeeeeeee", "CLEAN"))
    assert store.find_by_run_id("aud_dddddddddddd").promise_trace == _trace()
    assert store.find_by_run_id("aud_eeeeeeeeeeee").promise_trace is None
    columns = [
        row[1] for row in store.conn.execute("PRAGMA table_info(audit_runs)").fetchall()
    ]
    assert columns.count("promise_trace_json") == 1
    store.close()


def _fake_engine(conn: MagicMock) -> MagicMock:
    engine = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = conn
    context.__exit__.return_value = False
    engine.connect.return_value = context
    return engine


def test_sqlserver_serializes_promise_trace_without_live_connection(monkeypatch):
    result = _result("aud_ffffffffffff", "CLEAN", _trace())
    conn = MagicMock()
    not_found = MagicMock()
    not_found.fetchone.return_value = None
    conn.execute.side_effect = [not_found, MagicMock()]
    store = SqlServerStore()
    monkeypatch.setattr(store, "get_engine", lambda: _fake_engine(conn))
    assert store.write_audit(result) is True
    params = conn.execute.call_args_list[1].args[1]
    payload = json.loads(params["promise_trace_json"])
    assert payload["promise_id"] == "PR-D001"
    assert "patient_id" not in payload["facts"]

    read_conn = MagicMock()
    row = MagicMock()
    row.fetchone.return_value = (
        result.run_id, result.rule_id, result.patient_id, result.verdict,
        result.confidence, result.reasoning, "[]", "[]", result.duration_ms,
        result.model, result.started_at, "", None, params["promise_trace_json"],
    )
    read_conn.execute.return_value = row
    read_store = SqlServerStore()
    monkeypatch.setattr(read_store, "get_engine", lambda: _fake_engine(read_conn))
    loaded = read_store.find_audit_by_run_id(result.run_id)
    assert loaded is not None and loaded.promise_trace == result.promise_trace


def test_sqlserver_promise_column_ddl_is_nullable_and_idempotent():
    ddl = (Path(__file__).parents[1] / "scripts/sql/create_javert_tables.sql").read_text(encoding="utf-8")
    assert "promise_trace_json  NVARCHAR(MAX)  NULL" in ddl
    assert "ALTER TABLE javert_audit_runs ADD promise_trace_json NVARCHAR(MAX) NULL" in ddl
    assert "IF NOT EXISTS" in ddl
