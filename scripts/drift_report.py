# -*- coding: utf-8 -*-
"""drift_report — 存量重跑漂移只读清单 (recover-deterministic-recall 4.2).

列出全库 (rule_id, patient_id) 维度**历史曾判 VIOLATION 而当前最新为 CLEAN** 的漂移,
含两次 run_id 与批次 (覆盖 2026-07-08 晨 211440399 R063/R155 翻转)。

**纯只读**: 不修改任何行 (存量翻转可能是新代码修对了, 机器分不清, 交专家人裁)。
落 CSV 或打印。sqlite / mssql 两源。

用法:
    uv run python scripts/drift_report.py --target sqlite
    uv run python scripts/drift_report.py --target mssql --out output/drift_142.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys

from javert.config import get_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("drift_report")

FIELDS = ["rule_id", "patient_id", "v_run_id", "v_batch", "v_at", "c_run_id", "c_batch", "c_at"]


def find_drifts(rows: list[tuple]) -> list[dict]:
    """rows: (rule_id, patient_id, run_id, verdict, created_at, batch_tag), 任意序.

    分组按 (rule,patient), 时序取最新; 最新=CLEAN ∧ 历史含 VIOLATION → 一条漂移记录
    (最近一次 V + 当前 C)。纯函数, 可单测。
    """
    groups: dict[tuple[str, str], list[tuple]] = {}
    for rid, pid, run_id, verdict, at, batch in rows:
        groups.setdefault((rid, pid), []).append((str(at or ""), run_id, verdict, batch))
    out: list[dict] = []
    for (rid, pid), items in groups.items():
        items.sort(key=lambda x: x[0])  # 按 created_at 升序
        latest = items[-1]
        if latest[2] != "CLEAN":
            continue
        v_hist = [it for it in items if it[2] == "VIOLATION"]
        if not v_hist:
            continue
        v = v_hist[-1]  # 最近一次 V
        out.append({
            "rule_id": rid, "patient_id": pid,
            "v_run_id": v[1], "v_batch": v[3] or "", "v_at": v[0],
            "c_run_id": latest[1], "c_batch": latest[3] or "", "c_at": latest[0],
        })
    out.sort(key=lambda d: (d["rule_id"], d["patient_id"]))
    return out


def _read_sqlite(db_path) -> list[tuple]:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT rule_id, patient_id, run_id, verdict, created_at, batch_tag FROM audit_runs"
        ).fetchall()
    finally:
        con.close()


def _read_mssql() -> list[tuple]:
    from sqlalchemy import text

    from javert.store.sqlserver_store import get_sqlserver_store

    engine = get_sqlserver_store().get_engine()
    if engine is None:
        logger.error("142 Engine 不可用. 跳过.")
        return []
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT rule_id, patient_id, run_id, verdict, created_at, batch_tag FROM Javert_audit_runs"
        )).fetchall()
        conn.commit()
    return [tuple(r) for r in rows]


def main(argv=None):
    ap = argparse.ArgumentParser(description="存量漂移 (老 V 当前 C) 只读清单")
    ap.add_argument("--target", choices=("sqlite", "mssql"), default="sqlite")
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=None, help="CSV 输出路径 (缺省打印到 stdout)")
    args = ap.parse_args(argv)

    cfg = get_config()
    rows = _read_sqlite(args.db or cfg.audit_db_path) if args.target == "sqlite" else _read_mssql()
    drifts = find_drifts(rows)
    logger.info("扫描 %d 行, 发现 %d 条 (rule,patient) 漂移 (老 V 当前 C)", len(rows), len(drifts))

    if args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(drifts)
        logger.info("已写 %s", args.out)
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(drifts)


if __name__ == "__main__":
    main()
