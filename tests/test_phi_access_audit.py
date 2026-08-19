# -*- coding: utf-8 -*-
"""harden-onsite-redlines Task 1.4: raw 端点留痕 / 限流 429 / session secret fail-fast.

不连真 142: fake store 记 log_action; loader/主诊/labs 全 monkeypatch.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from javert.config import JavertConfig, get_config, reset_config_cache
from javert.store.models import User
from javert.web.api import routes_workbench as rw
from javert.web.api.main import create_app


# =========================================================
# fakes / helpers
# =========================================================
class _FakeStore:
    def __init__(self):
        self.logs: list[dict] = []

    def log_action(self, **kw):
        self.logs.append(kw)

    def get_user_by_id(self, uid: int) -> User:
        return User(id=uid, username=f"u{uid}",
                    created_at=datetime.now(timezone.utc))

    def latest_batch_tag_for_patient(self, _patient_id: str) -> None:
        """No batch-profile routing in this CSV-only raw-access fixture."""
        return None

    def list_model_comparison_runs(self, patient_id: str, batch_tag: str):
        base = {
            "patient_id": patient_id,
            "rule_id": "R191",
            "confidence": 0.9,
            "reasoning": "合成推理",
            "evidence": [],
            "tool_calls": [],
            "duration_ms": 1200,
            "created_at": datetime.now(timezone.utc),
            "batch_tag": batch_tag,
            "gate_tag": "",
        }
        return [
            {**base, "run_id": "aud_compare_q36", "verdict": "CLEAN",
             "model": "Qwen/Qwen3.6-35B-A3B-FP8"},
            {**base, "run_id": "aud_compare_q38", "verdict": "INCONCLUSIVE",
             "model": "Qwen/Qwen3.8-27B-FP8"},
        ]


class _FakeLoader:
    """一行文书 + 空费用 → 走 csv 源, 不 404."""

    def get_fees(self, pid):
        return pd.DataFrame()

    def get_notes(self, pid):
        return pd.DataFrame([{
            "住院号": pid, "事件时间": "2026-01-01", "阶段": "入院记录",
            "子阶段": "主诉", "内容": "测试", "来源文件": "test",
        }])


def _session_cookie(uid: int) -> str:
    """按 starlette SessionMiddleware 格式手工签 session cookie."""
    secret = get_config().session_secret
    data = base64.b64encode(json.dumps(
        {"user_id": uid, "prev_last_login": "first_login"}).encode())
    return TimestampSigner(str(secret)).sign(data).decode()


@pytest.fixture
def raw_client(monkeypatch):
    """with_mssql app + fake store/loader; 返回 (client, store, login(uid))."""
    store = _FakeStore()
    monkeypatch.setattr(rw, "get_sqlserver_store", lambda: store)
    monkeypatch.setattr("javert.web.auth.get_sqlserver_store", lambda: store)
    monkeypatch.setattr(rw, "_get_loader", lambda: _FakeLoader())
    monkeypatch.setattr(rw, "_get_main_diagnosis", lambda pid: "测试主诊")
    monkeypatch.setattr(rw, "_labs_to_list", lambda pid: [])
    monkeypatch.setattr(rw, "_exams_to_list", lambda pid: [])
    client = TestClient(create_app(with_mssql=True))

    def login(uid: int):
        client.cookies.set(get_config().session_cookie_name, _session_cookie(uid))

    return client, store, login


# =========================================================
# 留痕
# =========================================================
def test_raw_access_logged(raw_client):
    client, store, login = raw_client
    login(101)
    r = client.get("/api/patient/JTEST01/raw")
    assert r.status_code == 200
    hits = [l for l in store.logs if l["action"] == "raw_access"]
    assert len(hits) == 1
    assert hits[0]["target_id"] == "JTEST01"
    assert hits[0]["user_id"] == 101
    assert hits[0]["payload"]["source"] == "csv"


def test_raw_access_log_failure_does_not_block(raw_client, monkeypatch):
    client, store, login = raw_client
    login(102)

    def _boom(**kw):
        raise RuntimeError("142 down")

    monkeypatch.setattr(store, "log_action", _boom)
    r = client.get("/api/patient/JTEST02/raw")
    assert r.status_code == 200  # 留痕失败不阻断响应


def test_model_compare_is_authenticated_rendered_and_logged(raw_client):
    client, store, login = raw_client
    login(103)
    response = client.get(
        "/workbench/CASE-AB-001/model-compare?batch_tag=ab3.8"
    )
    assert response.status_code == 200
    assert "双模型逐规则对比" in response.text
    assert "Qwen/Qwen3.6-35B-A3B-FP8" in response.text
    assert "Qwen/Qwen3.8-27B-FP8" in response.text
    logs = [item for item in store.logs if item["action"] == "model_compare_access"]
    assert len(logs) == 1
    assert logs[0]["target_id"] == "CASE-AB-001"
    assert logs[0]["payload"] == {"batch_tag": "ab3.8"}


# =========================================================
# 限流
# =========================================================
def test_raw_rate_limited_429_and_logged(raw_client, monkeypatch):
    client, store, login = raw_client
    monkeypatch.setenv("JAVERT_RAW_RATE_LIMIT", "3/minute")
    reset_config_cache()
    try:
        login(4242)  # 独立 uid, 不吃其他测试的窗口
        codes = [client.get(f"/api/patient/P{i}/raw").status_code for i in range(4)]
        assert codes[:3] == [200, 200, 200]
        assert codes[3] == 429
        # 429 请求同样留痕 (source=rate_limited), 可事后追查
        rl = [l for l in store.logs if l["payload"].get("source") == "rate_limited"]
        assert len(rl) == 1
        assert rl[0]["target_id"] == "P3"
        assert rl[0]["user_id"] == 4242
    finally:
        reset_config_cache()


def test_raw_rate_limit_keyed_per_session(raw_client, monkeypatch):
    client, store, login = raw_client
    monkeypatch.setenv("JAVERT_RAW_RATE_LIMIT", "2/minute")
    reset_config_cache()
    try:
        login(5001)
        assert client.get("/api/patient/A/raw").status_code == 200
        assert client.get("/api/patient/B/raw").status_code == 200
        assert client.get("/api/patient/C/raw").status_code == 429
        login(5002)  # 换会话 → 新窗口
        assert client.get("/api/patient/D/raw").status_code == 200
    finally:
        reset_config_cache()


# =========================================================
# session secret fail-fast (create_app 单一来源)
# =========================================================
_DEFAULT_SECRET = JavertConfig.model_fields["session_secret"].default


def test_create_app_fail_fast_on_default_secret(monkeypatch):
    monkeypatch.setenv("JAVERT_SESSION_SECRET", _DEFAULT_SECRET)
    reset_config_cache()
    try:
        with pytest.raises(RuntimeError, match="JAVERT_SESSION_SECRET"):
            create_app(with_mssql=True)  # uvicorn 直起同路径 → 同样拦截
    finally:
        reset_config_cache()


def test_create_app_dev_mode_allows_default_secret(monkeypatch):
    monkeypatch.setenv("JAVERT_SESSION_SECRET", _DEFAULT_SECRET)
    reset_config_cache()
    try:
        app = create_app(with_mssql=False)  # --no-mssql dev 形态放行
        assert app is not None
    finally:
        reset_config_cache()
