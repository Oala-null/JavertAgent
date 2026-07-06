# -*- coding: utf-8 -*-
"""审计路由 — SSE 流式跑 audit + 历史查询.

GET /api/audit/run?rule_id=R191&patient_id=K23895
    → text/event-stream
       events: start / trace / result / error

GET /api/audit/runs?rule_id=&patient_id=&limit=50
GET /api/audit/runs/{run_id}
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from javert.audit.rule_loader import load_rule
from javert.audit.runner import Runner
from javert.config import get_config
from javert.store.audit_store import SqliteStore
from javert.store.result_persister import persist_one
from javert.tools.llm_provider import LlmUnavailableError
from javert.tools.registry import build_executor

from .routes_workbench import _get_loader
from .schemas import AuditRunDetail, AuditRunSummary

logger = logging.getLogger("javert.web.routes_audit")

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _format_sse(event: str, data: Any) -> str:
    """SSE 格式化: event + data (JSON)."""
    if not isinstance(data, (dict, list)):
        data = {"msg": str(data)}
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _result_payload(result: Any) -> dict:
    """AuditResult → result 事件 payload（单条 / 批量共用）。"""
    return {
        "run_id": result.run_id,
        "rule_id": result.rule_id,
        "patient_id": result.patient_id,
        "verdict": result.verdict,
        "confidence": result.confidence,
        "reasoning": result.reasoning,
        "evidence": [e.model_dump() for e in result.evidence],
        "tool_calls": [tc.model_dump() for tc in result.tool_calls],
        "duration_ms": result.duration_ms,
        "model": result.model,
        "started_at": result.started_at.isoformat(),
    }


@router.get("/run")
async def run_audit(
    rule_id: str = Query(..., description="规则 ID, 如 R191"),
    patient_id: str = Query(..., description="患者住院号"),
):
    """SSE 流式跑单条审计.

    Events:
        - start: {rule_id, patient_id, started_at}
        - trace: {msg} — 每轮 LLM/工具调用
        - result: {run_id, verdict, confidence, reasoning, evidence, tool_calls, ...}
        - fail: {type, message}  (注意: 不用 'error', 浏览器 EventSource 保留)
    """
    cfg = get_config()
    yaml_path = cfg.rules_path / f"{rule_id}.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"rule {rule_id} not found")
    rule = load_rule(yaml_path)

    msg_queue: queue.Queue = queue.Queue()

    def emit(msg: str) -> None:
        msg_queue.put(("trace", {"msg": msg}))

    # 复用 workbench 进程单例 loader (含 data_import overlay) — 每请求新建 CsvLoader 会冷读 300MB+ CSV
    loader = _get_loader()
    executor = build_executor(loader, cfg)
    runner = Runner(executor=executor, config=cfg, emit=emit, loader=loader)

    async def event_generator():
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(None, lambda: runner.audit(rule, patient_id))

        # start event
        yield _format_sse(
            "start",
            {
                "rule_id": rule.rule_id,
                "rule_question": rule.question,
                "rule_status": rule.status,
                "patient_id": patient_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "model": cfg.llm_model,
            },
        )

        # poll trace queue 直到 future 完成
        while True:
            try:
                kind, payload = msg_queue.get(timeout=0.05)
                yield _format_sse(kind, payload)
            except queue.Empty:
                if future.done():
                    break
                await asyncio.sleep(0.05)

        # drain 残留
        while not msg_queue.empty():
            try:
                kind, payload = msg_queue.get_nowait()
                yield _format_sse(kind, payload)
            except queue.Empty:
                break

        try:
            result = future.result()
        except LlmUnavailableError as exc:
            yield _format_sse("fail", {"type": "LlmUnavailable", "message": str(exc)})
            return
        except Exception as exc:
            logger.exception("audit failed: rule=%s patient=%s", rule_id, patient_id)
            yield _format_sse(
                "fail",
                {"type": exc.__class__.__name__, "message": str(exc)},
            )
            return

        # 持久化: 本地 SQLite + 立即推 142 + mark sync state
        state = persist_one(result, rule, triggered_by="web")

        yield _format_sse(
            "result",
            {
                "run_id": result.run_id,
                "rule_id": result.rule_id,
                "patient_id": result.patient_id,
                "verdict": result.verdict,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "evidence": [e.model_dump() for e in result.evidence],
                "tool_calls": [tc.model_dump() for tc in result.tool_calls],
                "duration_ms": result.duration_ms,
                "model": result.model,
                "started_at": result.started_at.isoformat(),
                "persisted": {
                    "sqlite": state["sqlite"],
                    "sqlserver_142": state["sqlserver_142"],
                    "sync_state": state["sync_state"],
                },
            },
        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# =========================================================
# 批量审计 (供 2c 平台 BFF 点菜调用，US-011)
# =========================================================
class RunBatchRequest(BaseModel):
    patient_id: str
    rules: list[str]


@router.post("/run-batch")
async def run_audit_batch(req: RunBatchRequest):
    """SSE 流式批量跑 (patient_id × 多规则)，供 BFF javert 适配器点菜调用。

    Events:
        - start:  {patient_id, rules, total}
        - trace:  {msg}                — 每轮 LLM/工具调用（进度中继）
        - result: {run_id, verdict, ...} — 每条规则一个（CLEAN/VIOLATION/INCONCLUSIVE）
        - fail:   {type, message, rule_id} — 单条失败不中断整批
        - done:   {total, completed}
    复用 Runner.audit + 共享 executor 工具缓存（一个病案多规则只检索一次文书/费用）。
    """
    cfg = get_config()
    rules = []
    for rid in req.rules:
        yaml_path = cfg.rules_path / f"{rid}.yaml"
        if yaml_path.exists():
            rules.append(load_rule(yaml_path))

    msg_queue: queue.Queue = queue.Queue()

    def emit(msg: str) -> None:
        msg_queue.put(("trace", {"msg": msg}))

    loader = _get_loader()  # 进程单例 (含 overlay), 不再每请求冷读 CSV
    executor = build_executor(loader, cfg)  # 共享：同病案多规则复用工具缓存
    runner = Runner(executor=executor, config=cfg, emit=emit, loader=loader)

    async def event_generator():
        yield _format_sse("start", {
            "patient_id": req.patient_id,
            "rules": [r.rule_id for r in rules],
            "total": len(rules),
        })
        loop = asyncio.get_event_loop()
        completed = 0
        for rule in rules:
            future = loop.run_in_executor(None, lambda r=rule: runner.audit(r, req.patient_id))
            while True:
                try:
                    kind, payload = msg_queue.get(timeout=0.05)
                    yield _format_sse(kind, payload)
                except queue.Empty:
                    if future.done():
                        break
                    await asyncio.sleep(0.05)
            while not msg_queue.empty():
                try:
                    kind, payload = msg_queue.get_nowait()
                    yield _format_sse(kind, payload)
                except queue.Empty:
                    break
            try:
                result = future.result()
            except LlmUnavailableError as exc:
                yield _format_sse("fail", {"type": "LlmUnavailable", "message": str(exc), "rule_id": rule.rule_id})
                continue
            except Exception as exc:
                logger.exception("batch audit failed: rule=%s patient=%s", rule.rule_id, req.patient_id)
                yield _format_sse("fail", {"type": exc.__class__.__name__, "message": str(exc), "rule_id": rule.rule_id})
                continue
            # 持久化（本地 SQLite + 142），与单条一致
            try:
                persist_one(result, rule, triggered_by="bff-batch")
            except Exception:
                logger.exception("persist failed: run=%s", result.run_id)
            completed += 1
            yield _format_sse("result", _result_payload(result))
        yield _format_sse("done", {"total": len(rules), "completed": completed})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# =========================================================
# 历史查询
# =========================================================
@router.get("/runs", response_model=list[AuditRunSummary])
def list_audit_runs(
    rule_id: str | None = Query(None),
    patient_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> list[AuditRunSummary]:
    """按时间倒序拉最近 N 条审计 (本地 SQLite)."""
    cfg = get_config()
    db_path = cfg.audit_db_path
    if not db_path.exists():
        return []

    where_parts: list[str] = []
    params: list[Any] = []
    if rule_id is not None:
        where_parts.append("rule_id = ?")
        params.append(rule_id)
    if patient_id is not None:
        where_parts.append("patient_id = ?")
        params.append(patient_id)
    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        sql = (
            "SELECT run_id, rule_id, patient_id, verdict, confidence, "
            "       duration_ms, model, started_at "
            f"FROM audit_runs{where_clause} "
            "ORDER BY started_at DESC LIMIT ?"
        )
        params.append(limit)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()

    out: list[AuditRunSummary] = []
    for row in rows:
        try:
            started = datetime.fromisoformat(row["started_at"])
        except Exception:
            started = datetime.now(timezone.utc)
        out.append(
            AuditRunSummary(
                run_id=row["run_id"],
                rule_id=row["rule_id"],
                patient_id=row["patient_id"],
                verdict=row["verdict"],
                confidence=row["confidence"] or 0.0,
                duration_ms=row["duration_ms"] or 0,
                model=row["model"] or "",
                started_at=started,
            )
        )
    return out


@router.get("/runs/{run_id}", response_model=AuditRunDetail)
def get_audit_run(run_id: str) -> AuditRunDetail:
    """单条 audit 详情 (展开 evidence + tool_calls)."""
    cfg = get_config()
    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        result = store.find_by_run_id(run_id)
    finally:
        store.close()
    if result is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return AuditRunDetail(
        run_id=result.run_id,
        rule_id=result.rule_id,
        patient_id=result.patient_id,
        verdict=result.verdict,
        confidence=result.confidence,
        duration_ms=result.duration_ms,
        model=result.model,
        started_at=result.started_at,
        reasoning=result.reasoning,
        evidence=[e.model_dump() for e in result.evidence],
        tool_calls=[tc.model_dump() for tc in result.tool_calls],
    )
