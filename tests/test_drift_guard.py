# -*- coding: utf-8 -*-
"""重跑漂移防护单测 (recover-deterministic-recall 4.1).

覆盖 D4 四路径: 老V新C拦截为I / C→C(及I→C)不拦 / 专家驳回放行 / 开关 off 回退.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from javert.audit.result import AuditResult
from javert.audit.run_id import new_run_id
from javert.store import result_persister as rp
from javert.store.audit_store import SqliteStore
from javert.store.result_persister import (
    DRIFT_TAG,
    _apply_drift_guard,
    _expert_rejected_violation,
    persist_one,
)


def _mk(verdict: str, *, rule_id="R063", patient_id="211440399", when=None) -> AuditResult:
    return AuditResult(
        run_id=new_run_id(),
        rule_id=rule_id,
        patient_id=patient_id,
        verdict=verdict,
        confidence=0.9,
        reasoning="r",
        started_at=when or datetime.now(timezone.utc),
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteStore:
    s = SqliteStore(tmp_path / "audit.sqlite")
    s.init_schema()
    yield s
    s.close()


def test_old_v_new_c_downgraded_to_inconclusive(store: SqliteStore):
    store.write(_mk("VIOLATION", when=datetime.now(timezone.utc) - timedelta(hours=1)))
    new = _mk("CLEAN")
    _apply_drift_guard(new, store, sql_enabled=False)
    assert new.verdict == "INCONCLUSIVE"
    assert new.gate_tag == DRIFT_TAG
    assert DRIFT_TAG in new.reasoning


def test_old_c_new_c_not_guarded(store: SqliteStore):
    store.write(_mk("CLEAN", when=datetime.now(timezone.utc) - timedelta(hours=1)))
    new = _mk("CLEAN")
    _apply_drift_guard(new, store, sql_enabled=False)
    assert new.verdict == "CLEAN" and new.gate_tag == ""


def test_old_i_new_c_not_guarded(store: SqliteStore):
    # 守护只针对老 V (老 I → C 不拦)
    store.write(_mk("INCONCLUSIVE", when=datetime.now(timezone.utc) - timedelta(hours=1)))
    new = _mk("CLEAN")
    _apply_drift_guard(new, store, sql_enabled=False)
    assert new.verdict == "CLEAN"


def test_no_prior_history_not_guarded(store: SqliteStore):
    new = _mk("CLEAN")
    _apply_drift_guard(new, store, sql_enabled=False)
    assert new.verdict == "CLEAN"


def test_expert_rejection_lets_clean_through(store: SqliteStore, monkeypatch):
    store.write(_mk("VIOLATION", when=datetime.now(timezone.utc) - timedelta(hours=1)))
    monkeypatch.setattr(rp, "_expert_rejected_violation", lambda run_id, sql_enabled: True)
    new = _mk("CLEAN")
    _apply_drift_guard(new, store, sql_enabled=True)
    assert new.verdict == "CLEAN" and new.gate_tag == ""


def test_expert_rejected_violation_no_sql_returns_false():
    assert _expert_rejected_violation("aud_whatever0000", sql_enabled=False) is False


def test_persist_one_off_switch_keeps_clean(store: SqliteStore, monkeypatch):
    # 开关 off → 落库行为回退 (老 V 新 C 照常落 CLEAN, 无标签)
    from javert import config as cfgmod

    monkeypatch.setenv("JAVERT_DRIFT_GUARD", "off")
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "false")
    cfgmod.reset_config_cache()
    try:
        store.write(_mk("VIOLATION", when=datetime.now(timezone.utc) - timedelta(hours=1)))
        new = _mk("CLEAN")
        persist_one(new, sqlite_store=store)
        assert new.verdict == "CLEAN" and new.gate_tag == ""
    finally:
        cfgmod.reset_config_cache()


def test_persist_one_on_downgrades_and_stores_inconclusive(store: SqliteStore, monkeypatch):
    from javert import config as cfgmod

    monkeypatch.setenv("JAVERT_DRIFT_GUARD", "on")
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "false")
    cfgmod.reset_config_cache()
    try:
        store.write(_mk("VIOLATION", when=datetime.now(timezone.utc) - timedelta(hours=1)))
        new = _mk("CLEAN")
        persist_one(new, sqlite_store=store)
        assert new.verdict == "INCONCLUSIVE"
        latest = store.find_by_rule_patient_latest(new.rule_id, new.patient_id)
        assert latest.verdict == "INCONCLUSIVE" and latest.gate_tag == DRIFT_TAG
        assert "待人工复核" in latest.headline
    finally:
        cfgmod.reset_config_cache()
