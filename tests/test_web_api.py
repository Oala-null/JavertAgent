# -*- coding: utf-8 -*-
"""Web API smoke test — 不连 LLM / 142 SQL Server."""

from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    """构造 TestClient. 强制关闭 142 双写, 避免触发真实连接."""
    # 先清掉 JAVERT_* 干扰
    for key in list(os.environ):
        if key.startswith("JAVERT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "false")
    monkeypatch.setenv("JAVERT_SESSION_SECRET", "pytest-session-secret-not-for-prod")
    monkeypatch.setenv("JAVERT_AUDIT_DB", str(tmp_path / "audit.sqlite"))
    monkeypatch.setenv("JAVERT_DATA_DIR", str(tmp_path))
    (tmp_path / "pilot_patients.txt").write_text(
        "TEST-P001\nTEST-P002\nTEST-P003\n",
        encoding="utf-8",
    )

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


def _as_logged_in(client) -> None:
    """伪造已登录 session cookie (与 starlette SessionMiddleware 同构签名).

    进院前红区修复后 /api/patients | /api/audit | /api/sync 需登录;
    测试知道 fixture 注入的 secret，直接签一个 user_id=1 的 cookie.
    """
    import base64 as _b64
    import json as _json

    import itsdangerous

    from javert.config import get_config

    cfg = get_config()
    signer = itsdangerous.TimestampSigner(str(cfg.session_secret))
    payload = _b64.b64encode(_json.dumps({"user_id": 1}).encode("utf-8"))
    client.cookies.set(cfg.session_cookie_name, signer.sign(payload).decode("utf-8"))


def test_phi_endpoints_require_login(client):
    """红区契约: 患者/审计/同步 API 匿名必须 401 (之前在 PUBLIC_PREFIXES, PHI 裸奔)."""
    for path in (
        "/api/patients/sample?n=1&pool=pilot",
        "/api/patients/pools",
        "/api/audit/runs?limit=1",
        "/api/sync/status",
    ):
        resp = client.get(path)
        assert resp.status_code == 401, f"{path} 匿名应 401, 实际 {resp.status_code}"


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
    for field in [
        "rule_id", "rule_kind", "clinical_criteria_ref", "domain",
        "violation_type", "question", "status", "has_prompt_addon",
    ]:
        assert field in r0, f"missing field {field} in {r0}"
    # 三个规则命名空间均可发现。
    import re
    for r in rules:
        assert re.fullmatch(r"R\d{3}|RD\d{2,3}|CD\d{2,3}", r["rule_id"])
    cd01 = next(r for r in rules if r["rule_id"] == "CD01")
    assert cd01["rule_kind"] == "chronic_disease_qualification"
    assert cd01["clinical_criteria_ref"] == "hlj-outpatient-chronic-2025/CD01"


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
    for field in ["rule_kind", "clinical_criteria_ref", "domain", "violation_type", "question", "example", "status",
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
    _as_logged_in(client)
    resp = client.get("/api/patients/sample?n=2&pool=pilot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pool"] == "pilot"
    assert len(data["patient_ids"]) == 2
    assert data["pool_size"] >= 2


def test_sample_pool_invalid(client):
    _as_logged_in(client)
    resp = client.get("/api/patients/sample?n=1&pool=bogus")
    assert resp.status_code == 422  # FastAPI Query pattern 校验


def test_pools_endpoint(client):
    _as_logged_in(client)
    resp = client.get("/api/patients/pools")
    assert resp.status_code == 200
    data = resp.json()
    assert "pilot" in data
    assert "full" in data


def test_audit_runs_query(client):
    _as_logged_in(client)
    resp = client.get("/api/audit/runs?limit=5")
    assert resp.status_code == 200
    runs = resp.json()
    assert isinstance(runs, list)
    # runs 可能为空 (新装项目), 这里只验 200 + list


def test_audit_run_detail_404(client):
    _as_logged_in(client)
    resp = client.get("/api/audit/runs/aud_NOTEXIST123")
    assert resp.status_code == 404


def test_index_html_served(client):
    resp = client.get("/")
    # static dir 存在则 200; 否则 200 with json error - 都不挂
    assert resp.status_code == 200
    # 优先验证 HTML
    if "text/html" in resp.headers.get("content-type", ""):
        assert "Javert" in resp.text
