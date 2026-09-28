# -*- coding: utf-8 -*-
"""rescreen_gated 重筛脚本单测 (recover-deterministic-recall 3.1)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from javert.audit.result import AuditResult
from javert.audit.rule import Rule
from javert.audit.run_id import new_run_id
from javert.store.audit_store import SqliteStore
from scripts.rescreen_gated import (
    PASS_TAG,
    REVERSE_TAG,
    rescreen_sqlite,
    should_flip,
)


def _rule() -> Rule:
    return Rule(rule_id="R155", domain="临床检验", violation_type="过度检查",
                question="q", derived_from_template="M2", trigger_keywords=["干扰素"])


def _fee_df(n: int, day="1/8/2024 00:00:00") -> pd.DataFrame:
    return pd.DataFrame({
        "medins_list_name": [f"干扰素测定项{i}" for i in range(n)],
        "med_list_codg": [f"IFN{i}" for i in range(n)],
        "cnt": [1.0] * n,
        "fee_ocur_time": [day] * n,
    })


class _StubLoader:
    def __init__(self, df):
        self._df = df

    def get_fees(self, pid):
        return self._df


def test_should_flip_panel_threshold():
    assert should_flip(_rule(), _fee_df(11), 3) == (True, 11)
    assert should_flip(_rule(), _fee_df(2), 3) == (False, 2)
    assert should_flip(_rule(), None, 3) == (False, None)


@pytest.fixture
def db(tmp_path: Path):
    p = tmp_path / "audit.sqlite"
    s = SqliteStore(p)
    s.init_schema()
    s.write(AuditResult(
        run_id=new_run_id(), rule_id="R155", patient_id="211419211",
        verdict="CLEAN", confidence=0.5, reasoning="LLM 原判 V, 旧闸单次放过",
        gate_tag=PASS_TAG, started_at=datetime.now(timezone.utc),
    ))
    s.close()
    return p


def test_rescreen_flip_and_revert_roundtrip(db: Path):
    panel = {"R155": 3}
    rules = {"R155": _rule()}
    loader = _StubLoader(_fee_df(11))  # 11 项 ≥ 3 → 翻转

    # dry-run: 不改库
    assert rescreen_sqlite(db, panel, rules, loader, dry_run=True) == 1
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT verdict FROM audit_runs").fetchone()[0] == "CLEAN"
    con.close()

    # 实跑: CLEAN→INCONCLUSIVE + 可逆标签
    assert rescreen_sqlite(db, panel, rules, loader) == 1
    con = sqlite3.connect(str(db))
    v, tag = con.execute("SELECT verdict, gate_tag FROM audit_runs").fetchone()
    assert v == "INCONCLUSIVE" and tag == REVERSE_TAG
    con.close()

    # 还原
    assert rescreen_sqlite(db, panel, rules, loader, revert=True) == 1
    con = sqlite3.connect(str(db))
    v, tag = con.execute("SELECT verdict, gate_tag FROM audit_runs").fetchone()
    assert v == "CLEAN" and tag == PASS_TAG
    con.close()


def test_rescreen_below_threshold_not_flipped(db: Path):
    loader = _StubLoader(_fee_df(2))  # 2 项 < 3 → 不翻
    assert rescreen_sqlite(db, {"R155": 3}, {"R155": _rule()}, loader) == 0
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT verdict FROM audit_runs").fetchone()[0] == "CLEAN"
    con.close()
