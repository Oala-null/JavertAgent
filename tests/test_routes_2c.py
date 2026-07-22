# -*- coding: utf-8 -*-
"""2C 平台对接接口测试 (submit/results) — 不连 LLM / 142.

契约: docs/2c对接_javert审计服务.md
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import pytest


class _FakeLoader:
    """J66252 有数据, 其余患者查无."""

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        if patient_id == "J66252":
            return pd.DataFrame({"record_name": ["入院记录"]})
        return pd.DataFrame()

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        if patient_id == "J66252":
            return pd.DataFrame({
                "medins_list_name": ["麻醉后复苏监护(PACU)", "静脉输液"],
                "med_list_codg": ["331501001", "120400001"],
                "medins_list_codg": ["F00123", "F00456"],
            })
        return pd.DataFrame()


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient: 关 142 双写 + sqlite 落 tmp + stub 掉真审计."""
    for key in list(os.environ):
        if key.startswith("JAVERT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "false")
    monkeypatch.setenv("JAVERT_AUDIT_DB", str(tmp_path / "audit.sqlite"))
    monkeypatch.setenv("JAVERT_SESSION_SECRET", "pytest-session-secret-not-for-prod")

    from javert.config import reset_config_cache
    reset_config_cache()

    if "javert.web.api.main" in sys.modules:
        del sys.modules["javert.web.api.main"]
    if "javert.store.sqlserver_store" in sys.modules:
        from javert.store.sqlserver_store import reset_sqlserver_store
        reset_sqlserver_store()

    from javert.web.api import routes_audit
    monkeypatch.setattr(routes_audit, "_get_loader", lambda: _FakeLoader())
    monkeypatch.setattr(routes_audit, "_2c_run_patient", lambda syxh: None)
    monkeypatch.setattr(routes_audit, "_hub_probe", lambda syxh: False)  # 默认中台也查无
    routes_audit._2c_tasks.clear()

    from fastapi.testclient import TestClient
    from javert.web.api.main import create_app

    app = create_app(with_mssql=True)
    with TestClient(app) as c:
        yield c

    routes_audit._2c_tasks.clear()
    reset_config_cache()


def _wait_done(client, syxh: str, timeout: float = 3.0) -> dict:
    """轮询直到 status=done (worker 是后台线程)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/audit/results/{syxh}").json()
        if body["status"] == "done":
            return body
        time.sleep(0.05)
    raise AssertionError(f"{syxh} 未在 {timeout}s 内 done")


def test_public_no_auth_required():
    """submit/results 免鉴权, /api/audit 其余路径仍受保护."""
    from javert.web.middleware import _path_protected

    assert not _path_protected("/api/audit/submit")
    assert not _path_protected("/api/audit/results/J66252")
    assert _path_protected("/api/audit/run")
    assert _path_protected("/api/audit/runs")


def test_submit_accept_and_reject(client):
    resp = client.post("/api/audit/submit", json=[
        {"SYXH": "J66252", "YLZZJGDM": "H31010600042"},
        {"SYXH": "J99999", "YLZZJGDM": "H31010600042"},
        {"SYXH": "", "YLZZJGDM": ""},
    ])
    assert resp.status_code == 202
    body = resp.json()
    assert len(body["accepted"]) == 1
    first_accepted = body["accepted"][0]
    assert first_accepted["SYXH"] == "J66252"
    assert first_accepted["source"] == "local"
    assert first_accepted["attempt_id"].startswith("att_")
    first_attempt = first_accepted["attempt_id"]
    assert {r["SYXH"]: r["reason"] for r in body["rejected"]} == {
        "J99999": "本地与数据中台均查无此患者",
        "": "SYXH 为空",
    }
    body = _wait_done(client, "J66252")
    assert body["YLZZJGDM"] == "H31010600042"
    assert body["attempt_id"] == first_attempt
    assert body["outcome"] == "succeeded"
    assert body["progress"] == {"total": 0, "completed": 0, "failed": 0}

    # 重复提交已 done 的患者 → 重新受理 (重跑)
    resp2 = client.post("/api/audit/submit", json=[{"SYXH": "J66252"}])
    second_accepted = resp2.json()["accepted"][0]
    assert second_accepted["SYXH"] == "J66252"
    assert second_accepted["source"] == "local"
    assert second_accepted["attempt_id"].startswith("att_")
    assert second_accepted["attempt_id"] != first_attempt
    body2 = _wait_done(client, "J66252")  # 等 worker 消费完再 teardown, 防 monkeypatch 撤销后跑真审计
    assert body2["attempt_id"] == second_accepted["attempt_id"]


def test_submit_hub_fallback(client, monkeypatch):
    """本地查无 → 中台有 → 受理 source=hub; 中台连接失败 → 诚实 reason."""
    from javert.web.api import routes_audit

    monkeypatch.setattr(routes_audit, "_hub_probe", lambda syxh: True)
    body = client.post("/api/audit/submit", json=[{"SYXH": "211449756"}]).json()
    assert body["accepted"][0]["SYXH"] == "211449756"
    assert body["accepted"][0]["source"] == "hub"
    assert body["accepted"][0]["attempt_id"].startswith("att_")
    assert routes_audit._2c_tasks["211449756"]["source"] == "hub"
    _wait_done(client, "211449756")

    def _boom(syxh):
        raise RuntimeError("hub down")

    monkeypatch.setattr(routes_audit, "_hub_probe", _boom)
    body = client.post("/api/audit/submit", json=[{"SYXH": "211000000"}]).json()
    assert body["rejected"] == [{"SYXH": "211000000", "reason": "本地查无, 数据中台连接失败"}]


def test_hub_cfg_paths(tmp_path, monkeypatch):
    """_hub_cfg_for: 整链路路径全指向取数目录 — 含部署机 .env 把 notes/fees
    指到合并版文件 (shi_fee_with_szx.csv) 的场景 (62 实测踩过)."""
    from javert.config import get_config, reset_config_cache
    from javert.web.api.routes_audit import _hub_cfg_for

    monkeypatch.setenv("JAVERT_FEES_FILE", "shi_fee_with_szx.csv")
    monkeypatch.setenv("JAVERT_NOTES_FILE", "case_notes_with_szx.csv")
    reset_config_cache()
    try:
        cfg2 = _hub_cfg_for(get_config(), tmp_path)
    finally:
        reset_config_cache()
    assert cfg2.notes_path == tmp_path / "case_notes.csv"
    assert cfg2.fees_path == tmp_path / "shi_fee.csv"
    assert cfg2.zd_path == tmp_path / "shi_zd.csv"
    assert cfg2.ss_path == tmp_path / "shi_ss.csv"
    assert cfg2.labs_path == tmp_path / "lab_results.csv"
    assert cfg2.examinations_path == tmp_path / "examinations.csv"


def test_results_unknown(client):
    body = client.get("/api/audit/results/NEVER").json()
    assert body["status"] == "unknown"
    assert body["outcome"] == "unknown"
    assert body["attempt_id"] is None
    assert body["error_code"] == ""
    assert body["retryable"] is False
    assert body["progress"] == {"total": 0, "completed": 0, "failed": 0}
    assert body["results"] == []
    assert body["summary"] == {"total": 0, "violation": 0, "inconclusive": 0, "clean": 0}


def test_results_done_with_runs(client):
    """注入一条已落库的裁决 → results 出全字段."""
    from javert.audit.result import AuditResult, Evidence
    from javert.config import get_config
    from javert.store.audit_store import SqliteStore
    from javert.web.api import routes_audit

    result = AuditResult(
        run_id="aud_TESTtest0001",
        rule_id="R191",
        patient_id="J66252",
        verdict="VIOLATION",
        confidence=0.85,
        reasoning="R191 经 search_fees 核实, 应判 VIOLATION",
        evidence=[Evidence(source="search_fees", locator="麻醉后复苏监护(PACU)", text="费用明细中出现 ¥300 麻醉后复苏监护(PACU)项")],
        duration_ms=45000,
        model="test-model",
        started_at=datetime(2026, 7, 15, 8, 0, 0, tzinfo=timezone.utc),
    )
    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    store.write(result)
    store.close()

    routes_audit._2c_tasks["J66252"] = {
        "YLZZJGDM": "H31010600042", "status": "done", "total": 1,
        "run_ids": ["aud_TESTtest0001"], "failed": [],
        "submitted_at": "2026-07-15T08:00:00+00:00",
    }

    body = client.get("/api/audit/results/J66252").json()
    assert body["status"] == "done"
    assert body["summary"] == {"total": 1, "violation": 1, "inconclusive": 0, "clean": 0}
    (item,) = body["results"]
    assert item["rule_id"] == "R191"
    assert item["verdict"] == "VIOLATION"
    assert item["verdict_label"] == "违规"
    assert item["evidence"] == [{
        "source": "search_fees",
        "locator": "麻醉后复苏监护(PACU)",
        "text": "费用明细中出现 ¥300 麻醉后复苏监护(PACU)项",
    }]
    assert item["finished_at"] == "2026-07-15T08:00:45+00:00"
    assert item["rule_name"]  # R191 yaml 存在 → violation_type 非空
    # hits: V 结果 join 患者费用行出编码 (2C 侧凭 code_nat/matched_fee_name 对明细)
    (hit,) = item["hits"]
    assert hit["source"] == "fee"
    assert hit["matched_fee_name"] == "麻醉后复苏监护(PACU)"
    assert hit["code_nat"] == "331501001"
    assert hit["code_local"] == "F00123"
    # behavior-naming 三新字段: 顶层扁平编码/名称 + 行为认定名称 (R191=重复收费)
    assert item["hit_codes"] == ["331501001"]
    assert item["hit_names"] == ["麻醉后复苏监护(PACU)"]
    assert item["behavior_name"] == "重复收费"
    # reasoning 对外自然语言化: 术语全部映射, R 代号/工具名/英文判定词不外泄
    assert item["reasoning"] == "本规则 经 费用明细检索 核实, 应判 违规"

    # 重启恢复: 任务表清空后, results 回放 sqlite 历史 (每规则最新) 按 done 返回
    routes_audit._2c_tasks.clear()
    body2 = client.get("/api/audit/results/J66252").json()
    assert body2["status"] == "done"
    assert body2["outcome"] == "succeeded"
    assert body2["attempt_id"] is None  # 重启后的 legacy/history replay 无 attempt
    assert body2["summary"]["total"] == 1
    assert body2["results"][0]["run_id"] == "aud_TESTtest0001"


def test_running_duplicate_submit_reuses_attempt(client, monkeypatch):
    """running 幂等提交复用当前 attempt，不能重复入队。"""
    from javert.web.api import routes_audit

    routes_audit._2c_tasks["J66252"] = {
        "YLZZJGDM": "H31010600042",
        "status": "running",
        "outcome": "running",
        "attempt_id": "att_existing0001",
        "source": "local",
        "stage": "audit",
        "total": 2,
        "run_ids": [],
        "failed": [],
        "submitted_at": "2026-07-22T00:00:00+00:00",
    }
    queued: list[str] = []
    monkeypatch.setattr(routes_audit._2c_queue, "put", queued.append)

    body = client.post("/api/audit/submit", json=[{"SYXH": "J66252"}]).json()

    assert body["accepted"] == [{
        "SYXH": "J66252", "source": "local", "attempt_id": "att_existing0001",
    }]
    assert queued == []


def test_running_duplicate_submit_does_not_reprobe_sources(client, monkeypatch):
    """running 幂等命中应先于数据源探测，避免瞬时连接故障把重复提交驳回。"""
    from javert.web.api import routes_audit

    routes_audit._2c_tasks["J66252"] = {
        "YLZZJGDM": "H31010600042",
        "status": "running",
        "outcome": "running",
        "attempt_id": "att_existing0002",
        "source": "hub",
        "stage": "hub_fetch",
        "total": None,
        "run_ids": [],
        "failed": [],
        "submitted_at": "2026-07-22T00:00:00+00:00",
    }

    def _must_not_probe():
        raise AssertionError("running duplicate must not load data source")

    monkeypatch.setattr(routes_audit, "_get_loader", _must_not_probe)
    body = client.post("/api/audit/submit", json=[{"SYXH": "J66252"}]).json()

    assert body["accepted"] == [{
        "SYXH": "J66252", "source": "hub", "attempt_id": "att_existing0002",
    }]
    assert body["rejected"] == []


def test_all_rule_failures_have_failed_outcome(client, monkeypatch):
    """所有入选规则均失败时没有成功子结果，不能标为 partial。"""
    from javert.web.api import routes_audit

    def _fail_every_rule(syxh: str) -> None:
        with routes_audit._2c_lock:
            task = routes_audit._2c_tasks[syxh]
            task["stage"] = "audit"
            task["total"] = 2
            task["failed"].extend(["R020", "R232"])

    monkeypatch.setattr(routes_audit, "_2c_run_patient", _fail_every_rule)
    client.post("/api/audit/submit", json=[{"SYXH": "J66252"}])
    body = _wait_done(client, "J66252")

    assert body["status"] == "done"
    assert body["outcome"] == "failed"
    assert body["error_code"] == "RULE_FAILURES"
    assert body["retryable"] is True
    assert body["results"] == []
    assert body["progress"] == {"total": 2, "completed": 0, "failed": 2}


def test_patient_level_failure_has_terminal_outcome(client, monkeypatch):
    """患者级 Hub 异常保持 status 兼容，但必须显式 outcome=failed。"""
    from javert.web.api import routes_audit

    def _fail_in_hub(syxh: str) -> None:
        with routes_audit._2c_lock:
            routes_audit._2c_tasks[syxh]["stage"] = "hub_fetch"
        raise RuntimeError("synthetic hub failure")

    monkeypatch.setattr(routes_audit, "_2c_run_patient", _fail_in_hub)
    accepted = client.post(
        "/api/audit/submit", json=[{"SYXH": "J66252"}]
    ).json()["accepted"][0]
    body = _wait_done(client, "J66252")

    assert body["attempt_id"] == accepted["attempt_id"]
    assert body["status"] == "done"
    assert body["outcome"] == "failed"
    assert body["error_code"] == "HUB_FETCH_FAILED"
    assert body["retryable"] is True
    assert body["results"] == []
    assert body["progress"] == {"total": 0, "completed": 0, "failed": 0}
    assert body["error"] == "审计中断: RuntimeError"


def test_results_partial_and_malformed_diagnostic(client):
    """单规则失败返回 partial；malformed 对外为中文且带稳定诊断码。"""
    from javert.audit.result import AuditResult
    from javert.config import get_config
    from javert.store.audit_store import SqliteStore
    from javert.web.api import routes_audit

    result = AuditResult(
        run_id="aud_MALFORMED001",
        rule_id="R020",
        patient_id="J66252",
        verdict="INCONCLUSIVE",
        confidence=0.0,
        reasoning="malformed verdict JSON (repair failed)",
        evidence=[],
        duration_ms=100,
        model="test-model",
        started_at=datetime(2026, 7, 22, 8, 0, 0, tzinfo=timezone.utc),
    )
    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    store.write(result)
    store.close()
    routes_audit._2c_tasks["J66252"] = {
        "YLZZJGDM": "H31010600042",
        "status": "done",
        "outcome": "partial",
        "attempt_id": "att_partial0001",
        "source": "local",
        "stage": "done",
        "total": 2,
        "run_ids": [result.run_id],
        "failed": ["R232"],
        "error_code": "RULE_FAILURES",
        "retryable": True,
        "submitted_at": "2026-07-22T08:00:00+00:00",
    }

    body = client.get("/api/audit/results/J66252").json()

    assert body["outcome"] == "partial"
    assert body["progress"] == {"total": 2, "completed": 1, "failed": 1}
    assert body["error_code"] == "RULE_FAILURES"
    assert body["retryable"] is True
    item = body["results"][0]
    assert item["diagnostic_code"] == "LLM_OUTPUT_MALFORMED"
    assert item["retryable"] is True
    assert item["reasoning"] == "模型输出格式异常，本规则未完成自动判定，需人工复核。"
    assert "malformed" not in item["reasoning"]

    # 即使 task 本身执行成功，存在可重试的规则诊断也要上卷到顶层。
    routes_audit._2c_tasks["J66252"].update({
        "outcome": "succeeded",
        "total": 1,
        "failed": [],
        "error_code": "",
        "retryable": False,
    })
    body2 = client.get("/api/audit/results/J66252").json()
    assert body2["outcome"] == "succeeded"
    assert body2["retryable"] is True


def test_truncated_diagnostic_is_chinese_and_retryable():
    from javert.web.api.routes_audit import _2c_result_diagnostic

    reasoning, code, retryable = _2c_result_diagnostic(
        "LLM output truncated (repair failed)"
    )
    assert reasoning == "模型输出达到长度上限，本规则未完成自动判定，需人工复核。"
    assert code == "LLM_OUTPUT_TRUNCATED"
    assert retryable is True
