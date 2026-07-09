# -*- coding: utf-8 -*-
"""drift_report.find_drifts 单测 (recover-deterministic-recall 4.2)."""

from __future__ import annotations

from scripts.drift_report import find_drifts


def test_old_v_current_c_is_drift():
    rows = [
        ("R063", "P1", "aud_1", "VIOLATION", "2026-07-01", "b1"),
        ("R063", "P1", "aud_2", "INCONCLUSIVE", "2026-07-05", "b2"),
        ("R063", "P1", "aud_3", "CLEAN", "2026-07-08", "b3"),
    ]
    d = find_drifts(rows)
    assert len(d) == 1
    assert d[0]["v_run_id"] == "aud_1" and d[0]["c_run_id"] == "aud_3"
    assert d[0]["v_batch"] == "b1" and d[0]["c_batch"] == "b3"


def test_current_v_not_drift():
    rows = [
        ("R063", "P1", "aud_1", "CLEAN", "2026-07-01", None),
        ("R063", "P1", "aud_2", "VIOLATION", "2026-07-08", None),
    ]
    assert find_drifts(rows) == []


def test_never_v_not_drift():
    rows = [
        ("R063", "P1", "aud_1", "CLEAN", "2026-07-01", None),
        ("R063", "P1", "aud_2", "CLEAN", "2026-07-08", None),
    ]
    assert find_drifts(rows) == []
