# -*- coding: utf-8 -*-
"""backfill_anchors — 把命中项目/锚点确定性结果回填 anchors_json 缓存 (D2).

**只重放确定性工具逻辑 (hit_resolver.resolve_hits), 不调 LLM, 不增删 run 行,
不碰专家批注**. 幂等: 同 run 二次跑写入 byte-identical.

用法:
    # 本地 sqlite (默认, 安全; 工作台读 142, 故 sqlite 回填主要供本地核对/测试)
    uv run python scripts/backfill_anchors.py --target sqlite

    # 142 (工作台真正读取的库; 先 `javert ensure-mssql-schema` 加列)
    uv run python scripts/backfill_anchors.py --target mssql

    uv run python scripts/backfill_anchors.py --target mssql --dry-run   # 只算不写

    # 隔离数据源的可信收费缓存必须精确限定患者和批次
    uv run python scripts/backfill_anchors.py --target mssql \
      --patient-id <去标识号> --batch-tag desus --verified-fee-snapshot
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from types import SimpleNamespace

from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.web.hit_resolver import hits_to_json, load_kb_drugs, resolve_hits
from javert.web.rule_meta import load_rule_meta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_anchors")


# =========================================================
# 纯函数核心 (可单测, 幂等)
# =========================================================
def build_anchors_json(
    patient_id: str,
    evidence_json: str | None,
    tool_calls_json: str | None,
    drug_type: str | None,
    fee_df,
    kb_drugs: dict,
    *,
    verified_fee_snapshot: bool = False,
) -> str:
    """单 run → anchors_json 缓存串 (确定性). 不调 LLM."""
    run_ns = SimpleNamespace(
        patient_id=patient_id,
        evidence_json=evidence_json,
        tool_calls_json=tool_calls_json,
    )
    hits = resolve_hits(
        run_ns, drug_type,
        patient_fee_df=fee_df,
        kb_drugs=(kb_drugs if drug_type else {}),
    )
    return hits_to_json(hits, verified_fee_snapshot=verified_fee_snapshot)


def _drug_type(meta_map: dict, rule_id: str) -> str | None:
    m = meta_map.get(rule_id) or {}
    return m.get("drug_rule_type")


# =========================================================
# sqlite 目标 (本地; 完全可测)
# =========================================================
def _ensure_sqlite_column(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(audit_runs)")}
    if "anchors_json" not in cols:
        con.execute("ALTER TABLE audit_runs ADD COLUMN anchors_json TEXT")
        con.commit()


def backfill_sqlite(
    db_path,
    loader,
    meta_map,
    kb_drugs,
    *,
    dry_run=False,
    limit=None,
    patient_id=None,
    batch_tag=None,
    verified_fee_snapshot=False,
):
    if verified_fee_snapshot and (not patient_id or not batch_tag):
        raise ValueError("verified fee snapshot 回填必须同时限定 patient_id 和 batch_tag")
    con = sqlite3.connect(str(db_path))
    try:
        _ensure_sqlite_column(con)
        sql = (
            "SELECT run_id, rule_id, patient_id, evidence_json, tool_calls_json "
            "FROM audit_runs"
        )
        clauses: list[str] = []
        params: list[str] = []
        if patient_id:
            clauses.append("patient_id = ?")
            params.append(patient_id)
        if batch_tag:
            clauses.append("batch_tag = ?")
            params.append(batch_tag)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY run_id"
        rows = con.execute(sql, params).fetchall()
        if limit:
            rows = rows[:limit]
        written = 0
        fee_cache: dict[str, object] = {}
        for run_id, rule_id, pid, ev, tc in rows:
            if pid not in fee_cache:
                try:
                    fee_cache[pid] = loader.get_fees(pid)
                except Exception:  # noqa: BLE001
                    fee_cache[pid] = None
            try:
                aj = build_anchors_json(
                    pid, ev, tc, _drug_type(meta_map, rule_id), fee_cache[pid], kb_drugs,
                    verified_fee_snapshot=verified_fee_snapshot,
                )
            except Exception as e:  # noqa: BLE001 — 单 run 坏数据不应中断整批回填
                logger.warning("build_anchors_json 失败 run=%s: %s (跳过)", run_id, e)
                continue
            if not dry_run:
                con.execute(
                    "UPDATE audit_runs SET anchors_json = ? WHERE run_id = ?",
                    (aj, run_id),
                )
            written += 1
        if not dry_run:
            con.commit()
        logger.info("sqlite backfill: %d runs %s", written, "(dry-run)" if dry_run else "written")
        return written
    finally:
        con.close()


# =========================================================
# 142 (mssql) 目标 — 工作台真正读取的库
# =========================================================
def backfill_mssql(
    meta_map,
    loader,
    kb_drugs,
    *,
    dry_run=False,
    limit=None,
    batch_size=500,
    patient_id=None,
    batch_tag=None,
    verified_fee_snapshot=False,
):
    """142 回填 — 分批提交, 不长占表锁 (可在工作台在线时跑).

    旧实现把所有 UPDATE 放进单一大事务, commit 只在末尾 → 持有 javert_audit_runs
    行/表锁长达整批 (~万行/十几分钟), 阻塞工作台 list_patients 等读查询 (实测整页
    变慢 + 病人列表空). 改为每 batch_size 行一次 commit: 锁只在每批内短暂持有,
    工作台读可在批间穿插, 不再被长事务挡死.
    """
    if verified_fee_snapshot and (not patient_id or not batch_tag):
        raise ValueError("verified fee snapshot 回填必须同时限定 patient_id 和 batch_tag")

    from javert.store.sqlserver_store import get_sqlserver_store

    store = get_sqlserver_store()
    engine = store.get_engine()
    if engine is None:
        logger.error("142 Engine 不可用 (sql_enabled=false / 依赖缺失 / 连不上). 跳过.")
        return 0
    from sqlalchemy import text

    sel = (
        "SELECT run_id, rule_id, patient_id, evidence_json, tool_calls_json "
        "FROM javert_audit_runs"
    )
    clauses: list[str] = []
    select_params: dict[str, str] = {}
    if patient_id:
        clauses.append("patient_id = :patient_id")
        select_params["patient_id"] = patient_id
    if batch_tag:
        clauses.append("batch_tag = :batch_tag")
        select_params["batch_tag"] = batch_tag
    if clauses:
        sel += " WHERE " + " AND ".join(clauses)
    sel += " ORDER BY run_id"
    upd = text("UPDATE javert_audit_runs SET anchors_json = :aj WHERE run_id = :rid")
    written = 0
    fee_cache: dict[str, object] = {}
    with engine.connect() as conn:
        rows = conn.execute(text(sel), select_params).fetchall()
        conn.commit()  # 关掉读事务 (释放共享锁), 写入走后续独立小事务
        if limit:
            rows = rows[:limit]
        total = len(rows)

        def flush(params: list[dict]) -> None:
            if params and not dry_run:
                conn.execute(upd, params)  # 整批一次提交参数 (executemany)
                conn.commit()              # 立即释放本批行锁

        batch: list[dict] = []
        for r in rows:
            run_id, rule_id, pid, ev, tc = r[0], r[1], r[2], r[3], r[4]
            if pid not in fee_cache:
                try:
                    fee_cache[pid] = loader.get_fees(pid)
                except Exception:  # noqa: BLE001
                    fee_cache[pid] = None
            try:
                aj = build_anchors_json(
                    pid, ev, tc, _drug_type(meta_map, rule_id), fee_cache[pid], kb_drugs,
                    verified_fee_snapshot=verified_fee_snapshot,
                )
            except Exception as e:  # noqa: BLE001 — 单 run 坏数据不应中断整批回填
                logger.warning("build_anchors_json 失败 run=%s: %s (跳过)", run_id, e)
                continue
            batch.append({"aj": aj, "rid": run_id})
            written += 1
            if len(batch) >= batch_size:
                flush(batch)
                logger.info(
                    "142 backfill: %d/%d %s",
                    written,
                    total,
                    "已计算(dry-run)" if dry_run else "已提交",
                )
                batch = []
        flush(batch)  # 余量
    logger.info("142 backfill: %d runs %s", written, "(dry-run)" if dry_run else "written")
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description="回填 anchors_json 命中项目缓存 (确定性, 不调 LLM)")
    ap.add_argument("--target", choices=("sqlite", "mssql"), default="sqlite")
    ap.add_argument("--db", default=None, help="sqlite db 路径 (默认 cfg.audit_db_path)")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--patient-id", default=None, help="只回填该去标识患者")
    ap.add_argument("--batch-tag", default=None, help="只回填该 batch_tag")
    ap.add_argument(
        "--verified-fee-snapshot",
        action="store_true",
        help="将缓存标记为审计收费切片已验证；必须同时指定 patient-id 和 batch-tag",
    )
    ap.add_argument("--batch-size", type=int, default=500,
                    help="142 回填每批提交行数 (越小锁占用越短, 默认 500)")
    args = ap.parse_args(argv)
    if args.verified_fee_snapshot and (not args.patient_id or not args.batch_tag):
        ap.error("--verified-fee-snapshot 必须同时指定 --patient-id 和 --batch-tag")

    cfg = get_config()
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    meta_map = load_rule_meta()
    kb_drugs = load_kb_drugs()

    if args.target == "sqlite":
        db_path = args.db or cfg.audit_db_path
        n = backfill_sqlite(db_path, loader, meta_map, kb_drugs,
                            dry_run=args.dry_run, limit=args.limit,
                            patient_id=args.patient_id, batch_tag=args.batch_tag,
                            verified_fee_snapshot=args.verified_fee_snapshot)
    else:
        n = backfill_mssql(meta_map, loader, kb_drugs,
                          dry_run=args.dry_run, limit=args.limit,
                          batch_size=args.batch_size,
                          patient_id=args.patient_id, batch_tag=args.batch_tag,
                          verified_fee_snapshot=args.verified_fee_snapshot)
    logger.info("done: %d runs processed", n)


if __name__ == "__main__":
    main()
