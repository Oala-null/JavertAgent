"""工作台性能回归：只用合成患者，走真实路由、模板和查询。"""
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event

from javert.store.models import PatientSidebarItem, RunWithReviews, SinceLastLoginStats, User
from javert.store.sqlserver_store import SqlServerStore
from javert.web.api import routes_workbench as rw

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


@pytest.fixture
def synthetic_workbench(monkeypatch):
    patients = [PatientSidebarItem(patient_id=f"CASE-{i:05}", v_count=1,
                relevant_count=1, fees_sum=i * 100, primary_dx=f"合成诊断{i % 3}",
                updated_at=NOW, batch_tag="SYNTHETIC" if i % 2 else None)
                for i in range(1200)]
    calls = []
    def list_patients(filter_mode):
        calls.append(filter_mode)
        return patients
    store = SimpleNamespace(
        list_patients_with_violations=list_patients,
        list_runs_for_patient=lambda patient_id, filter_mode: [RunWithReviews(
            run_id="aud_SYNTHETIC", rule_id="R191", patient_id=patient_id,
            verdict="VIOLATION", confidence=0.9, reasoning="合成结论", created_at=NOW)],
        fetch_anchors_for_patient=lambda patient_id: {},
        get_since_last_login_stats=lambda **kwargs: SinceLastLoginStats(),
    )
    monkeypatch.setattr(rw, "get_sqlserver_store", lambda: store)
    monkeypatch.setattr(rw, "current_user", lambda req: User(id=1, username="SYNTHETIC", created_at=NOW))
    monkeypatch.setattr(rw, "_enrich_sidebar", lambda rows: rows)
    monkeypatch.setattr(rw, "_get_loader", lambda: SimpleNamespace(get_notes=lambda pid: None, get_fees=lambda pid: None))
    if hasattr(rw, "_get_profile_hub_source_for_patient"):
        monkeypatch.setattr(rw, "_get_profile_hub_source_for_patient", lambda pid: None)
    monkeypatch.setattr(rw, "build_overview", lambda *args: None)
    monkeypatch.setattr(rw, "_resolve_hits_for_runs", lambda *args: {})
    monkeypatch.setattr(rw, "load_rule_meta", lambda: {})
    monkeypatch.setattr(rw, "get_config", lambda: SimpleNamespace(hub_raw_enabled=False))
    rw._sidebar_cache.clear()
    app = FastAPI()
    from starlette.middleware.sessions import SessionMiddleware
    app.add_middleware(SessionMiddleware, secret_key="SYNTHETIC-test-session")
    app.include_router(rw.router)
    yield TestClient(app), patients, calls
    rw._sidebar_cache.clear()


def test_patient_first_response_does_not_query_or_render_other_patients(synthetic_workbench):
    client, _, calls = synthetic_workbench
    response = client.get("/workbench/CASE-00000")
    assert response.status_code == 200
    print(f"detail_bytes={len(response.content)} sidebar_queries={len(calls)} cards={response.text.count('class=\"patient-card')}")
    assert not calls, "当前患者首屏不应等待全患者侧栏查询"
    assert "CASE-01199" not in response.text
    assert "aud_SYNTHETIC" in response.text


