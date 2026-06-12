# -*- coding: utf-8 -*-
"""tests for scripts/backfill_anchors.py + anchors_json 缓存 (evidence-anchoring G7).

回填幂等 (二次 byte-identical) / round-trip 序列化 / 不增删 run 行 / 不改其他列 /
缓存缺失渲染仍出命中项目 (route fallback) / 缓存命中走缓存.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.web.hit_resolver import Anchor, HitItem, hits_from_json, hits_to_json
from javert.web.rule_meta import load_rule_meta
from javert.web.hit_resolver import load_kb_drugs

# 从脚本文件加载 (scripts 非包)
_SPEC = importlib.util.spec_from_file_location(
    "backfill_anchors",
    Path(__file__).resolve().parent.parent / "scripts" / "backfill_anchors.py",
)
backfill = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backfill)


_KB = {
    "布地奈德肠溶胶囊": [{"rule_type": "限适应症", "basis": "限IgAN成人。"}],
}
_FEE_DF = pd.DataFrame(
    [["(集)吸入用布地奈德混悬液", "XR03BAB", "0731"]],
    columns=["medins_list_name", "med_list_codg", "medins_list_codg"],
)
_EV = json.dumps(
    [{"source": "drug_audit_lookup", "locator": "布地奈德肠溶胶囊", "text": "x"}],
    ensure_ascii=False,
)


# =========================================================
# 序列化 round-trip + 幂等
# =========================================================
def test_hits_json_round_trip():
    hits = [
        HitItem(source="drug", name="A", code_nat="C1", code_local="L1",
                restriction="r", matched_fee_name="(集)A",
                anchor=Anchor(tab="fees", query="(集)A", match_level="keyword")),
        HitItem(source="note", name="出院诊断",
                anchor=Anchor(tab="notes", subsection="出院诊断", query="甲状腺癌",
                              match_level="locator")),
    ]
    s = hits_to_json(hits)
    back = hits_from_json(s)
    assert back is not None
    assert [h.model_dump() for h in back] == [h.model_dump() for h in hits]


def test_hits_from_json_handles_garbage():
    assert hits_from_json(None) is None
    assert hits_from_json("") is None
    assert hits_from_json("not json") is None
    assert hits_from_json('{"not":"a list"}') is None


def test_build_anchors_json_idempotent():
    a = backfill.build_anchors_json("P1", _EV, "[]", "限适应症", _FEE_DF, _KB)
    b = backfill.build_anchors_json("P1", _EV, "[]", "限适应症", _FEE_DF, _KB)
    assert a == b
    hits = hits_from_json(a)
    assert hits and hits[0].code_nat == "XR03BAB"
    assert "IgAN" in hits[0].restriction


# =========================================================
# sqlite 回填端到端 — 幂等 + 不增删行 + 不改其他列
# =========================================================
def test_backfill_sqlite_end_to_end(tmp_path):
    db = tmp_path / "audit.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE audit_runs (run_id TEXT PRIMARY KEY, rule_id TEXT, "
        "patient_id TEXT, verdict TEXT, evidence_json TEXT, tool_calls_json TEXT)"
    )
    con.execute(
        "INSERT INTO audit_runs VALUES (?,?,?,?,?,?)",
        ("aud_x1", "R007", "J90508", "VIOLATION", _EV, "[]"),
    )
    con.commit()
    con.close()

    cfg = get_config()
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    meta = load_rule_meta()
    kb = load_kb_drugs()

    n1 = backfill.backfill_sqlite(db, loader, meta, kb)
    assert n1 == 1

    con = sqlite3.connect(db)
    rows = con.execute("SELECT run_id, verdict, evidence_json, anchors_json FROM audit_runs").fetchall()
    assert len(rows) == 1  # 不增删行
    run_id, verdict, ev, aj1 = rows[0]
    assert verdict == "VIOLATION"  # 其他列不动
    assert ev == _EV
    assert aj1  # anchors_json 已回填
    hits = hits_from_json(aj1)
    assert hits and hits[0].source == "drug"
    # fix-drug-code-match: J90508 实际用「吸入用布地奈德混悬液」(R03 呼吸, 码 XR03BAB…),
    # 与 KB「布地奈德肠溶胶囊」(限 IgAN, H02 消化, 码 XH02ABB…) 码不同 → 码精确不命中.
    # 码全不中 → 名兜底但编码留空 + needs_review, 不臆造相似药码 (anti-串味, 修复旧子串误配).
    assert all(h.code_nat == "" for h in hits)
    assert all("需复核" in h.review_note for h in hits)
    con.close()

    # 二次跑 byte-identical (幂等)
    backfill.backfill_sqlite(db, loader, meta, kb)
    con = sqlite3.connect(db)
    aj2 = con.execute("SELECT anchors_json FROM audit_runs WHERE run_id='aud_x1'").fetchone()[0]
    cnt = con.execute("SELECT COUNT(*) FROM audit_runs").fetchone()[0]
    con.close()
    assert aj2 == aj1
    assert cnt == 1


def test_backfill_sqlite_skips_failing_run(tmp_path, monkeypatch):
    """单 run build 异常不中断整批: 坏 run 跳过 (NULL), 其余照常回填."""
    db = tmp_path / "audit.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE audit_runs (run_id TEXT PRIMARY KEY, rule_id TEXT, "
        "patient_id TEXT, verdict TEXT, evidence_json TEXT, tool_calls_json TEXT)"
    )
    con.executemany(
        "INSERT INTO audit_runs VALUES (?,?,?,?,?,?)",
        [("aud_bad", "R007", "J90508", "VIOLATION", _EV, "[]"),
         ("aud_ok", "R007", "J90508", "VIOLATION", _EV, "[]")],
    )
    con.commit()
    con.close()
    cfg = get_config()
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    orig = backfill.build_anchors_json
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:  # ORDER BY run_id → aud_bad 先
            raise ValueError("boom")
        return orig(*a, **k)

    monkeypatch.setattr(backfill, "build_anchors_json", flaky)
    n = backfill.backfill_sqlite(db, loader, load_rule_meta(), load_kb_drugs())
    assert n == 1  # 坏的跳过, 好的写入
    con = sqlite3.connect(db)
    rows = dict(con.execute("SELECT run_id, anchors_json FROM audit_runs").fetchall())
    con.close()
    assert rows["aud_bad"] is None   # 坏 run 不写
    assert rows["aud_ok"]            # 好 run 照常


def test_backfill_sqlite_dry_run_writes_nothing(tmp_path):
    db = tmp_path / "audit.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE audit_runs (run_id TEXT PRIMARY KEY, rule_id TEXT, "
        "patient_id TEXT, verdict TEXT, evidence_json TEXT, tool_calls_json TEXT)"
    )
    con.execute("INSERT INTO audit_runs VALUES (?,?,?,?,?,?)",
                ("aud_y1", "R007", "J90508", "VIOLATION", _EV, "[]"))
    con.commit()
    con.close()
    cfg = get_config()
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    backfill.backfill_sqlite(db, loader, load_rule_meta(), load_kb_drugs(), dry_run=True)
    con = sqlite3.connect(db)
    aj = con.execute("SELECT anchors_json FROM audit_runs WHERE run_id='aud_y1'").fetchone()[0]
    con.close()
    assert aj is None  # dry-run 不写


# =========================================================
# route fallback / cache-hit (无需 142)
# =========================================================
def test_route_resolve_hits_cache_hit_and_fallback():
    from javert.web.api.routes_workbench import _resolve_hits_for_runs

    run = SimpleNamespace(
        run_id="aud_cache1", rule_id="R007", patient_id="J90508",
        evidence_json=_EV, tool_calls_json="[]",
    )
    meta = load_rule_meta()

    # 缓存命中 — 直接用 anchors_map, 不触 loader/KB
    cached_hits = [HitItem(source="drug", name="缓存药", code_nat="CC",
                           anchor=Anchor(tab="fees", query="x"))]
    amap = {"aud_cache1": hits_to_json(cached_hits)}
    out = _resolve_hits_for_runs("J90508", [run], meta, amap)
    assert [h.model_dump() for h in out["aud_cache1"]] == [h.model_dump() for h in cached_hits]

    # 缓存缺失 — 现算回退 (真实 loader + KB), 仍出命中项目
    out2 = _resolve_hits_for_runs("J90508", [run], meta, {})
    assert out2["aud_cache1"]  # 非空
    assert any(h.source == "drug" for h in out2["aud_cache1"])
