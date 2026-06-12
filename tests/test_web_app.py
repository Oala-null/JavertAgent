# -*- coding: utf-8 -*-
"""tests for create_app(): no-mssql 503 + with-mssql login form 渲染 + 路由注册.

不依赖真 142 / sglang. 复用 FastAPI TestClient.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from javert.web.api.main import create_app


@pytest.fixture(scope="module")
def app_no_mssql():
    return create_app(with_mssql=False)


@pytest.fixture(scope="module")
def app_with_mssql():
    return create_app(with_mssql=True)


@pytest.fixture
def client_no_mssql(app_no_mssql):
    return TestClient(app_no_mssql)


@pytest.fixture
def client_with_mssql(app_with_mssql):
    return TestClient(app_with_mssql)


# =========================================================
# no-mssql 模式
# =========================================================
def test_no_mssql_healthz(client_no_mssql):
    r = client_no_mssql.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "mssql": False}


def test_no_mssql_workbench_503(client_no_mssql):
    r = client_no_mssql.get("/workbench", follow_redirects=False)
    assert r.status_code == 503
    assert "工作台需要 SQL Server" in r.json()["error"]


def test_no_mssql_sse_503(client_no_mssql):
    r = client_no_mssql.get("/sse/reviews", follow_redirects=False)
    assert r.status_code == 503


def test_no_mssql_review_503(client_no_mssql):
    r = client_no_mssql.post("/review", json={"run_id": "x", "verdict": "V"})
    assert r.status_code == 503


def test_no_mssql_existing_api_health_still_works(client_no_mssql):
    r = client_no_mssql.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["with_mssql"] is False


def test_no_mssql_register_route_not_mounted(client_no_mssql):
    # /register 在 no-mssql 模式不注册 (auth router skipped)
    r = client_no_mssql.get("/register", follow_redirects=False)
    assert r.status_code in (404, 503)


# =========================================================
# with-mssql 模式 (登录页可独立渲染, 不需要 142)
# =========================================================
def test_with_mssql_login_page(client_with_mssql):
    r = client_with_mssql.get("/login", follow_redirects=False)
    assert r.status_code == 200
    assert "登录" in r.text
    assert 'name="username"' in r.text
    assert 'name="password"' in r.text


def test_with_mssql_register_page_closed_by_default(client_with_mssql):
    # 默认 allow_register=False; /register 返 403 + "注册已关闭" 页面
    r = client_with_mssql.get("/register", follow_redirects=False)
    assert r.status_code == 403
    assert "注册已关闭" in r.text or "联系运维" in r.text


def test_with_mssql_workbench_redirects_to_login(client_with_mssql):
    r = client_with_mssql.get("/workbench", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/workbench"


def test_with_mssql_workbench_patient_redirects(client_with_mssql):
    r = client_with_mssql.get("/workbench/J66252", follow_redirects=False)
    assert r.status_code == 302
    assert "next=/workbench/J66252" in r.headers["location"]


def test_with_mssql_dashboard_redirects(client_with_mssql):
    r = client_with_mssql.get("/dashboard", follow_redirects=False)
    assert r.status_code == 302


def test_with_mssql_export_redirects(client_with_mssql):
    r = client_with_mssql.get("/export?format=xlsx", follow_redirects=False)
    assert r.status_code == 302


def test_with_mssql_sse_api_returns_401(client_with_mssql):
    # API/SSE 端点未登录 → 401 JSON 而不是 302
    r = client_with_mssql.get("/sse/reviews", follow_redirects=False)
    assert r.status_code == 401
    assert r.json() == {"error": "未登录"}


def test_with_mssql_review_api_unauth_401(client_with_mssql):
    r = client_with_mssql.post(
        "/review",
        json={"run_id": "aud_abc", "verdict": "V"},
    )
    assert r.status_code == 401


def test_with_mssql_banner_dismiss_api_unauth_401(client_with_mssql):
    r = client_with_mssql.post("/api/banner/dismiss")
    assert r.status_code == 401


def test_with_mssql_raw_data_api_unauth_401(client_with_mssql):
    r = client_with_mssql.get("/api/patient/J66252/raw")
    assert r.status_code == 401


# =========================================================
# 路由注册检查
# =========================================================
def test_workbench_routes_registered_in_with_mssql_mode(app_with_mssql):
    paths = {getattr(r, "path", None) for r in app_with_mssql.routes}
    must_exist = {
        "/login", "/register", "/logout",
        "/workbench", "/workbench/{patient_id}",
        "/review", "/dashboard", "/export",
        "/sse/reviews", "/api/banner/dismiss",
        "/api/patient/{patient_id}/raw",
    }
    missing = must_exist - paths
    assert not missing, f"missing routes: {missing}"


def test_workbench_routes_not_in_no_mssql(app_no_mssql):
    paths = {getattr(r, "path", None) for r in app_no_mssql.routes}
    forbidden = {"/login", "/register", "/workbench", "/review",
                 "/dashboard", "/export", "/sse/reviews"}
    leaked = forbidden & paths
    assert not leaked, f"these routes leaked into no-mssql mode: {leaked}"


# =========================================================
# 静态资源
# =========================================================
def test_static_style_css_served(client_no_mssql):
    r = client_no_mssql.get("/static/style.css")
    assert r.status_code == 200
    assert "--primary" in r.text
    # 蓝色商务风核心 token
    assert "#1e40af" in r.text


def test_static_favicon_served(client_no_mssql):
    r = client_no_mssql.get("/static/favicon.svg")
    assert r.status_code == 200
    assert r.text.startswith("<svg")


def test_static_app_js_served(client_no_mssql):
    r = client_no_mssql.get("/static/app.js")
    assert r.status_code == 200
    assert "EventSource" in r.text  # SSE 订阅代码
