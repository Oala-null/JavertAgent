# -*- coding: utf-8 -*-
"""SSE 端点 + EventBus + audit_watcher 后台任务.

两类事件:
  - review_submitted: routes_workbench.submit_review 触发 (asyncio 同进程)
  - new_audit_run:    audit_watcher 任务 (poll javert_audit_runs 每秒) 触发

EventBus 单进程内 fan-out, 无外部依赖. 多 web 进程 (未来) 各自 poll 各自 fan-out.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from javert.store.sqlserver_store import get_sqlserver_store
from javert.web.auth import session_user_id
from javert.web.public_presenter import present_public_explanation, public_promise_summary
from javert.web.rule_meta import load_rule_meta

logger = logging.getLogger("javert.web.routes_sse")


_ELIGIBILITY_SSE_FIELDS = (
    "audit_disposition",
    "eligibility_status",
    "release_id",
    "rule_revision_id",
    "drug_concept_id",
    "policy_scope",
    "source_type",
    "policy_scope_display_label",
    "source_versions",
    "source_document_ids",
    "source_fragment_ids",
    "rule_effective_from",
    "rule_effective_to",
    "evaluated_service_date",
    "effective_date_enforced",
    "temporal_applicability",
    "temporal_warning",
    "scope_evaluations",
)


def _eligibility_sse_fields(value: Any) -> dict[str, Any]:
    """提取 SSE 顶层可选摘要；旧行/非肿瘤结果统一返回 None。"""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    evaluation = value if isinstance(value, dict) else {}
    return {field: evaluation.get(field) for field in _ELIGIBILITY_SSE_FIELDS}


# =========================================================
# EventBus — asyncio Queue fan-out
# =========================================================
class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subs.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subs.discard(q)

    async def publish(self, event: str, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=_json_default)
        msg = {"event": event, "data": payload}
        async with self._lock:
            dead: list[asyncio.Queue] = []
            for q in self._subs:
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"Not JSON serializable: {type(o).__name__}")


event_bus = EventBus()


# =========================================================
# audit_watcher — 后台任务 (lifespan 控制)
# =========================================================
class AuditWatcher:
    """按 BIGINT IDENTITY id 追踪进度, 不用 created_at — DATETIME2(7) 比 Python
    datetime 多 1 位精度, `created_at > :last_seen` 在最后一行死循环 (stored.X >
    param.0 永真). id 是单调自增整数, 精确比较."""

    def __init__(self, poll_interval_s: float = 1.0, error_backoff_s: float = 5.0) -> None:
        self.poll_interval_s = poll_interval_s
        self.error_backoff_s = error_backoff_s
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_id: int = 0
        self.published_total: int = 0
        self.last_error: str | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # 初始 last_id = 当前 max(id), 重启不重推老数据
        store = get_sqlserver_store()
        loop = asyncio.get_event_loop()
        self._last_id = await loop.run_in_executor(None, store.get_max_id) or 0
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="javert-audit-watcher")
        logger.info(
            "AuditWatcher started, initial last_id=%s", self._last_id,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None
        logger.info("AuditWatcher stopped")

    async def _loop(self) -> None:
        loop = asyncio.get_event_loop()
        store = get_sqlserver_store()
        while not self._stop.is_set():
            try:
                rows = await loop.run_in_executor(
                    None, store.fetch_runs_since_id, self._last_id, 100,
                )
                for row in rows:
                    eligibility_evaluation = row.get("eligibility_evaluation")
                    is_new_p = not await loop.run_in_executor(
                        None, store.has_other_runs, row["patient_id"], row["run_id"],
                    )
                    await event_bus.publish("new_audit_run", {
                        "run_id": row["run_id"],
                        "patient_id": row["patient_id"],
                        "rule_id": row["rule_id"],
                        "verdict": row["verdict"],
                        "confidence": row["confidence"],
                        "eligibility_evaluation": eligibility_evaluation,
                        **_eligibility_sse_fields(eligibility_evaluation),
                        "public_explanation": present_public_explanation(
                            row, load_rule_meta().get(row["rule_id"]), []
                        ),
                        "promise": public_promise_summary(row.get("promise_trace")),
                        "is_new_patient": is_new_p,
                    })
                    self._last_id = int(row["id"])
                    self.published_total += 1
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)[:200]
                logger.warning("audit_watcher poll 失败: %s (backoff %ss)", e, self.error_backoff_s)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.error_backoff_s)
                    return
                except asyncio.TimeoutError:
                    continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
                return
            except asyncio.TimeoutError:
                continue


audit_watcher = AuditWatcher()


# =========================================================
# SSE 路由
# =========================================================
router = APIRouter(tags=["sse"])


@router.get("/sse/reviews")
async def sse_reviews(request: Request):
    if session_user_id(request) is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="未登录")

    q = await event_bus.subscribe()

    async def event_gen():
        try:
            # 启动 hello
            yield {"event": "hello", "data": json.dumps({
                "subscribers": event_bus.subscriber_count,
            })}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": json.dumps({
                        "ts": datetime.utcnow().isoformat(),
                    })}
        finally:
            await event_bus.unsubscribe(q)

    return EventSourceResponse(event_gen())
