# -*- coding: utf-8 -*-
"""javert sync-to-mssql — sqlite audit_runs → 142 javert_audit_runs 一次性/增量同步.

设计上和 web SyncWorker 同源 (write_audit + mark_synced/mark_sync_failed), 但用于
CLI 一次性 import (3345 条) 与 --pending-only 补漏.
"""

from __future__ import annotations

import sqlite3
import time

import click

from javert.audit.rule_loader import load_rule
from javert.config import get_config
from javert.store.audit_store import SqliteStore
from javert.store.sqlserver_store import get_sqlserver_store


def run_sync_to_mssql(
    *,
    dry_run: bool = False,
    pending_only: bool = False,
    batch_size: int = 200,
) -> int:
    cfg = get_config()
    if not cfg.sql_enabled:
        click.echo("error: JAVERT_SQL_ENABLED=false, 不能 sync", err=True)
        return 2
    if not cfg.audit_db_path.exists():
        click.echo(f"error: sqlite 不存在: {cfg.audit_db_path}", err=True)
        return 2

    sql142 = get_sqlserver_store()
    health = sql142.health_check()
    if not health.get("sql_server"):
        click.echo(f"error: 142 不可达: {health.get('error')}", err=True)
        return 3

    # 统计 sqlite 行 + 142 已存在的 run_id 集合 (idempotent diff)
    with sqlite3.connect(cfg.audit_db_path) as conn:
        conn.row_factory = sqlite3.Row
        if pending_only:
            total = conn.execute(
                "SELECT COUNT(*) FROM audit_runs WHERE synced_at IS NULL"
            ).fetchone()[0]
        else:
            total = conn.execute("SELECT COUNT(*) FROM audit_runs").fetchone()[0]

    if total == 0:
        click.echo("nothing to sync (sqlite empty / no pending rows)")
        return 0

    if dry_run:
        # 不写, 只算 INSERT/UPDATE plan
        engine = sql142.get_engine()
        if engine is None:
            click.echo("error: 142 Engine 不可用", err=True)
            return 3
        from sqlalchemy import text
        with engine.connect() as conn142:
            existing = {
                r[0]
                for r in conn142.execute(
                    text("SELECT run_id FROM javert_audit_runs")
                ).fetchall()
            }
        with sqlite3.connect(cfg.audit_db_path) as conn_l:
            conn_l.row_factory = sqlite3.Row
            where = "WHERE synced_at IS NULL " if pending_only else ""
            cur = conn_l.execute(f"SELECT run_id FROM audit_runs {where}")
            local_ids = [r[0] for r in cur.fetchall()]
        n_insert = sum(1 for rid in local_ids if rid not in existing)
        n_update = sum(1 for rid in local_ids if rid in existing)
        click.echo(
            f"[dry-run] will INSERT {n_insert} / UPDATE {n_update} / "
            f"total {len(local_ids)} from sqlite to 142"
        )
        return 0

    # 实跑
    store = SqliteStore(cfg.audit_db_path)
    store.init_schema()
    t0 = time.perf_counter()
    synced = 0
    failed = 0
    offset = 0
    try:
        while True:
            if pending_only:
                pending = store.find_unsynced(limit=batch_size)
                if not pending:
                    break
                batch = pending
            else:
                # 全量分页
                with sqlite3.connect(cfg.audit_db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.execute(
                        "SELECT run_id FROM audit_runs ORDER BY created_at "
                        "LIMIT ? OFFSET ?",
                        (batch_size, offset),
                    )
                    rids = [r[0] for r in cur.fetchall()]
                if not rids:
                    break
                batch = [store.find_by_run_id(rid) for rid in rids if rid]
                batch = [r for r in batch if r is not None]
                offset += batch_size

            # 一次性查 batch_tag (AuditResult model 没这字段)
            run_ids = [r.run_id for r in batch]
            tag_map: dict[str, str | None] = {}
            if run_ids:
                with sqlite3.connect(cfg.audit_db_path) as conn_t:
                    placeholders = ",".join("?" * len(run_ids))
                    cur = conn_t.execute(
                        f"SELECT run_id, batch_tag FROM audit_runs WHERE run_id IN ({placeholders})",
                        run_ids,
                    )
                    tag_map = {r[0]: r[1] for r in cur.fetchall()}

            for result in batch:
                rule_obj = None
                try:
                    rule_obj = load_rule(cfg.rules_path / f"{result.rule_id}.yaml")
                except Exception:
                    rule_obj = None
                ok = sql142.write_audit(
                    result, rule_obj,
                    triggered_by="cli-sync",
                    batch_tag=tag_map.get(result.run_id),
                )
                if ok:
                    store.mark_synced(result.run_id)
                    synced += 1
                else:
                    store.mark_sync_failed(result.run_id, "cli-sync 写入失败")
                    failed += 1

            click.echo(
                f"[{synced + failed}/{total}] synced={synced} failed={failed}",
                err=True,
            )
            if not pending_only and len(batch) < batch_size:
                break
            if pending_only and synced + failed >= total:
                break
    finally:
        store.close()

    dt = time.perf_counter() - t0
    if pending_only:
        click.echo(
            f"synced {synced} pending rows, {failed} failed, {dt:.1f}s"
        )
    else:
        click.echo(f"synced {synced} rows ({failed} failed) in {dt:.1f}s")
    return 0 if failed == 0 else 4
