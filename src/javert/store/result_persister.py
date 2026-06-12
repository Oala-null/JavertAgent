# -*- coding: utf-8 -*-
"""ResultPersister — 写 SQLite + 立即试推 142 + mark 同步状态.

设计 (跟 zadig_agent ResultPersister 模式一致):
  1. 本地 SQLite 是 source-of-truth, write 必须成功 (失败抛异常)
  2. 立即试推 142, 成功 → mark_synced; 失败 → mark_sync_failed (留给后台 worker retry)
  3. 142 写入失败永不阻塞主流程, 只 warn

调用约定:
  - 单次调用 (web / dry-run): persist_one(result, rule, ...) — 内部管理 SqliteStore
  - 批量调用 (run --pilot): 传入已打开的 sqlite_store 复用连接
"""

from __future__ import annotations

import logging

from javert.audit.result import AuditResult
from javert.audit.rule import Rule
from javert.config import get_config

from .audit_store import SqliteStore
from .sqlserver_store import get_sqlserver_store

logger = logging.getLogger("javert.store.result_persister")


def persist_one(
    result: AuditResult,
    rule: Rule | None = None,
    *,
    triggered_by: str = "cli",
    sqlite_store: SqliteStore | None = None,
    batch_tag: str | None = None,
) -> dict:
    """单条 audit 持久化.

    Args:
        result: AuditResult
        rule: 可选, 用于 142 双写的 yaml snapshot
        triggered_by: "cli-dry-run" / "cli-run" / "web" / "heartbeat"
        sqlite_store: 复用已开的连接 (批量场景); None = 内部新开

    Returns:
        {
            "sqlite": True,                 # 本地写入永远成功 (失败会抛)
            "sqlserver_142": bool,          # 立即推 142 是否成功
            "sync_state": "synced" | "pending" | "skipped",
                # synced: 已立即推到 142 + mark
                # pending: 142 不可达, 留给后台 worker retry
                # skipped: sql_enabled=false, 不会推
        }
    """
    cfg = get_config()
    # batch_tag 优先级: 显式参数 > config (env JAVERT_BATCH_TAG)
    tag = batch_tag if batch_tag is not None else cfg.batch_tag
    own_store = sqlite_store is None
    if own_store:
        sqlite_store = SqliteStore(cfg.audit_db_path)
        sqlite_store.init_schema()

    try:
        # 1. 本地写 (source-of-truth)
        sqlite_store.write(result, batch_tag=tag)

        # 2. 立即试推 142
        sql142 = get_sqlserver_store()
        if not cfg.sql_enabled:
            return {"sqlite": True, "sqlserver_142": False, "sync_state": "skipped"}

        ok = sql142.write_audit(result, rule, triggered_by=triggered_by, batch_tag=tag)
        if ok:
            sqlite_store.mark_synced(result.run_id)
            return {"sqlite": True, "sqlserver_142": True, "sync_state": "synced"}
        else:
            sqlite_store.mark_sync_failed(
                result.run_id,
                "142 立即推失败: Engine/连接/写入异常 (后台 worker 将 retry)",
            )
            logger.warning(
                "142 立即推失败 run_id=%s, 已留待后台 retry", result.run_id,
            )
            return {"sqlite": True, "sqlserver_142": False, "sync_state": "pending"}

    finally:
        if own_store:
            sqlite_store.close()
