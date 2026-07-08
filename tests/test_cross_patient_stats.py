# -*- coding: utf-8 -*-
"""跨患者统计 (add-cross-patient-stats) — 纯函数阈值判定 + sqlite latest 去重."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from javert.audit.result import AuditResult, Evidence
from javert.audit.run_id import new_run_id
from javert.stats.cross_patient import (
    RuleAgg,
    Thresholds,
    aggregate,
    compute_rule_stats,
)
from javert.store.audit_store import SqliteStore


# ---------------- 纯函数 ----------------

def test_aggregate_counts_and_v_patients():
    rows = [
        ("R1", "P1", "VIOLATION"),
        ("R1", "P2", "CLEAN"),
        ("R1", "P3", "VIOLATION"),
        ("R2", "P1", "INCONCLUSIVE"),
    ]
    aggs = {a.rule_id: a for a in aggregate(rows)}
    assert aggs["R1"].n_patients == 3
    assert aggs["R1"].v == 2
    assert aggs["R1"].c == 1
    assert sorted(aggs["R1"].v_patients) == ["P1", "P3"]
    assert aggs["R2"].n_patients == 1
    assert aggs["R2"].i == 1


def test_v_rate_math():
    stat = compute_rule_stats([RuleAgg("R1", 8, 3, 1, 4, [])], Thresholds())[0]
    assert abs(stat.v_rate - 0.375) < 1e-9
    assert abs(stat.i_rate - 0.125) < 1e-9


def test_systemic_meets_threshold():
    stat = compute_rule_stats([RuleAgg("R1", 20, 12, 0, 8, [])], Thresholds())[0]
    assert stat.systemic is True
    assert "60%" in stat.reason


def test_systemic_exactly_at_boundary_inclusive():
    # v_rate == 0.5 且 n == 10, 两个 >= 都恰好等于 → 判系统性
    stat = compute_rule_stats([RuleAgg("R1", 10, 5, 0, 5, [])], Thresholds())[0]
    assert stat.systemic is True


def test_sample_too_small():
    stat = compute_rule_stats([RuleAgg("R1", 5, 4, 0, 1, [])], Thresholds())[0]
    assert stat.systemic is False
    assert "样本不足" in stat.reason


def test_below_v_rate_not_systemic():
    stat = compute_rule_stats([RuleAgg("R1", 20, 4, 0, 16, [])], Thresholds())[0]
    assert stat.systemic is False
    assert "< 阈值" in stat.reason


def test_override_raises_v_rate_bar():
    th = Thresholds(overrides={"R1": {"v_rate": 0.7}})
    stat = compute_rule_stats([RuleAgg("R1", 20, 12, 0, 8, [])], th)[0]  # 0.6 < 0.7
    assert stat.systemic is False


def test_override_lowers_min_patients():
    aggs = [RuleAgg("R1", 8, 6, 0, 2, [])]  # v_rate 0.75, n=8
    assert compute_rule_stats(aggs, Thresholds())[0].systemic is False  # 默认 min 10
    th = Thresholds(overrides={"R1": {"min_patients": 5}})
    assert compute_rule_stats(aggs, th)[0].systemic is True


def test_sorted_by_v_rate_desc():
    aggs = [RuleAgg("Rlow", 10, 2, 0, 8, []), RuleAgg("Rhigh", 10, 9, 0, 1, [])]
    stats = compute_rule_stats(aggs, Thresholds())
    assert [s.rule_id for s in stats] == ["Rhigh", "Rlow"]


# ---------------- sqlite latest 去重 ----------------

def _result(rule_id, patient_id, verdict) -> AuditResult:
    return AuditResult(
        run_id=new_run_id(),
        rule_id=rule_id,
        patient_id=patient_id,
        verdict=verdict,
        confidence=0.8,
        reasoning="t",
        evidence=[Evidence(source="note", locator="x", text="y")],
        tool_calls=[],
        duration_ms=1,
        model="m",
        started_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteStore:
    s = SqliteStore(tmp_path / "audit.sqlite")
    s.init_schema()
    yield s
    s.close()


def test_latest_verdict_rows_dedups_by_created_at(store: SqliteStore):
    # 同 rule+patient 两条: 旧 CLEAN / 新 VIOLATION → latest 取 VIOLATION
    store.write(_result("R001", "P1", "CLEAN"))
    store.write(_result("R001", "P1", "VIOLATION"))
    store.conn.execute("UPDATE audit_runs SET created_at=? WHERE verdict='CLEAN'", ("2026-01-01 00:00:00",))
    store.conn.execute("UPDATE audit_runs SET created_at=? WHERE verdict='VIOLATION'", ("2026-06-01 00:00:00",))
    store.conn.commit()
    rows = store.latest_verdict_rows()
    assert rows == [("R001", "P1", "VIOLATION")]


def test_latest_verdict_rows_batch_tag_filter(store: SqliteStore):
    store.write(_result("R001", "P1", "VIOLATION"), batch_tag="b1")
    store.write(_result("R002", "P2", "CLEAN"))  # 无 tag
    only_b1 = store.latest_verdict_rows(batch_tag="b1")
    assert only_b1 == [("R001", "P1", "VIOLATION")]
    all_rows = store.latest_verdict_rows()
    assert len(all_rows) == 2
