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
    routes_audit._2c_tasks.clear()

    from fastapi.testclient import TestClient
    from javert.web.api.main import create_app

    app = create_app()
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
    assert body["accepted"] == [{"SYXH": "J66252"}]
    assert {r["SYXH"]: r["reason"] for r in body["rejected"]} == {
        "J99999": "查无此患者数据",
        "": "SYXH 为空",
    }
    body = _wait_done(client, "J66252")
    assert body["YLZZJGDM"] == "H31010600042"

    # 重复提交已 done 的患者 → 重新受理 (重跑)
    resp2 = client.post("/api/audit/submit", json=[{"SYXH": "J66252"}])
    assert resp2.json()["accepted"] == [{"SYXH": "J66252"}]
    _wait_done(client, "J66252")  # 等 worker 消费完再 teardown, 防 monkeypatch 撤销后跑真审计


def test_results_unknown(client):
    body = client.get("/api/audit/results/NEVER").json()
    assert body["status"] == "unknown"
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
        reasoning="测试理由",
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
