# -*- coding: utf-8 -*-
"""同步状态路由.

GET /api/sync/status — 本地 sync 状态 + 后台 worker 快照
POST /api/sync/now    — 立即触发一次心跳 (不等下个 interval)
GET /api/sync/unsynced — 当前未同步的 audit 列表 (调试用)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from javert.config import get_config
from javert.store.audit_store import SqliteStore

logger = logging.getLogger("javert.web.routes_sync")

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.get("/status")
def sync_status(request: Request) -> dict:
    """聚合: 本地未同步计数 + worker 心跳快照 + sql_enabled flag."""
    cfg = get_config()
    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        local = store.count_sync_state()
    finally:
        store.close()

    worker = getattr(request.app.state, "sync_worker", None)
    return {
        "sql_enabled": cfg.sql_enabled,
        "sql_target": {
            "host": cfg.sql_host,
            "port": cfg.sql_port,
            "database": cfg.sql_database,
            "table": "javert_audit_runs",
        },
        "local": local,
        "worker": worker.snapshot() if worker else None,
    }


@router.post("/now")
async def sync_now(request: Request) -> dict:
    """立即触发一次心跳 + 回灌 (异步, 等待完成). 返回这次的 worker 快照."""
    worker = getattr(request.app.state, "sync_worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="SyncWorker 未启动 (sql_enabled=false?)")
    state = await worker.tick_once()
    return state


@router.get("/unsynced")
def list_unsynced(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    """列出未同步的 audit (调试用). 不返回完整 evidence/tool_calls 以减负."""
    cfg = get_config()
    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        pending = store.find_unsynced(limit=limit)
    finally:
        store.close()

    return [
        {
            "run_id": r.run_id,
            "rule_id": r.rule_id,
            "patient_id": r.patient_id,
            "verdict": r.verdict,
            "confidence": r.confidence,
            "duration_ms": r.duration_ms,
            "started_at": r.started_at.isoformat(),
        }
        for r in pending
    ]
