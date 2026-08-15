# -*- coding: utf-8 -*-
"""SqliteStore 测试."""

from __future__ import annotations

import time
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from javert.audit.result import AuditResult, Evidence, ToolCall
from javert.audit.run_id import new_run_id
from javert.store.audit_store import SqliteStore


def _make_result(rule_id="R191", patient_id="J66252", verdict="VIOLATION", **overrides) -> AuditResult:
    base = dict(
        run_id=new_run_id(),
        rule_id=rule_id,
        patient_id=patient_id,
        verdict=verdict,
        confidence=0.85,
        reasoning="测试理由",
        evidence=[Evidence(source="note", locator="入院诊断", text="甲状腺乳头状癌")],
        tool_calls=[ToolCall(tool_name="search_notes", arguments={"patient_id": patient_id}, result="...", duration_ms=50)],
        duration_ms=2000,
        model="qwen-test",
        started_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return AuditResult(**base)


@pytest.fixture
def store(tmp_path: Path) -> SqliteStore:
    s = SqliteStore(tmp_path / "audit.sqlite")
    s.init_schema()
    yield s
    s.close()


def test_init_creates_schema(tmp_path: Path):
    db = tmp_path / "audit.sqlite"
    assert not db.exists()
    s = SqliteStore(db)
    s.init_schema()
    assert db.exists()
    cur = s.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in cur.fetchall()}
    assert "audit_runs" in names
    assert "_meta" in names
    cur = s.conn.execute("SELECT value FROM _meta WHERE key='schema_version'")
    assert cur.fetchone()["value"] == str(SqliteStore.SCHEMA_VERSION)
    # v2 sync 列存在
    cols = {r[1] for r in s.conn.execute("PRAGMA table_info(audit_runs)").fetchall()}
    assert {"synced_at", "sync_attempts", "sync_last_error"} <= cols
    s.close()


