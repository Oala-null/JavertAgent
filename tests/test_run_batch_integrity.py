# -*- coding: utf-8 -*-
"""harden-onsite-redlines Task 2.3: run-batch SSE 完整性.

- persist 抛错 → fail(stage=persist) 且不发 result
- 未知 rule_id → fail(stage=unknown_rule) 逐条回执
- 正常路径 result 事件字段逐字回归 (契约只加不改)

不调 LLM: Runner / persist_one / loader 全 monkeypatch.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from javert.audit.result import AuditResult, Evidence, ToolCall
from javert.config import get_config
from javert.web.api import routes_audit as ra
from javert.web.api.main import create_app


def _mk_result(rule_id: str, patient_id: str) -> AuditResult:
    return AuditResult(
        run_id="aud_abcdef123456",
        rule_id=rule_id,
        patient_id=patient_id,
        verdict="CLEAN",
        confidence=0.9,
        reasoning="ok",
        evidence=[Evidence(source="note", locator="入院诊断", text="测试")],
        tool_calls=[ToolCall(tool_name="note_diagnosis")],
        duration_ms=10,
        model="fake-qwen",
        started_at=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )


class _FakeRunner:
    def __init__(self, *a, **kw):
        pass

    def audit(self, rule, patient_id):
        return _mk_result(rule.rule_id, patient_id)


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        ev, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if ev is not None:
            events.append((ev, data))
    return events


@pytest.fixture
def batch_client(monkeypatch):
    monkeypatch.setattr(ra, "_get_loader", lambda: object())
    monkeypatch.setattr(ra, "build_executor", lambda loader, cfg: object())
    monkeypatch.setattr(ra, "Runner", _FakeRunner)
    client = TestClient(create_app(with_mssql=True))
    # /api/audit 在 PROTECTED — 手工签 session cookie 过 AuthMiddleware
    secret = get_config().session_secret
    data = base64.b64encode(json.dumps(
        {"user_id": 1, "prev_last_login": "first_login"}).encode())
    cookie = TimestampSigner(str(secret)).sign(data).decode()
    client.cookies.set(get_config().session_cookie_name, cookie)
    return client


def test_persist_failure_emits_fail_without_result(batch_client, monkeypatch):
    def _boom(result, rule, triggered_by):
        raise RuntimeError("sqlite locked")

    monkeypatch.setattr(ra, "persist_one", _boom)
    r = batch_client.post("/api/audit/run-batch",
                          json={"patient_id": "JT001", "rules": ["R191"]})
    events = _parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert "result" not in kinds, "persist 失败不得发 result"
    fails = [d for e, d in events if e == "fail"]
    assert len(fails) == 1
    assert fails[0]["rule_id"] == "R191"
    assert fails[0]["stage"] == "persist"
    assert "sqlite locked" in fails[0]["message"]
    done = [d for e, d in events if e == "done"][0]
    assert done == {"total": 1, "completed": 0}


def test_unknown_rule_gets_explicit_fail(batch_client, monkeypatch):
    monkeypatch.setattr(ra, "persist_one",
                        lambda result, rule, triggered_by: {"sync_state": "synced"})
    r = batch_client.post("/api/audit/run-batch",
                          json={"patient_id": "JT001", "rules": ["R191", "R999"]})
    events = _parse_sse(r.text)
    fails = [d for e, d in events if e == "fail"]
    assert len(fails) == 1
    assert fails[0]["rule_id"] == "R999"
    assert fails[0]["stage"] == "unknown_rule"
    # R191 正常执行
    results = [d for e, d in events if e == "result"]
    assert [d["rule_id"] for d in results] == ["R191"]
    # start.total 只计已知规则 (老字段语义不变)
    start = [d for e, d in events if e == "start"][0]
    assert start["rules"] == ["R191"] and start["total"] == 1


def test_normal_path_result_payload_keeps_legacy_fields_and_adds_public_projection(
    batch_client, monkeypatch,
):
    monkeypatch.setattr(ra, "persist_one",
                        lambda result, rule, triggered_by: {"sync_state": "synced"})
    r = batch_client.post("/api/audit/run-batch",
                          json={"patient_id": "JT001", "rules": ["R191"]})
    events = _parse_sse(r.text)
    results = [d for e, d in events if e == "result"]
    assert len(results) == 1
    # 当前 SSE 契约：旧字段不变，公开解释与 Promise 摘要只做 additive 扩展。
    expected = {
        "run_id": "aud_abcdef123456",
        "rule_id": "R191",
        "patient_id": "JT001",
        "verdict": "CLEAN",
        "confidence": 0.9,
        "reasoning": "ok",
        "evidence": [{"source": "note", "locator": "入院诊断",
                      "text": "测试", "anchor": None}],
        "tool_calls": [{"tool_name": "note_diagnosis", "arguments": {},
                        "result": "", "duration_ms": 0, "cached": False,
                        "structured_output": None}],
        "duration_ms": 10,
        "model": "fake-qwen",
        "started_at": "2026-07-07T00:00:00+00:00",
        "eligibility_evaluation": None,
    }
    assert {key: results[0][key] for key in expected} == expected
    assert set(results[0]) == {*expected, "public_explanation", "promise"}
    assert set(results[0]["public_explanation"]) == {
        "conclusion", "narrative", "audit_items", "charge_facts", "basis",
        "clinical_evidence", "review_needs",
    }
    assert results[0]["promise"] is None
    done = [d for e, d in events if e == "done"][0]
    assert done == {"total": 1, "completed": 1}