@pytest.fixture
def synthetic_sql_store(monkeypatch):
    engine = create_engine("sqlite://")
    # SQLite 同样执行 window/join/CASE，只移除 SQL Server Unicode 字面量前缀。
    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def unicode_literals(conn, cursor, statement, parameters, context, executemany):
        return re.sub(r"(?<![\w'])N'", "'", statement), parameters
    with engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TABLE javert_audit_runs (
            id INTEGER PRIMARY KEY, run_id TEXT, rule_id TEXT, patient_id TEXT,
            verdict TEXT, confidence REAL, reasoning TEXT, evidence_json TEXT,
            tool_calls_json TEXT, duration_ms INTEGER, model TEXT, started_at TEXT,
            created_at TEXT, triggered_by TEXT, batch_tag TEXT, gate_tag TEXT,
            eligibility_json TEXT, promise_trace_json TEXT, headline TEXT, anchors_json TEXT)""")
        conn.exec_driver_sql("CREATE TABLE javert_vio_review (id INTEGER, run_id TEXT, user_id INTEGER, review_verdict TEXT, comment TEXT, created_at TEXT, is_latest INTEGER)")
        conn.exec_driver_sql("CREATE TABLE javert_users (id INTEGER, username TEXT, display_name TEXT)")
        for i in range(8):
            conn.exec_driver_sql("""INSERT INTO javert_audit_runs
                (id, run_id, rule_id, patient_id, verdict, confidence, reasoning, evidence_json,
                 tool_calls_json, created_at, anchors_json) VALUES (?, ?, 'R191', 'CASE-00000', 'VIOLATION', 0.9, ?, '[]', ?, ?, ?)""",
                (i + 1, f"aud_SYNTHETIC_{i}", "合成推理" * 1024,
                 '["' + 'x' * 65536 + '"]', f"2026-09-{i+1:02}T00:00:00+00:00", '["SYNTHETIC"]'))
        conn.exec_driver_sql("INSERT INTO javert_users VALUES (1, 'SYNTHETIC', '合成专家')")
        conn.exec_driver_sql("INSERT INTO javert_vio_review VALUES (1, 'aud_SYNTHETIC_0', 1, 'C', '历史批注应保留', '2026-09-01T00:00:00+00:00', 1)")
    transferred = []
    class Connection:
        def __enter__(self):
            self.conn = engine.connect()
            return self
        def __exit__(self, *args): self.conn.close()
        def execute(self, statement, params):
            rows = self.conn.execute(statement, params).fetchall()
            transferred.extend(rows)
            return SimpleNamespace(fetchall=lambda: rows)
    store = SqlServerStore()
    monkeypatch.setattr(store, "get_engine", lambda: SimpleNamespace(connect=Connection))
    yield store, engine, transferred
    engine.dispose()


def test_history_keeps_reviews_without_transferring_large_payloads(synthetic_sql_store):
    store, _, transferred = synthetic_sql_store
    runs = store.list_runs_for_patient("CASE-00000", "all")
    assert len(runs) == 1 and len(runs[0].history) == 7
    assert runs[0].run_id == "aud_SYNTHETIC_7"
    assert runs[0].tool_calls_json and runs[0].reasoning
    assert runs[0].history[-1].reviews[0].comment == "历史批注应保留"
    payloads = [value for row in transferred for value in row
                if isinstance(value, str) and len(value) > 60000]
    print(f"large_tool_payloads_transferred={len(payloads)}")
    assert len(payloads) == 1, "只有最新主卡需要完整工具调用记录"


def test_sidebar_filters_whole_cohort_before_paging(synthetic_workbench):
    client, _, calls = synthetic_workbench
    data = client.get("/api/workbench/patients?page=2").json()
    assert (data["page"], data["pages"], data["total"]) == (2, 24, 1200)
    assert data["html"].count('class="patient-card') == 50
    assert "CASE-00050" in data["html"] and "CASE-00000" not in data["html"]
    # 原来不在首屏的病例仍能被全名单搜索和排序找到。
    data = client.get("/api/workbench/patients?pid=CASE-01199&page=99").json()
    assert data["total"] == 1 and data["page"] == 1
    assert "CASE-01199" in data["html"]
    data = client.get("/api/workbench/patients?dx=合成诊断2&fee=gt5&tag=SYNTHETIC&sort=fee_desc").json()
    assert data["total"] == 117
    assert data["html"].index("CASE-01199") < data["html"].index("CASE-01193")
    assert calls == ["v_and_i"], "翻页与筛选应复用轻量摘要"


def test_sidebar_empty_invalid_and_unauthorized(synthetic_workbench, monkeypatch):
    client, _, _ = synthetic_workbench
    response = client.get("/api/workbench/patients?pid=CASE-ABSENT")
    assert response.json()["total"] == 0
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/workbench/patients?page=0").status_code == 422
    assert client.get("/api/workbench/patients?sort=invalid").status_code == 422
    monkeypatch.setattr(rw, "current_user", lambda request: None)
    assert client.get("/api/workbench/patients").status_code == 401


def test_sidebar_time_filter_and_escaping(synthetic_workbench):
    client, patients, _ = synthetic_workbench
    patients[0].primary_dx = '<img src=x onerror="alert(1)">'
    data = client.get("/api/workbench/patients?pid=CASE-00000").json()
    assert "<img" not in data["html"] and "&lt;img" in data["html"]
    assert client.get("/api/workbench/patients?updated_since=2026-09-29T00:00:00Z").json()["total"] == 0
    assert client.get("/api/workbench/patients?updated_since=2026-09-27T00:00:00Z").json()["total"] == 1200


def test_history_latest_tie_filter_and_patient_isolation(synthetic_sql_store):
    store, engine, transferred = synthetic_sql_store
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE javert_audit_runs SET created_at = '2026-09-28T00:00:00Z', verdict = CASE WHEN id = 8 THEN 'CLEAN' ELSE 'VIOLATION' END")
        conn.exec_driver_sql("INSERT INTO javert_audit_runs (id, run_id, rule_id, patient_id, verdict, confidence, created_at) VALUES (100, 'aud_OTHER', 'R191', 'CASE-OTHER', 'VIOLATION', 0.9, '2026-09-30T00:00:00Z')")
    assert store.list_runs_for_patient("CASE-00000", "v_only") == []
    runs = store.list_runs_for_patient("CASE-00000", "all")
    assert runs[0].run_id == "aud_SYNTHETIC_7"
    assert len(runs[0].history) == 7 and runs[0].history[-1].reviews
    assert all(row[0] != "aud_OTHER" for row in transferred)
    assert store.fetch_anchors_for_patient("CASE-00000") == {"aud_SYNTHETIC_7": '["SYNTHETIC"]'}


def test_index_first_response_does_not_load_sidebar(synthetic_workbench):
    client, _, calls = synthetic_workbench
    response = client.get("/workbench")
    assert response.status_code == 200 and not calls
    assert 'class="patient-card' not in response.text


def test_sidebar_cache_ttl_starts_after_slow_query_finishes(monkeypatch):
    clock = [0.0]
    calls = []
    def slow_query(filter_mode):
        calls.append(filter_mode)
        clock[0] += 15.0
        return []
    monkeypatch.setattr(rw.time, 'monotonic', lambda: clock[0])
    store = SimpleNamespace(list_patients_with_violations=slow_query)
    rw._sidebar_cache.clear()
    try:
        rw._sidebar_patients(store, 'all', allow_cached=True)
        rw._sidebar_patients(store, 'all', allow_cached=True)
        assert calls == ['all'], '慢查询完成后应享有完整 TTL，不能刚填充就过期'
    finally:
        rw._sidebar_cache.clear()
