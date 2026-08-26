# -*- coding: utf-8 -*-
"""SQL Server headline nullable 双写、读取和 replay 去重回归。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from javert.audit.result import AuditResult
from javert.store.audit_store import SqliteStore
from javert.store.sqlserver_store import SqlServerStore


def _result() -> AuditResult:
    return AuditResult(
        run_id="aud_SQLHEADLINE1",
        rule_id="R191",
        patient_id="CASE-DEID-SQL",
        verdict="VIOLATION",
        confidence=0.91,
        headline="脑功能成像项目存在重复收费，现有证据支持违规结论",
        reasoning="去标识完整推理",
        started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _engine(connection: MagicMock) -> MagicMock:
    engine = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = connection
    context.__exit__.return_value = False
    engine.connect.return_value = context
    return engine


def test_sqlserver_write_and_full_read_preserve_same_headline(monkeypatch):
    result = _result()
    write_conn = MagicMock()
    missing = MagicMock()
    missing.fetchone.return_value = None
    write_conn.execute.side_effect = [missing, MagicMock()]
    store = SqlServerStore()
    monkeypatch.setattr(store, "get_engine", lambda: _engine(write_conn))

    assert store.write_audit(result) is True
    params = write_conn.execute.call_args_list[1].args[1]
    assert params["headline"] == result.headline

    read_conn = MagicMock()
    query = MagicMock()
    query.fetchone.return_value = (
        result.run_id,
        result.rule_id,
        result.patient_id,
        result.verdict,
        result.confidence,
        result.reasoning,
        "[]",
        "[]",
        0,
        "offline",
        result.started_at,
        "",
        None,
        None,
        None,
        result.headline,
    )
    read_conn.execute.return_value = query
    reader = SqlServerStore()
    monkeypatch.setattr(reader, "get_engine", lambda: _engine(read_conn))
    loaded = reader.find_audit_by_run_id(result.run_id)
    assert loaded is not None
    assert loaded.headline == result.headline
    assert loaded.reasoning == result.reasoning


def test_sqlite_and_sqlserver_dual_write_use_identical_headline(tmp_path, monkeypatch):
    result = _result()
    sqlite_store = SqliteStore(tmp_path / "audit.sqlite")
    sqlite_store.init_schema()
    sqlite_store.write(result)

    conn = MagicMock()
    missing = MagicMock()
    missing.fetchone.return_value = None
    conn.execute.side_effect = [missing, MagicMock()]
    sqlserver_store = SqlServerStore()
    monkeypatch.setattr(sqlserver_store, "get_engine", lambda: _engine(conn))
    assert sqlserver_store.write_audit(result) is True

    sqlserver_params = conn.execute.call_args_list[1].args[1]
    assert sqlite_store.find_by_run_id(result.run_id).headline == sqlserver_params["headline"]
    sqlite_store.close()


def test_sqlserver_old_null_headline_reads_as_empty(monkeypatch):
    result = _result()
    conn = MagicMock()
    query = MagicMock()
    query.fetchone.return_value = (
        result.run_id, result.rule_id, result.patient_id, "CLEAN", 0.9,
        result.reasoning, "[]", "[]", 0, "offline", result.started_at,
        "", None, None, None, None,
    )
    conn.execute.return_value = query
    store = SqlServerStore()
    monkeypatch.setattr(store, "get_engine", lambda: _engine(conn))
    loaded = store.find_audit_by_run_id(result.run_id)
    assert loaded is not None and loaded.headline == ""


def test_sqlserver_duplicate_replay_key_skips_insert_without_mutating_result(monkeypatch):
    result = _result()
    conn = MagicMock()
    missing_run = MagicMock()
    missing_run.fetchone.return_value = None
    existing_replay = MagicMock()
    existing_replay.fetchone.return_value = ("aud_EXISTING001",)
    conn.execute.side_effect = [missing_run, existing_replay]
    store = SqlServerStore()
    monkeypatch.setattr(store, "get_engine", lambda: _engine(conn))

    assert store.write_audit(result, replay_key="deid-replay-v1") is True
    assert conn.execute.call_count == 2
    assert result.headline == "脑功能成像项目存在重复收费，现有证据支持违规结论"


def test_sqlserver_headline_ddl_is_nullable_and_idempotent():
    ddl = (
        Path(__file__).parents[1] / "scripts" / "sql" / "create_javert_tables.sql"
    ).read_text(encoding="utf-8")
    assert "headline            NVARCHAR(120)  NULL" in ddl
    assert "ALTER TABLE javert_audit_runs ADD headline NVARCHAR(120) NULL" in ddl
    assert "WHERE Name = N'headline'" in ddl
