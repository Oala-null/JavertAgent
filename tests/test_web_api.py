# -*- coding: utf-8 -*-
"""Web API smoke test — 不连 LLM / 142 SQL Server."""

from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture
def client(monkeypatch):
    """构造 TestClient. 强制关闭 142 双写, 避免触发真实连接."""
    # 先清掉 JAVERT_* 干扰
    for key in list(os.environ):
        if key.startswith("JAVERT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "false")

    # 重置 config / sqlserver 单例
    from javert.config import reset_config_cache
    reset_config_cache()

    # 重新导入 main 以让 STATIC_DIR 重新计算
    if "javert.web.api.main" in sys.modules:
        del sys.modules["javert.web.api.main"]
    if "javert.store.sqlserver_store" in sys.modules:
        from javert.store.sqlserver_store import reset_sqlserver_store
        reset_sqlserver_store()

    from fastapi.testclient import TestClient
    from javert.web.api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c

    reset_config_cache()


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["sql_enabled"] is False
    # sql_server_142 在 sql_enabled=false 时是 Engine None → false
    assert data["sql_server_142"]["sql_server"] is False


def test_list_rules(client):
    resp = client.get("/api/rules")
    assert resp.status_code == 200
    rules = resp.json()
    assert isinstance(rules, list)
    assert len(rules) >= 30, f"expected ~34 rules, got {len(rules)}"
    # 字段完整性
    r0 = rules[0]
    for field in ["rule_id", "domain", "violation_type", "question", "status", "has_prompt_addon"]:
        assert field in r0, f"missing field {field} in {r0}"
    # rule_id 形如 R\d{3}
    for r in rules:
        assert r["rule_id"].startswith("R") and len(r["rule_id"]) == 4


def test_get_rule_existing(client):
    # 先列出取一个真实 rule_id
    rules = client.get("/api/rules").json()
    assert rules, "no rules to test"
    rid = rules[0]["rule_id"]

    resp = client.get(f"/api/rules/{rid}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["rule_id"] == rid
    # 完整字段
    for field in ["domain", "violation_type", "question", "example", "status",
                  "prompt_addon", "trigger_keywords", "suggested_tools",
                  "expected_signal", "notes", "recent_runs"]:
        assert field in detail


def test_get_rule_404(client):
    resp = client.get("/api/rules/R999")
    assert resp.status_code == 404


def test_get_rule_yaml(client):
    rules = client.get("/api/rules").json()
    rid = rules[0]["rule_id"]
    resp = client.get(f"/api/rules/{rid}/yaml")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rule_id"] == rid
    assert "yaml_text" in data
    assert "rule_id:" in data["yaml_text"]


def test_sample_pilot(client):
    resp = client.get("/api/patients/sample?n=2&pool=pilot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pool"] == "pilot"
    assert len(data["patient_ids"]) == 2
    assert data["pool_size"] >= 2


def test_sample_pool_invalid(client):
    resp = client.get("/api/patients/sample?n=1&pool=bogus")
    assert resp.status_code == 422  # FastAPI Query pattern 校验


def test_pools_endpoint(client):
    resp = client.get("/api/patients/pools")
    assert resp.status_code == 200
    data = resp.json()
    assert "pilot" in data
    assert "full" in data


def test_audit_runs_query(client):
    resp = client.get("/api/audit/runs?limit=5")
    assert resp.status_code == 200
    runs = resp.json()
    assert isinstance(runs, list)
    # runs 可能为空 (新装项目), 这里只验 200 + list


def test_audit_run_detail_404(client):
    resp = client.get("/api/audit/runs/aud_NOTEXIST123")
    assert resp.status_code == 404


def test_index_html_served(client):
    resp = client.get("/")
    # static dir 存在则 200; 否则 200 with json error - 都不挂
    assert resp.status_code == 200
    # 优先验证 HTML
    if "text/html" in resp.headers.get("content-type", ""):
        assert "Javert" in resp.text