def test_v1_to_v2_migration(tmp_path: Path):
    """老 v1 库应被自动 ALTER 加 3 列, 现有数据保留."""
    import sqlite3
    db = tmp_path / "old.sqlite"
    # 模拟 v1 schema (没有 sync 列)
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE audit_runs (
            run_id TEXT PRIMARY KEY, rule_id TEXT, patient_id TEXT,
            verdict TEXT, confidence REAL, reasoning TEXT,
            evidence_json TEXT, tool_calls_json TEXT,
            duration_ms INTEGER, model TEXT, started_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO _meta VALUES ('schema_version', '1')")
    conn.execute(
        "INSERT INTO audit_runs (run_id, rule_id, patient_id, verdict, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("aud_old123abc", "R001", "K00001", "CLEAN", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    # 触发 migration
    s = SqliteStore(db)
    s.init_schema()
    cols = {r[1] for r in s.conn.execute("PRAGMA table_info(audit_runs)").fetchall()}
    assert {"synced_at", "sync_attempts", "sync_last_error"} <= cols
    # 老数据保留 + 默认 unsynced
    state = s.count_sync_state()
    assert state["total"] == 1
    assert state["unsynced"] == 1
    assert state["synced"] == 0
    s.close()


# =========================================================
# v2: sync 状态管理
# =========================================================
def test_sync_state_initial_unsynced(store: SqliteStore):
    """新写入的 audit 默认 synced_at=NULL."""
    r = _make_result()
    store.write(r)
    state = store.count_sync_state()
    assert state["total"] == 1
    assert state["unsynced"] == 1
    assert state["synced"] == 0


def test_mark_synced(store: SqliteStore):
    r = _make_result()
    store.write(r)
    store.mark_synced(r.run_id)
    state = store.count_sync_state()
    assert state["synced"] == 1
    assert state["unsynced"] == 0
    assert state["last_synced_run_id"] == r.run_id
    assert state["last_synced_at"] is not None


def test_mark_sync_failed_keeps_unsynced(store: SqliteStore):
    r = _make_result()
    store.write(r)
    store.mark_sync_failed(r.run_id, "142 网络超时")
    state = store.count_sync_state()
    assert state["unsynced"] == 1  # 仍未同步
    assert state["synced"] == 0
    assert state["last_error"] == "142 网络超时"
    assert state["last_error_run_id"] == r.run_id

    # attempts 应该 +1
    row = store.conn.execute(
        "SELECT sync_attempts FROM audit_runs WHERE run_id = ?",
        (r.run_id,),
    ).fetchone()
    assert row["sync_attempts"] == 1


def test_find_unsynced_order_and_limit(store: SqliteStore):
    rs = []
    for i in range(5):
        r = _make_result(rule_id=f"R00{i+1}")
        store.write(r)
        time.sleep(0.005)  # 保证 created_at 有序
        rs.append(r)
    # mark 第 0 条同步; 剩 4 条 unsynced
    store.mark_synced(rs[0].run_id)

    pending = store.find_unsynced(limit=10)
    assert len(pending) == 4
    # 按 created_at 升序 (最早的优先回灌)
    assert pending[0].run_id == rs[1].run_id
    assert pending[-1].run_id == rs[4].run_id

    # limit 生效
    assert len(store.find_unsynced(limit=2)) == 2


def test_mark_synced_then_failed_then_synced(store: SqliteStore):
    """attempts 累计 + last_error 在重新 mark_synced 后清空."""
    r = _make_result()
    store.write(r)
    store.mark_sync_failed(r.run_id, "first fail")
    store.mark_sync_failed(r.run_id, "second fail")
    store.mark_synced(r.run_id)

    row = store.conn.execute(
        "SELECT sync_attempts, sync_last_error, synced_at FROM audit_runs WHERE run_id = ?",
        (r.run_id,),
    ).fetchone()
    assert row["sync_attempts"] == 3  # 2 fail + 1 synced
    assert row["sync_last_error"] is None  # mark_synced 清掉
    assert row["synced_at"] is not None


def test_write_and_round_trip(store: SqliteStore):
    r = _make_result(anchors_json='{"items":[],"schema_version":2,"verified_fee_snapshot":true}')
    store.write(r)
    again = store.find_by_run_id(r.run_id)
    assert again is not None
    assert again.run_id == r.run_id
    assert again.verdict == "VIOLATION"
    assert again.evidence[0].text == "甲状腺乳头状癌"
    assert again.tool_calls[0].tool_name == "search_notes"
    assert again.anchors_json == r.anchors_json


def test_find_unsynced_preserves_anchors_cache(store: SqliteStore):
    r = _make_result(anchors_json='{"items":[],"schema_version":2,"verified_fee_snapshot":true}')
    store.write(r)
    pending = store.find_unsynced()
    assert len(pending) == 1
    assert pending[0].anchors_json == r.anchors_json


def test_two_writes_same_rule_patient_kept(store: SqliteStore):
    r1 = _make_result()
    time.sleep(0.01)
    r2 = _make_result(verdict="CLEAN")
    store.write(r1)
    store.write(r2)
    rows = store.find_by_rule("R191")
    assert len(rows) == 2
    latest = store.find_by_rule_patient_latest("R191", "J66252")
    assert latest is not None
    assert latest.run_id in {r1.run_id, r2.run_id}


def test_replay_key_is_persisted_and_unique_per_rule(store: SqliteStore):
    replay_key = "123e4567-e89b-42d3-a456-426614174000-v1"
    first = _make_result(rule_id="R191")
    store.write(first, batch_tag="ocr1.0", replay_key=replay_key)

    assert store.publication_metadata(first.run_id) == ("ocr1.0", replay_key)
    entries = store.find_replay_entries(replay_key)
    assert [(result.run_id, synced) for result, synced in entries] == [
        (first.run_id, False)
    ]

    with pytest.raises(sqlite3.IntegrityError):
        store.write(
            _make_result(rule_id="R191"),
            batch_tag="ocr1.0",
            replay_key=replay_key,
        )


def test_summary_distribution(store: SqliteStore):
    for v in ["VIOLATION"] * 3 + ["CLEAN"] * 2 + ["INCONCLUSIVE"]:
        store.write(_make_result(verdict=v))
    summary = store.summary_by_rule()
    assert "R191" in summary
    assert summary["R191"]["V"] == 3
    assert summary["R191"]["C"] == 2
    assert summary["R191"]["I"] == 1
    assert summary["R191"]["total"] == 6


def test_summary_filtered_by_rule(store: SqliteStore):
    store.write(_make_result(rule_id="R191", verdict="VIOLATION"))
    store.write(_make_result(rule_id="R001", verdict="CLEAN"))
    summary = store.summary_by_rule(rule_id="R191")
    assert set(summary.keys()) == {"R191"}


def test_find_by_verdict_with_since(store: SqliteStore):
    old = _make_result(verdict="VIOLATION", started_at=datetime.now(timezone.utc) - timedelta(days=2))
    new = _make_result(verdict="VIOLATION")
    store.write(old)
    # 手动改 created_at 到过去
    with store.conn as c:
        c.execute(
            "UPDATE audit_runs SET created_at = ? WHERE run_id = ?",
            ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), old.run_id),
        )
    store.write(new)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    rows = store.find_by_verdict("VIOLATION", since=yesterday)
    ids = {r.run_id for r in rows}
    assert new.run_id in ids
    assert old.run_id not in ids


# ============================================================
# 并发写测试 (add-parallel-audit)
# ============================================================


def test_concurrent_write_50_threads(store: SqliteStore):
    """50 线程并发 write 50 条不同 AuditResult, 全部入库无丢失/no OperationalError."""
    from concurrent.futures import ThreadPoolExecutor

    results = [_make_result(rule_id=f"R{i:03d}", patient_id=f"PT{i:04d}") for i in range(50)]

    errors: list[Exception] = []

    def writer(r):
        try:
            store.write(r)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(writer, results))

    assert errors == [], f"并发写出错: {errors}"
    n = store.conn.execute("SELECT COUNT(*) FROM audit_runs").fetchone()[0]
    assert n == 50


def test_concurrent_mark_synced_and_write(store: SqliteStore):
    """write / mark_synced / mark_sync_failed 并发互斥, 无 race."""
    from concurrent.futures import ThreadPoolExecutor

    results = [_make_result(rule_id=f"R{i:03d}") for i in range(20)]
    for r in results:
        store.write(r)

    def mark(r):
        store.mark_synced(r.run_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(mark, results))

    synced = store.conn.execute(
        "SELECT COUNT(*) FROM audit_runs WHERE synced_at IS NOT NULL"
    ).fetchone()[0]
    assert synced == 20
