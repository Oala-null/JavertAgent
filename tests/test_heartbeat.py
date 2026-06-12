# -*- coding: utf-8 -*-
"""SyncWorker 心跳 + 回灌单测.

mock SqlServerStore 模拟 142 不通 / 通 / 写入失败三种场景.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from javert.audit.result import AuditResult, Evidence, ToolCall
from javert.audit.run_id import new_run_id
from javert.store.audit_store import SqliteStore


def _make_result(rule_id="R001", patient_id="K00001", verdict="CLEAN") -> AuditResult:
    return AuditResult(
        run_id=new_run_id(),
        rule_id=rule_id,
        patient_id=patient_id,
        verdict=verdict,
        confidence=0.9,
        reasoning="test",
        evidence=[Evidence(source="note", locator="L1", text="t")],
        tool_calls=[ToolCall(tool_name="search_notes", arguments={}, result="r", duration_ms=10)],
        duration_ms=100,
        model="test",
        started_at=datetime.now(timezone.utc),
    )


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """每个测试隔离 config + sqlserver_store 单例."""
    for k in list(os.environ):
        if k.startswith("JAVERT_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "true")
    monkeypatch.setenv("JAVERT_AUDIT_DB", str(tmp_path / "audit.sqlite"))

    from javert.config import reset_config_cache
    reset_config_cache()
    from javert.store.sqlserver_store import reset_sqlserver_store
    reset_sqlserver_store()
    yield
    reset_config_cache()
    reset_sqlserver_store()


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "audit.sqlite")
    s.init_schema()
    yield s
    s.close()


def _seed_unsynced(store: SqliteStore, n: int) -> list[AuditResult]:
    out = []
    for i in range(n):
        r = _make_result(rule_id=f"R00{i+1}")
        store.write(r)
        out.append(r)
    return out


def test_tick_when_142_down(store, monkeypatch):
    """142 不通时, tick 不会回灌, last_health 报 false."""
    _seed_unsynced(store, 3)

    fake_sql = MagicMock()
    fake_sql.health_check.return_value = {"sql_server": False, "error": "down"}
    fake_sql.write_audit = MagicMock(return_value=False)
    monkeypatch.setattr(
        "javert.web.api.heartbeat.get_sqlserver_store",
        lambda: fake_sql,
    )

    from javert.web.api.heartbeat import SyncWorker
    w = SyncWorker(interval_s=999, batch_size=10)
    snap = asyncio.run(w.tick_once())

    assert snap["last_health"]["sql_server"] is False
    assert snap["last_synced_count"] == 0
    fake_sql.write_audit.assert_not_called()
    assert store.count_sync_state()["unsynced"] == 3  # 都还在


def test_tick_when_142_up_replays_all(store, monkeypatch):


    """142 通 + write_audit 全部成功 → 全部 mark_synced."""
    _seed_unsynced(store, 3)

    fake_sql = MagicMock()
    fake_sql.health_check.return_value = {"sql_server": True, "host": "fake", "database": "x"}
    fake_sql.write_audit.return_value = True
    monkeypatch.setattr(
        "javert.web.api.heartbeat.get_sqlserver_store",
        lambda: fake_sql,
    )

    from javert.web.api.heartbeat import SyncWorker
    w = SyncWorker(interval_s=999, batch_size=10)
    snap = asyncio.run(w.tick_once())

    assert snap["last_synced_count"] == 3
    assert snap["last_failed_count"] == 0
    assert fake_sql.write_audit.call_count == 3
    state = store.count_sync_state()
    assert state["synced"] == 3
    assert state["unsynced"] == 0


def test_tick_partial_failure(store, monkeypatch):
    """142 通但部分写入失败 → 失败的 mark_sync_failed, 留下次 retry."""
    _seed_unsynced(store, 3)

    fake_sql = MagicMock()
    fake_sql.health_check.return_value = {"sql_server": True}
    # 第 1, 3 条成功, 第 2 条失败
    fake_sql.write_audit.side_effect = [True, False, True]
    monkeypatch.setattr(
        "javert.web.api.heartbeat.get_sqlserver_store",
        lambda: fake_sql,
    )

    from javert.web.api.heartbeat import SyncWorker
    w = SyncWorker(interval_s=999, batch_size=10)
    snap = asyncio.run(w.tick_once())

    assert snap["last_synced_count"] == 2
    assert snap["last_failed_count"] == 1
    state = store.count_sync_state()
    assert state["synced"] == 2
    assert state["unsynced"] == 1
    assert state["last_error"] is not None


def test_tick_batch_size(store, monkeypatch):
    """batch_size 限制每次回灌条数, 多次 tick 累计."""
    _seed_unsynced(store, 5)

    fake_sql = MagicMock()
    fake_sql.health_check.return_value = {"sql_server": True}
    fake_sql.write_audit.return_value = True
    monkeypatch.setattr(
        "javert.web.api.heartbeat.get_sqlserver_store",
        lambda: fake_sql,
    )

    from javert.web.api.heartbeat import SyncWorker
    w = SyncWorker(interval_s=999, batch_size=2)
    asyncio.run(w.tick_once())
    assert store.count_sync_state()["synced"] == 2

    asyncio.run(w.tick_once())
    assert store.count_sync_state()["synced"] == 4

    asyncio.run(w.tick_once())
    assert store.count_sync_state()["synced"] == 5


def test_tick_skipped_when_sql_disabled(store, monkeypatch):
    """sql_enabled=false → tick 不打 142 也不回灌."""
    _seed_unsynced(store, 2)
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "false")
    from javert.config import reset_config_cache
    reset_config_cache()

    fake_sql = MagicMock()
    fake_sql.health_check.return_value = {"sql_server": False, "error": "disabled"}
    fake_sql.write_audit = MagicMock()
    monkeypatch.setattr(
        "javert.web.api.heartbeat.get_sqlserver_store",
        lambda: fake_sql,
    )

    from javert.web.api.heartbeat import SyncWorker
    w = SyncWorker(interval_s=999, batch_size=10)
    snap = asyncio.run(w.tick_once())

    assert snap["last_synced_count"] == 0
    fake_sql.write_audit.assert_not_called()
