# -*- coding: utf-8 -*-
"""rescreen_gated — 存量「单次放过」行按套餐口径确定性重筛 (recover-deterministic-recall 3.1).

只对 `panel_rules` 内规则、`gate_tag='单次放过'` 且 `verdict='CLEAN'` 的存量行, 用患者费用
按「同日不同项目名数」重算: 达阈值 (min_distinct_items) → 原地 UPDATE CLEAN→INCONCLUSIVE
+ 可逆标签「单次闸重筛回升(原C)」(进专家队列, LLM 原始 V 推理原样保留)。

**不调 LLM · 不增删行 · 不碰 review 表 · 有专家 review 的行跳过并报告 · 一键可还原。**

用法:
    uv run python scripts/rescreen_gated.py --target sqlite --dry-run   # 出翻转量分布, 不写
    uv run python scripts/rescreen_gated.py --target sqlite             # sqlite 落 (source-of-truth)
    uv run python scripts/rescreen_gated.py --target mssql              # 142 落 (先 sqlite 后 142)
    uv run python scripts/rescreen_gated.py --target mssql --revert     # 凭可逆标签还原
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from collections import Counter, defaultdict

from javert.audit.rule_loader import load_all
from javert.audit.verdict_gate import extract_exam_keywords, get_gate_config
from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.data.fee_netting import max_same_day_distinct_items

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rescreen_gated")

PASS_TAG = "单次放过"
REVERSE_TAG = "单次闸重筛回升(原C)"


# =========================================================
# 纯函数核心 (可单测)
# =========================================================
def should_flip(rule, fee_df, min_items: int) -> tuple[bool, int | None]:
    """该 (rule, patient) 是否达套餐口径翻转阈值. 返回 (flip, 同日不同项目名数 n).

    n is None (费用不可得 / 无匹配) → 不翻 (保守: 不确认不回收)。
    """
    n = max_same_day_distinct_items(fee_df, extract_exam_keywords(rule))
    return (n is not None and n >= min_items), n


def _panel_context():
    """(panel {rule_id: min}, rules {rule_id: Rule}, loader) — 三处目标共用."""
    cfg = get_config()
    panel = get_gate_config().panel_rules
    all_rules = load_all(cfg.resolve(cfg.rules_dir))
    rules = {rid: all_rules[rid] for rid in panel if rid in all_rules}
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    return panel, rules, loader


def _report(dist: dict[str, Counter], flips: int, skipped: int, total: int) -> None:
    logger.info("重筛候选 (panel · gate_tag=单次放过 · CLEAN): %d 行", total)
    for rid, c in sorted(dist.items()):
        logger.info("  %s: 将翻转 %d 行, 同日项目数分布 %s", rid, sum(c.values()), dict(sorted(c.items())))
    logger.info("合计将翻转 %d 行 (跳过有 review %d 行)", flips, skipped)
    if flips > 100:
        logger.warning("翻转量 > 100 → 建议收紧 min_distinct_items 再落 (设计 D2)")


# =========================================================
# sqlite 目标 (source-of-truth, 先落)
# =========================================================
def rescreen_sqlite(db_path, panel, rules, loader, *, dry_run=False, revert=False) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        if revert:
            return _revert_sqlite(con, dry_run=dry_run)
        placeholders = ",".join("?" for _ in panel)
        rows = con.execute(
            f"SELECT run_id, rule_id, patient_id FROM audit_runs "
            f"WHERE verdict='CLEAN' AND gate_tag=? AND rule_id IN ({placeholders})",
            (PASS_TAG, *panel.keys()),
        ).fetchall()
        dist: dict[str, Counter] = defaultdict(Counter)
        flips = 0
        fee_cache: dict[str, object] = {}
        for run_id, rule_id, pid in rows:
            rule = rules.get(rule_id)
            if rule is None:
                continue
            if pid not in fee_cache:
                try:
                    fee_cache[pid] = loader.get_fees(pid)
                except Exception:  # noqa: BLE001
                    fee_cache[pid] = None
            flip, n = should_flip(rule, fee_cache[pid], panel[rule_id])
            if not flip:
                continue
            dist[rule_id][n] += 1
            flips += 1
            if not dry_run:
                con.execute(
                    "UPDATE audit_runs SET verdict='INCONCLUSIVE', gate_tag=? WHERE run_id=?",
                    (REVERSE_TAG, run_id),
                )
        if not dry_run:
            con.commit()
        _report(dist, flips, 0, len(rows))
        return flips
    finally:
        con.close()


def _revert_sqlite(con, *, dry_run=False) -> int:
    rows = con.execute(
        "SELECT run_id FROM audit_runs WHERE gate_tag=?", (REVERSE_TAG,)
    ).fetchall()
    if not dry_run:
        con.execute(
            "UPDATE audit_runs SET verdict='CLEAN', gate_tag=? WHERE gate_tag=?",
            (PASS_TAG, REVERSE_TAG),
        )
        con.commit()
    logger.info("sqlite 还原: %d 行 %s→CLEAN/单次放过 %s", len(rows), REVERSE_TAG,
                "(dry-run)" if dry_run else "")
    return len(rows)


# =========================================================
# 142 (mssql) 目标 — 工作台读取的库; 跳过有 review 的行
# =========================================================
def rescreen_mssql(panel, rules, loader, *, dry_run=False, revert=False) -> int:
    from sqlalchemy import text

    from javert.store.sqlserver_store import get_sqlserver_store

    store = get_sqlserver_store()
    engine = store.get_engine()
    if engine is None:
        logger.error("142 Engine 不可用 (sql_enabled=false / 连不上). 跳过.")
        return 0

    with engine.connect() as conn:
        if revert:
            rows = conn.execute(
                text("SELECT run_id FROM Javert_audit_runs WHERE gate_tag=:t"),
                {"t": REVERSE_TAG},
            ).fetchall()
            conn.commit()
            if not dry_run:
                conn.execute(
                    text("UPDATE Javert_audit_runs SET verdict='CLEAN', gate_tag=:p WHERE gate_tag=:t"),
                    {"p": PASS_TAG, "t": REVERSE_TAG},
                )
                conn.commit()
            logger.info("142 还原: %d 行 %s (%s)", len(rows), REVERSE_TAG,
                        "dry-run" if dry_run else "done")
            return len(rows)

        ph = ",".join(f":r{i}" for i in range(len(panel)))
        params = {f"r{i}": rid for i, rid in enumerate(panel)}
        params["t"] = PASS_TAG
        rows = conn.execute(
            text(f"SELECT run_id, rule_id, patient_id FROM Javert_audit_runs "
                 f"WHERE verdict='CLEAN' AND gate_tag=:t AND rule_id IN ({ph})"),
            params,
        ).fetchall()
        conn.commit()  # 关读事务

        dist: dict[str, Counter] = defaultdict(Counter)
        flips = skipped = 0
        fee_cache: dict[str, object] = {}
        upd = text("UPDATE Javert_audit_runs SET verdict='INCONCLUSIVE', gate_tag=:g WHERE run_id=:rid")
        for r in rows:
            run_id, rule_id, pid = r[0], r[1], r[2]
            rule = rules.get(rule_id)
            if rule is None:
                continue
            # 有任意专家 review 的行跳过 (不覆盖专家已看过的裁决)
            if store.list_reviews_for_run(run_id):
                skipped += 1
                continue
            if pid not in fee_cache:
                try:
                    fee_cache[pid] = loader.get_fees(pid)
                except Exception:  # noqa: BLE001
                    fee_cache[pid] = None
            flip, n = should_flip(rule, fee_cache[pid], panel[rule_id])
            if not flip:
                continue
            dist[rule_id][n] += 1
            flips += 1
            if not dry_run:
                conn.execute(upd, {"g": REVERSE_TAG, "rid": run_id})
        if not dry_run:
            conn.commit()
        _report(dist, flips, skipped, len(rows))
        return flips


def main(argv=None):
    ap = argparse.ArgumentParser(description="套餐口径存量重筛 (单次放过 CLEAN → INCONCLUSIVE, 可逆)")
    ap.add_argument("--target", choices=("sqlite", "mssql"), default="sqlite")
    ap.add_argument("--db", default=None, help="sqlite db 路径 (默认 cfg.audit_db_path)")
    ap.add_argument("--dry-run", action="store_true", help="只算翻转量分布, 不写")
    ap.add_argument("--revert", action="store_true", help="凭可逆标签还原 (INCONCLUSIVE→CLEAN)")
    args = ap.parse_args(argv)

    cfg = get_config()
    panel, rules, loader = _panel_context()
    if not panel:
        logger.warning("verdict_gate.yaml 无 panel_rules, 无可重筛对象")
        return
    logger.info("panel_rules = %s", panel)

    if args.target == "sqlite":
        rescreen_sqlite(args.db or cfg.audit_db_path, panel, rules, loader,
                        dry_run=args.dry_run, revert=args.revert)
    else:
        rescreen_mssql(panel, rules, loader, dry_run=args.dry_run, revert=args.revert)


if __name__ == "__main__":
    main()
