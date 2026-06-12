# -*- coding: utf-8 -*-
"""SyncWorker — 后台心跳 + 142 回灌.

每 interval_s 秒做一次:
  1. SQL Server 健康检查 (Engine.connect + SELECT 1)
  2. 若通: 从本地 SQLite 拉 batch_size 条 unsynced, 逐条 write_audit + mark
  3. 把状态保存到 self.last_*, /api/health 与 /api/sync/status 读它

failure modes:
  - 142 不通: health_check 失败, 不回灌. 数据堆在本地等下次.
  - 142 通但 write_audit 失败: 单条 mark_sync_failed, 下次再试.
  - tick 异常: log 错误, 不让 worker 死, 等下次 interval.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from javert.audit.rule_loader import load_rule
from javert.config import get_config
from javert.store.audit_store import SqliteStore
from javert.store.sqlserver_store import get_sqlserver_store

logger = logging.getLogger("javert.web.heartbeat")


class SyncWorker:
    """异步后台 worker. 单 instance per FastAPI app, lifespan 管理."""

    def __init__(self, interval_s: int = 30, batch_size: int = 50):
        self.interval_s = int(interval_s)
        self.batch_size = int(batch_size)

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

        # 状态 (供 /api/health 与 /api/sync/status 读)
        self.last_run_at: datetime | None = None
        self.last_run_duration_ms: int = 0
        self.last_health: dict = {"sql_server": False, "error": "未首次心跳"}
        self.last_synced_count: int = 0
        self.last_failed_count: int = 0
        self.total_runs: int = 0
        self.total_synced: int = 0
        self.total_failed: int = 0
        self.started_at: datetime | None = None

    # ------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self.started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._loop(), name="javert-sync-worker")
        logger.info(
            "SyncWorker 启动 (interval=%ds, batch=%d)",
            self.interval_s, self.batch_size,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None
        logger.info("SyncWorker 已停止")

    # ------------------------------------------------------
    # 主循环
    # ------------------------------------------------------
    async def _loop(self) -> None:
        # 给 lifespan 一点缓冲, 1s 后第一次 tick
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return
        while not self._stop_event.is_set():
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SyncWorker tick 异常 (吞掉, 等下一轮)")
            # 等 interval, 期间被 stop 唤醒就跳出
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_s)
                break  # stop 触发了
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

    # ------------------------------------------------------
    # 单次心跳 + 回灌 (可手动触发)
    # ------------------------------------------------------
    async def tick_once(self) -> dict:
        """单次心跳: health_check + 批量回灌. 返回 self.snapshot()."""
        t0 = time.perf_counter()
        cfg = get_config()
        sql142 = get_sqlserver_store()
        loop = asyncio.get_event_loop()

        # 1. 健康检查 (同步 IO 包到 thread)
        health = await loop.run_in_executor(None, sql142.health_check)
        self.last_health = health

        synced_now = 0
        failed_now = 0

        if cfg.sql_enabled and health.get("sql_server"):
            # 2. 142 通 → 批量回灌
            counts = await loop.run_in_executor(
                None, self._replay_unsynced, cfg, sql142,
            )
            synced_now, failed_now = counts

        self.last_run_at = datetime.now(timezone.utc)
        self.last_run_duration_ms = int((time.perf_counter() - t0) * 1000)
        self.last_synced_count = synced_now
        self.last_failed_count = failed_now
        self.total_runs += 1
        self.total_synced += synced_now
        self.total_failed += failed_now

        if synced_now or failed_now:
            logger.info(
                "心跳 #%d: synced=%d failed=%d duration=%dms",
                self.total_runs, synced_now, failed_now, self.last_run_duration_ms,
            )
        return self.snapshot()

    def _replay_unsynced(self, cfg, sql142) -> tuple[int, int]:
        """同步实现 (在 thread executor 中跑). 返回 (synced, failed)."""
        store = SqliteStore(cfg.audit_db_path)
        synced = 0
        failed = 0
        try:
            store.init_schema()
            pending = store.find_unsynced(limit=self.batch_size)
            if not pending:
                return (0, 0)
            for r in pending:
                # 尝试加载 rule yaml (回灌时 rule 信息丢了, 兜底从文件读)
                rule_obj = None
                try:
                    rule_obj = load_rule(cfg.rules_path / f"{r.rule_id}.yaml")
                except Exception:
                    rule_obj = None
                ok = sql142.write_audit(r, rule_obj, triggered_by="heartbeat")
                if ok:
                    store.mark_synced(r.run_id)
                    synced += 1
                else:
                    store.mark_sync_failed(r.run_id, "心跳回灌失败 (142 write_audit)")
                    failed += 1
        finally:
            store.close()
        return (synced, failed)

    # ------------------------------------------------------
    # 状态快照
    # ------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "running": self._task is not None and not self._task.done(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "interval_s": self.interval_s,
            "batch_size": self.batch_size,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_run_duration_ms": self.last_run_duration_ms,
            "last_health": self.last_health,
            "last_synced_count": self.last_synced_count,
            "last_failed_count": self.last_failed_count,
            "total_runs": self.total_runs,
            "total_synced": self.total_synced,
            "total_failed": self.total_failed,
        }
