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
import copy
import hashlib
import json
import logging
import queue
import secrets
import sqlite3
import threading
import time
import weakref
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from javert.audit.rule_loader import load_all, load_rule
from javert.audit.runner import Runner
from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.routing import RuleRouter, build_patient_record_for_router, default_shi_zd_path
from javert.store.audit_store import SqliteStore
from javert.store.result_persister import persist_one
from javert.tools.llm_provider import LlmUnavailableError
from javert.tools.registry import build_executor
from javert.web.hit_resolver import load_kb_drugs, resolve_hits_from_json
from javert.web.reasoning_zh import humanize_reasoning
from javert.web.rule_meta import load_rule_meta
from javert.web.public_presenter import (
    present_public_explanation,
    public_promise_summary,
)

from .routes_workbench import _get_loader
from .schemas import AuditRunDetail, AuditRunSummary

logger = logging.getLogger("javert.web.routes_audit")
outbound_logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _format_sse(event: str, data: Any) -> str:
    """SSE 格式化: event + data (JSON)."""
    if not isinstance(data, (dict, list)):
        data = {"msg": str(data)}
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _result_payload(result: Any) -> dict:
    """AuditResult → result 事件 payload（单条 / 批量共用）。"""
    meta = load_rule_meta().get(result.rule_id)
    return {
        "run_id": result.run_id,
        "rule_id": result.rule_id,
        "handling_level": meta["handling_level"] if meta else None,
        "patient_id": result.patient_id,
        "verdict": result.verdict,
        "confidence": result.confidence,
        "reasoning": result.reasoning,
        "evidence": [e.model_dump() for e in result.evidence],
        "tool_calls": [tc.model_dump() for tc in result.tool_calls],
        "duration_ms": result.duration_ms,
        "model": result.model,
        "started_at": result.started_at.isoformat(),
        "eligibility_evaluation": (
            result.eligibility_evaluation.model_dump(mode="json")
            if result.eligibility_evaluation is not None
            else None
        ),
        "public_explanation": present_public_explanation(result, meta, []),
        "promise": public_promise_summary(result.promise_trace),
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
        state = persist_one(
            result, rule, triggered_by="web", source_loader=loader,
        )

        payload = _result_payload(result)
        payload["persisted"] = {
            "sqlite": state["sqlite"],
            "sqlserver_142": state["sqlserver_142"],
            "sync_state": state["sync_state"],
        }
        yield _format_sse("result", payload)

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
        - result: {run_id, verdict, ...} — 每条规则一个（CLEAN/VIOLATION/INCONCLUSIVE），
                  仅在裁决成功落库 (sqlite) 后发出
        - fail:   {type, message, rule_id, stage} — 单条失败不中断整批;
                  stage ∈ audit(裁决失败) / persist(落库失败, 不再发 result) /
                  unknown_rule(请求了不存在的 rule_id)
        - done:   {total, completed}
    契约只加不改 (harden-onsite-redlines): 老字段原样, 新增 stage + unknown_rule/persist 语义.
    复用 Runner.audit + 共享 executor 工具缓存（一个病案多规则只检索一次文书/费用）。
    """
    cfg = get_config()
    rules = []
    unknown_rule_ids: list[str] = []
    for rid in req.rules:
        yaml_path = cfg.rules_path / f"{rid}.yaml"
        if yaml_path.exists():
            rules.append(load_rule(yaml_path))
        else:
            unknown_rule_ids.append(rid)

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
        # 未知 rule_id 逐条显式回执 — BFF 可区分「规则不存在」与「漏返回」
        for rid in unknown_rule_ids:
            yield _format_sse("fail", {
                "type": "UnknownRule",
                "message": f"rule {rid} not found",
                "rule_id": rid,
                "stage": "unknown_rule",
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
                yield _format_sse("fail", {"type": "LlmUnavailable", "message": str(exc), "rule_id": rule.rule_id, "stage": "audit"})
                continue
            except Exception as exc:
                logger.exception("batch audit failed: rule=%s patient=%s", rule.rule_id, req.patient_id)
                yield _format_sse("fail", {"type": exc.__class__.__name__, "message": str(exc), "rule_id": rule.rule_id, "stage": "audit"})
                continue
            # 持久化（本地 SQLite + 142），与单条一致.
            # persist 成功才发 result — 否则 BFF 拿到库中不存在的 run (真丢数窗口)
            try:
                persist_one(
                    result, rule, triggered_by="bff-batch", source_loader=loader,
                )
            except Exception as exc:
                logger.exception("persist failed: run=%s", result.run_id)
                yield _format_sse("fail", {
                    "type": exc.__class__.__name__, "message": str(exc),
                    "rule_id": rule.rule_id, "stage": "persist",
                })
                continue
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
    migration_store = SqliteStore(db_path)
    try:
        migration_store.init_schema()
    finally:
        migration_store.close()

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
            "       duration_ms, model, started_at, eligibility_json "
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
                audit_disposition=(
                    (json.loads(row["eligibility_json"]) or {}).get(
                        "audit_disposition"
                    )
                    if row["eligibility_json"]
                    else None
                ),
                eligibility_status=(
                    (json.loads(row["eligibility_json"]) or {}).get(
                        "eligibility_status"
                    )
                    if row["eligibility_json"]
                    else None
                ),
            )
        )
    return out


# =========================================================
# 2C 平台系统间对接 (契约: docs/2c对接_javert审计服务.md)
#   POST /api/audit/submit          — 患者名单 → 202 受理回执, 后台跑审计
#   GET  /api/audit/results/{SYXH}  — 轮询拉裁决 (running 时增量可见)
#   POST /api/audit/v2/submit       — v2 同入参/同任务, 独立卡片契约
#   GET  /api/audit/v2/results/{SYXH}
# 免鉴权: middleware PUBLIC_PREFIXES 只放行上述精确路径, 仅限内网.
# =========================================================

_VERDICT_LABEL = {"VIOLATION": "违规", "INCONCLUSIVE": "待人工复核", "CLEAN": "合规"}
_2C_RULE_CONCURRENCY = 5
_2C_STAGE_ERROR_CODE = {
    "hub_fetch": "HUB_FETCH_FAILED",
    "routing": "ROUTING_FAILED",
    "audit": "AUDIT_FAILED",
}

# ponytail: 进程内任务表, 重启后历史提交回 unknown (裁决本体仍在 sqlite/工作台);
# 需要跨重启状态时把任务表落 sqlite
_2c_tasks: dict[str, dict] = {}
_2c_lock = threading.Lock()
_2c_queue: "queue.Queue[str]" = queue.Queue()
_2c_worker: threading.Thread | None = None

# v2/v3 卡片投影包含费用命中解析；同一 attempt/run 内结果不可变，使用有界 LRU
# 避免每次轮询从头扫描。只缓存进程内深拷贝，重启即清空。
_2C_CARD_CACHE_MAX = 2048
_2c_card_cache: "OrderedDict[tuple[str, str, str], dict[str, Any]]" = OrderedDict()
_2c_card_cache_lock = threading.Lock()
_2c_projection_locks: "weakref.WeakValueDictionary[str, Any]" = (
    weakref.WeakValueDictionary()
)
_2c_projection_locks_guard = threading.Lock()


def _2c_card_cache_get(
    api_version: str, projection_scope: str | None, run_id: str
) -> dict[str, Any] | None:
    if not projection_scope:
        return None
    key = (api_version, projection_scope, run_id)
    with _2c_card_cache_lock:
        card = _2c_card_cache.get(key)
        if card is None:
            return None
        _2c_card_cache.move_to_end(key)
        return copy.deepcopy(card)


def _2c_card_cache_put(
    api_version: str,
    projection_scope: str | None,
    run_id: str,
    card: dict[str, Any],
) -> None:
    if not projection_scope:
        return
    key = (api_version, projection_scope, run_id)
    with _2c_card_cache_lock:
        _2c_card_cache[key] = copy.deepcopy(card)
        _2c_card_cache.move_to_end(key)
        while len(_2c_card_cache) > _2C_CARD_CACHE_MAX:
            _2c_card_cache.popitem(last=False)


def _clear_2c_card_cache() -> None:
    """测试/重启边界使用；不得输出缓存键或业务内容。"""
    with _2c_card_cache_lock:
        _2c_card_cache.clear()
    with _2c_projection_locks_guard:
        _2c_projection_locks.clear()


def _2c_projection_scope(
    attempt_id: str | None, items: list[dict[str, Any]]
) -> str | None:
    """当前 attempt 直接隔离；重启后的历史回放按 run 快照摘要隔离。"""
    if attempt_id:
        return attempt_id
    run_ids = sorted(str(item.get("run_id") or "") for item in items)
    run_ids = [run_id for run_id in run_ids if run_id]
    if not run_ids:
        return None
    digest = hashlib.sha256("\0".join(run_ids).encode("utf-8")).hexdigest()[:24]
    return f"history_{digest}"


def _2c_projection_build_lock(projection_scope: str | None) -> Any:
    """同一 attempt/历史快照共用弱引用单飞锁，不随患者数无限增长。"""
    if not projection_scope:
        return threading.Lock()
    with _2c_projection_locks_guard:
        build_lock = _2c_projection_locks.get(projection_scope)
        if build_lock is None:
            build_lock = threading.Lock()
            _2c_projection_locks[projection_scope] = build_lock
        return build_lock


class SubmitItem(BaseModel):
    SYXH: str
    YLZZJGDM: str = ""


def _new_2c_attempt_id() -> str:
    """生成不含患者标识的 2C attempt id；仅用于关联本次进程内任务。"""
    return f"att_{secrets.token_urlsafe(9)}"


def _2c_result_diagnostic(reasoning: str) -> tuple[str, str, bool]:
    """内部 Runner reason → 2C 可展示文本 + 稳定机器码。"""
    lower = reasoning.lower()
    if "truncated" in lower and ("verdict" in lower or "output" in lower):
        return (
            "模型输出达到长度上限，本规则未完成自动判定，需人工复核。",
            "LLM_OUTPUT_TRUNCATED",
            True,
        )
    if "malformed" in lower and "verdict" in lower:
        return (
            "模型输出格式异常，本规则未完成自动判定，需人工复核。",
            "LLM_OUTPUT_MALFORMED",
            True,
        )
    return humanize_reasoning(reasoning), "", False


# ─── 142 数据中台兜底 (2C 提交真实患者, 本地 CSV 查无 → hub 取数入审) ───
_2C_HUB_CACHE = "output/hub_cache_2c"  # 每患者一目录, 每次提交重取保新鲜


def _hub_source_candidates(cfg):
    """默认 Hub + 已声明 profile，并保留命中 profile 的来源标签。"""
    candidates = [(cfg, cfg.hub_table_prefix, None)]
    seen = {(getattr(cfg, "hub_database", ""), cfg.hub_table_prefix)}
    for tag, profile in (getattr(cfg, "hub_raw_profiles", {}) or {}).items():
        key = (profile.database, profile.table_prefix)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((
            cfg.model_copy(update={
                "hub_database": profile.database,
                "hub_table_prefix": profile.table_prefix,
            }),
            profile.table_prefix,
            tag,
        ))
    return candidates


def _hub_probe(syxh: str) -> bool:
    """中台任一已配置表族是否有患者费用；全部连接失败才报依赖故障。"""
    from javert.data import hub_source as hs
    cfg = get_config()
    successful_queries = 0
    last_error = None
    for source_cfg, prefix, _source_tag in _hub_source_candidates(cfg):
        cn = None
        try:
            cn = hs.connect(source_cfg, timeout=10)
            yq2org = hs.fetch_hospital_map(cn, table_prefix=prefix)
            fees = hs.fetch_fees(cn, [syxh], yq2org, table_prefix=prefix)
            successful_queries += 1
            if len(fees) > 0:
                return True
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        finally:
            if cn is not None:
                cn.close()
    if successful_queries == 0 and last_error is not None:
        raise last_error
    return False


def _hub_fetch_patient(syxh: str, out) -> str | None:
    """中台 → out/ 6 CSV，并返回命中 profile 的 batch tag。"""
    from javert.data import hub_source as hs
    cfg = get_config()
    out.mkdir(parents=True, exist_ok=True)
    candidates = _hub_source_candidates(cfg)
    selected = None
    last_error = None
    for source_cfg, prefix, source_tag in candidates:
        cn = None
        try:
            cn = hs.connect(source_cfg)
            yq2org = hs.fetch_hospital_map(cn, table_prefix=prefix)
            fees = hs.fetch_fees(cn, [syxh], yq2org, table_prefix=prefix)
            if len(fees) > 0:
                selected = (cn, prefix, source_tag, yq2org, fees)
                break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        if cn is not None:
            cn.close()
    if selected is None:
        # 保留历史行为：即使患者为空也产出六个空快照；连接全失败时才抛错。
        source_cfg, prefix, source_tag = candidates[0]
        try:
            cn = hs.connect(source_cfg)
            yq2org = hs.fetch_hospital_map(cn, table_prefix=prefix)
            fees = hs.fetch_fees(cn, [syxh], yq2org, table_prefix=prefix)
            selected = (cn, prefix, source_tag, yq2org, fees)
        except Exception:
            if last_error is not None:
                raise last_error
            raise
    cn, prefix, source_tag, yq2org, fees = selected
    try:
        kw = {"index": False, "encoding": "utf-8-sig"}
        fees.to_csv(out / "shi_fee.csv", **kw)
        hs.fetch_notes(cn, [syxh], table_prefix=prefix).to_csv(
            out / "case_notes.csv", **kw
        )
        hs.fetch_zd(cn, [syxh], yq2org, table_prefix=prefix).to_csv(
            out / "shi_zd.csv", **kw
        )
        hs.fetch_ss(cn, [syxh], yq2org, table_prefix=prefix).to_csv(
            out / "shi_ss.csv", **kw
        )
        hs.fetch_labs(cn, [syxh], table_prefix=prefix).to_csv(
            out / "lab_results.csv", **kw
        )
        hs.fetch_exams(cn, [syxh], table_prefix=prefix).to_csv(
            out / "examinations.csv", **kw
        )
    finally:
        cn.close()
    return source_tag


def _hub_cfg_for(cfg, data_dir) -> Any:
    """cfg 副本切到 hub 取数目录 — Runner/工具/临床闸整链路跟随 (文件名=取数桥产出)."""
    # 六个文件名全部显式覆盖 — 部署机 .env 可能把 notes/fees 指到合并版
    # (如 shi_fee_with_szx.csv), 不覆盖会去取数目录找不存在的文件 (62 实测踩过)
    return cfg.model_copy(update={
        "data_dir": str(data_dir),
        "notes_file": "case_notes.csv",
        "fees_file": "shi_fee.csv",
        "zd_file": "shi_zd.csv",
        "ss_file": "shi_ss.csv",
        "labs_file": "lab_results.csv",
        "examinations_file": "examinations.csv",
    })


def _2c_run_patient(syxh: str) -> None:
    """单患者全流程: (hub 患者先取数) → ready 全集 → router 预筛 → 规则并发 5 → 逐条落库."""
    cfg = get_config()
    batch_tag = None
    with _2c_lock:
        source = _2c_tasks[syxh].get("source", "local")
    if source == "hub":
        with _2c_lock:
            _2c_tasks[syxh]["stage"] = "hub_fetch"
        hub_dir = cfg.resolve(_2C_HUB_CACHE) / syxh
        batch_tag = _hub_fetch_patient(syxh, hub_dir)
        cfg = _hub_cfg_for(cfg, hub_dir)
        loader = CsvLoader(cfg.notes_path, cfg.fees_path)
        zd_path = cfg.zd_path
        with _2c_lock:
            _2c_tasks[syxh]["data_dir"] = str(hub_dir)
    else:
        loader = _get_loader()
        zd_path = default_shi_zd_path()
    with _2c_lock:
        _2c_tasks[syxh]["stage"] = "routing"
    ready = [r for r in load_all(cfg.rules_path).values() if r.status == "ready"]

    rule_router = RuleRouter.from_defaults(enabled_priorities=("P0", "P1", "P2", "P3"))
    record = build_patient_record_for_router(
        syxh, loader, shi_zd_path=zd_path if zd_path.exists() else None,
    )
    final_set = set(rule_router.route(record).final_rules)
    selected = sorted((r for r in ready if r.rule_id in final_set), key=lambda r: r.rule_id)

    with _2c_lock:
        _2c_tasks[syxh]["total"] = len(selected)
    if not selected:
        return  # router 判定无可疑规则 → done, 0 条 (病案干净)

    with _2c_lock:
        _2c_tasks[syxh]["stage"] = "audit"
    executor = build_executor(loader, cfg)
    runner = Runner(executor=executor, config=cfg, emit=lambda _m: None, loader=loader)
    store = SqliteStore(cfg.audit_db_path)
    store.init_schema()
    runner.executor.set_patient_context(syxh)
    try:
        with ThreadPoolExecutor(max_workers=min(_2C_RULE_CONCURRENCY, len(selected))) as pool:
            futs = {
                pool.submit(
                    runner.audit, rule, syxh,
                    reset_cache=False, manage_patient_context=False,
                ): rule
                for rule in selected
            }
            for fut in as_completed(futs):
                rule = futs[fut]
                try:
                    result = fut.result()
                    persist_one(
                        result, rule,
                        triggered_by="2c-submit",
                        sqlite_store=store,
                        batch_tag=batch_tag,
                        source_loader=loader,
                    )
                except Exception as exc:  # noqa: BLE001 — 单条失败不中断整患者
                    logger.warning("2c audit rule failed: %s %s: %s", syxh, rule.rule_id, exc)
                    with _2c_lock:
                        _2c_tasks[syxh]["failed"].append(rule.rule_id)
                    continue
                with _2c_lock:
                    _2c_tasks[syxh]["run_ids"].append(result.run_id)
    finally:
        runner.executor.clear_patient_context()
        store.close()


def _2c_worker_loop() -> None:
    """单 worker 逐患者跑 (患者间串行避免 GPU 争抢, 患者内规则并发 5)."""
    while True:
        syxh = _2c_queue.get()
        try:
            _2c_run_patient(syxh)
        except Exception as exc:  # noqa: BLE001
            logger.exception("2c audit patient failed: %s", syxh)
            with _2c_lock:
                if syxh in _2c_tasks:
                    task = _2c_tasks[syxh]
                    task["error"] = f"审计中断: {exc.__class__.__name__}"
                    task["error_code"] = _2C_STAGE_ERROR_CODE.get(
                        task.get("stage", ""), "AUDIT_FAILED"
                    )
                    task["retryable"] = True
                    task["outcome"] = "failed"
        finally:
            with _2c_lock:
                if syxh in _2c_tasks:
                    task = _2c_tasks[syxh]
                    if task.get("outcome") != "failed":
                        if task.get("failed"):
                            task["outcome"] = (
                                "partial" if task.get("run_ids") else "failed"
                            )
                            task["error_code"] = "RULE_FAILURES"
                            task["retryable"] = True
                        else:
                            task["outcome"] = "succeeded"
                            task["error_code"] = ""
                            task["retryable"] = False
                    task["stage"] = "done"
                    task["status"] = "done"


def _2c_ensure_worker() -> None:
    global _2c_worker
    with _2c_lock:
        if _2c_worker is not None and _2c_worker.is_alive():
            return
        _2c_worker = threading.Thread(target=_2c_worker_loop, daemon=True, name="audit-2c")
        _2c_worker.start()


def _submit_2c(items: list[SubmitItem]) -> dict[str, list[dict]]:
    """2C v1/v2 共用提交：同一任务表、队列与 attempt 幂等语义。"""
    loader = None
    accepted: list[dict] = []
    rejected: list[dict] = []
    for item in items:
        syxh = item.SYXH.strip()
        if not syxh:
            rejected.append({"SYXH": item.SYXH, "reason": "SYXH 为空"})
            continue
        # 幂等命中必须先于数据源探测：当前 attempt 已在执行时，数据源瞬时故障
        # 不应把重复提交误判为 rejected。入队前仍会在锁内二次检查并发竞态。
        with _2c_lock:
            existing = _2c_tasks.get(syxh)
            if existing is not None and existing["status"] == "running":
                accepted.append({
                    "SYXH": syxh,
                    "source": existing.get("source", "local"),
                    "attempt_id": existing.get("attempt_id"),
                })
                continue
        if loader is None:
            loader = _get_loader()
        try:
            has_data = len(loader.get_notes(syxh)) > 0 or len(loader.get_fees(syxh)) > 0
        except Exception:  # noqa: BLE001
            has_data = False
        source = "local"
        if not has_data:
            # 本地 CSV 查无 → 142 数据中台兜底 (真实院内患者数据源头在中台)
            try:
                if _hub_probe(syxh):
                    source = "hub"
                else:
                    rejected.append({"SYXH": syxh, "reason": "本地与数据中台均查无此患者"})
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("2c hub probe 失败 %s: %s", syxh, exc)
                rejected.append({"SYXH": syxh, "reason": "本地查无, 数据中台连接失败"})
                continue
        with _2c_lock:
            existing = _2c_tasks.get(syxh)
            if existing is not None and existing["status"] == "running":
                accepted.append({
                    "SYXH": syxh,
                    "source": existing.get("source", "local"),
                    "attempt_id": existing.get("attempt_id"),
                })
                continue  # 已在跑, 幂等受理
            attempt_id = _new_2c_attempt_id()
            _2c_tasks[syxh] = {
                "YLZZJGDM": item.YLZZJGDM,
                "status": "running",
                "outcome": "running",
                "attempt_id": attempt_id,
                "source": source,
                "stage": "accepted",
                "total": None,
                "run_ids": [],
                "failed": [],
                "error_code": "",
                "retryable": False,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }
        _2c_queue.put(syxh)
        accepted.append({"SYXH": syxh, "source": source, "attempt_id": attempt_id})
    if accepted:
        _2c_ensure_worker()
    return {"accepted": accepted, "rejected": rejected}


@router.post("/submit", status_code=202)
def submit_2c(items: list[SubmitItem]):
    """2C v1 提交：保留既有路径和响应。"""
    return _submit_2c(items)


@router.post("/v2/submit", status_code=202)
def submit_2c_v2(items: list[SubmitItem]):
    """2C v2 提交：入参与执行语义复用 v1，仅结果契约升级。"""
    return _submit_2c(items)


@router.post("/v3/submit", status_code=202)
def submit_2c_v3(items: list[SubmitItem]):
    """2C v3 提交：继续复用同一任务、队列和 attempt 幂等语义。"""
    return _submit_2c(items)


def _latest_runs_for_patient(syxh: str) -> list[str]:
    """sqlite 兜底: 该患者每条规则的最新 run_id (重启后任务表丢失时供 results 回放)."""
    cfg = get_config()
    if not cfg.audit_db_path.exists():
        return []
    try:
        with sqlite3.connect(cfg.audit_db_path) as conn:
            rows = conn.execute(
                "SELECT run_id FROM audit_runs a WHERE patient_id = ? AND started_at = ("
                "  SELECT MAX(started_at) FROM audit_runs b"
                "  WHERE b.patient_id = a.patient_id AND b.rule_id = a.rule_id)",
                (syxh,),
            ).fetchall()
        return [r[0] for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("2c sqlite 历史回放失败 %s: %s", syxh, exc)
        return []


def _results_2c_payload(syxh: str, *, include_hits: bool) -> dict[str, Any]:
    """构建 v1 基础结果；v2/v3 会自行投影完整 hits，跳过被丢弃的 v1 hits。"""
    with _2c_lock:
        task = _2c_tasks.get(syxh)
        status = task["status"] if task else "unknown"
        ylzzjgdm = task["YLZZJGDM"] if task else ""
        total = task["total"] if task else None
        run_ids = list(task["run_ids"]) if task else []
        data_dir = task.get("data_dir") if task else None
        error = task.get("error", "") if task else ""
        attempt_id = task.get("attempt_id") if task else None
        failed_count = len(task.get("failed", [])) if task else 0
        error_code = task.get("error_code", "") if task else ""
        retryable = bool(task.get("retryable", False)) if task else False
        if task:
            outcome = task.get("outcome") or (
                "running" if status == "running"
                else "failed" if error
                else "partial" if failed_count
                else "succeeded"
            )
        else:
            outcome = "unknown"

    if task is None:
        # 服务重启后任务表清空, 但裁决本体在 sqlite — 回退查历史 (每规则最新一条),
        # 有则按 done 返回, 2C 不必因我方重启而重跑
        run_ids = _latest_runs_for_patient(syxh)
        if run_ids:
            status = "done"
            outcome = "succeeded"
            total = len(run_ids)
            hub_dir = get_config().resolve(_2C_HUB_CACHE) / syxh
            if hub_dir.exists():
                data_dir = str(hub_dir)

    results: list[dict] = []
    if run_ids:
        metas = load_rule_meta()
        cfg = get_config()
        fee_df = None
        kb_drugs = None
        if include_hits:
            try:
                if data_dir:  # hub 患者: fee 行在取数目录, 不在全局 CSV
                    from pathlib import Path as _P
                    fee_df = CsvLoader(
                        _P(data_dir) / "case_notes.csv", _P(data_dir) / "shi_fee.csv"
                    ).get_fees(syxh)
                else:
                    fee_df = _get_loader().get_fees(syxh)
            except Exception:  # noqa: BLE001
                fee_df = None
            kb_drugs = load_kb_drugs()
        store = SqliteStore(cfg.audit_db_path)
        try:
            store.init_schema()
            for rid in run_ids:
                r = store.find_by_run_id(rid)
                if r is None:
                    continue
                meta = metas.get(r.rule_id)
                display_reasoning, diagnostic_code, result_retryable = (
                    _2c_result_diagnostic(r.reasoning)
                )
                # 命中项目 (确定性, 复用工作台 hit_resolver): V/I 才算, 给 2C 侧
                # join 自己的费用明细 (code_nat=国家医保码 / matched_fee_name=明细原始项目名)
                hits: list[dict] = []
                hit_items: list[Any] = []
                hit_codes: list[str] = []
                hit_names: list[str] = []
                if include_hits and r.verdict in ("VIOLATION", "INCONCLUSIVE"):
                    hit_items = resolve_hits_from_json(
                        json.dumps([e.model_dump() for e in r.evidence], ensure_ascii=False),
                        json.dumps(
                            [tc.model_dump() for tc in r.tool_calls],
                            ensure_ascii=False, default=str,
                        ),
                        meta["drug_rule_type"] if meta else None,
                        fee_df,
                        kb_drugs,
                    )
                    hits = [
                        {
                            "source": h.source,
                            "name": h.name,
                            "code_nat": h.code_nat,
                            "code_local": h.code_local,
                            "matched_fee_name": h.matched_fee_name,
                            "restriction": h.restriction,
                            "review_note": h.review_note,
                        }
                        for h in hit_items
                    ]
                    # 顶层扁平键 (2C 直接挂明细用): 仅费用/药品命中, 去重保序
                    for h in hit_items:
                        if h.source not in ("fee", "drug"):
                            continue
                        code = h.code_nat or h.code_local
                        name = h.matched_fee_name or h.name
                        if code and code not in hit_codes:
                            hit_codes.append(code)
                        if name and name not in hit_names:
                            hit_names.append(name)
                results.append({
                    "run_id": r.run_id,
                    "rule_id": r.rule_id,
                    "rule_name": meta["violation_type"] if meta else "",
                    "handling_level": meta["handling_level"] if meta else None,
                    "behavior_name": meta["behavior_name"] if meta else "",
                    "behavior_code": meta["behavior_code"] if meta else "",
                    "verdict": r.verdict,
                    "verdict_label": _VERDICT_LABEL.get(r.verdict, r.verdict),
                    "confidence": r.confidence,
                    "reasoning": display_reasoning,
                    "diagnostic_code": diagnostic_code,
                    "retryable": result_retryable,
                    "eligibility_evaluation": (
                        r.eligibility_evaluation.model_dump(mode="json")
                        if r.eligibility_evaluation is not None
                        else None
                    ),
                    "public_explanation": present_public_explanation(
                        r, meta, hit_items
                    ),
                    "promise": public_promise_summary(r.promise_trace),
                    "evidence": [
                        {"source": e.source, "locator": e.locator, "text": e.text}
                        for e in r.evidence
                    ],
                    "hits": hits,
                    "hit_codes": hit_codes,
                    "hit_names": hit_names,
                    "finished_at": (
                        r.started_at + timedelta(milliseconds=r.duration_ms)
                    ).isoformat(),
                })
        finally:
            store.close()
    results.sort(key=lambda x: x["rule_id"])
    response_retryable = retryable or any(item["retryable"] for item in results)

    out: dict[str, Any] = {
        "SYXH": syxh,
        "YLZZJGDM": ylzzjgdm,
        "status": status,
        "outcome": outcome,
        "attempt_id": attempt_id,
        "error_code": error_code,
        "retryable": response_retryable,
        "progress": {
            "total": total if total is not None else len(results),
            "completed": len(run_ids),
            "failed": failed_count,
        },
        "summary": {
            "total": total if total is not None else len(results),
            "violation": sum(1 for x in results if x["verdict"] == "VIOLATION"),
            "inconclusive": sum(1 for x in results if x["verdict"] == "INCONCLUSIVE"),
            "clean": sum(1 for x in results if x["verdict"] == "CLEAN"),
        },
        "results": results,
    }
    if error:
        out["error"] = error
    return out


@router.get("/results/{syxh}")
def results_2c(syxh: str):
    """2C v1 查结果: unknown / running(增量) / done + summary + results[]."""
    return _results_2c_payload(syxh, include_hits=True)


_V2_PLACEHOLDER_TEXT = "暂未描述"


def _clean_v2_payload(value: Any) -> Any:
    """只清理 v2 展示副本，不改数据库、AuditResult 或 v1 响应。"""
    if isinstance(value, str):
        if _V2_PLACEHOLDER_TEXT not in value:
            return value
        return value.replace(_V2_PLACEHOLDER_TEXT, "").strip(" \t\r\n，,；;。")
    if isinstance(value, list):
        return [_clean_v2_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_v2_payload(item) for key, item in value.items()}
    return value


def _clean_fee_scalar(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null", "nat"} else text


def _format_v2_occurrence_time(value: Any) -> str:
    """费用发生时间统一为 2C 契约格式；不可解析值不原样泄露。"""
    text = _clean_fee_scalar(value)
    if not text:
        return ""
    try:
        parsed = pd.to_datetime(text, errors="raise")
    except (TypeError, ValueError, OverflowError):
        logger.warning("2c v2 忽略不可解析的 fee_ocur_time 值")
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _v2_fee_occurrence_times(hit: Any, fee_df: Any) -> list[str]:
    """已解析 fee/drug hit → 患者实际收费行的 fee_ocur_time（去重保序）。"""
    if (
        hit.source not in ("fee", "drug")
        or fee_df is None
        or len(fee_df) == 0
        or "medins_list_name" not in fee_df.columns
    ):
        return [""]
    matched_name = _clean_fee_scalar(hit.matched_fee_name)
    if not matched_name:
        return [""]
    rows = fee_df[
        fee_df["medins_list_name"].fillna("").astype(str).str.strip() == matched_name
    ]
    if hit.code_nat and "med_list_codg" in rows.columns:
        rows = rows[
            rows["med_list_codg"].fillna("").astype(str).str.strip() == hit.code_nat
        ]
    elif hit.code_local and "medins_list_codg" in rows.columns:
        rows = rows[
            rows["medins_list_codg"].fillna("").astype(str).str.strip()
            == hit.code_local
        ]
    if "cnt" in rows.columns:
        positive = rows[pd.to_numeric(rows["cnt"], errors="coerce").fillna(0) > 0]
        if len(positive):
            rows = positive
    if "fee_ocur_time" not in rows.columns:
        return [""]
    times: list[str] = []
    for value in rows["fee_ocur_time"].tolist():
        text = _format_v2_occurrence_time(value)
        if text and text not in times:
            times.append(text)
    return times or [""]


def _v2_hit_payloads(hit_items: list[Any], fee_df: Any) -> tuple[list[dict], list[dict]]:
    """返回完整 hits 与 fee/drug matched_items；后者按 code/name/time 去重。"""
    hits: list[dict] = []
    matched_items: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for hit in hit_items:
        occurrence_times = _v2_fee_occurrence_times(hit, fee_df)
        hits.append({
            "source": hit.source,
            "name": hit.name,
            "code_nat": hit.code_nat,
            "code_local": hit.code_local,
            "matched_fee_name": hit.matched_fee_name,
            "restriction": hit.restriction,
            "review_note": hit.review_note,
            "occurrence_times": occurrence_times if hit.source in ("fee", "drug") else [],
            "anchor": hit.anchor.model_dump(),
        })
        if hit.source not in ("fee", "drug"):
            continue
        # hit_resolver 为工作台追溯会保留“检索过但没有命中费用行”的锚点。
        # v2 matched_items 只表达患者实际收费项目，不能把 PTCA 等未命中检索词
        # 投影成 code/time 均为空的假命中。
        if not _clean_fee_scalar(hit.matched_fee_name):
            continue
        code = hit.code_nat or hit.code_local
        # Web 卡片主名称使用规范命中名；患者费用原文单独保留 matched_fee_name。
        name = hit.name or hit.matched_fee_name
        for occurrence_time in occurrence_times:
            key = (code, name, occurrence_time)
            if key in seen:
                continue
            seen.add(key)
            matched_items.append({
                "code": code,
                "name": name,
                "occurrence_time": occurrence_time,
                "source": hit.source,
                "code_nat": hit.code_nat,
                "code_local": hit.code_local,
                "matched_fee_name": hit.matched_fee_name,
                "restriction": hit.restriction,
                "review_note": hit.review_note,
            })
    return hits, matched_items


def _v2_applicability(verdict: str, raw_reasoning: str) -> tuple[str, str]:
    """在不扩展持久化三态的前提下，区分 CLEAN 与规则不适用。"""
    if verdict == "CLEAN" and "规则不适用" in (raw_reasoning or ""):
        return "NOT_APPLICABLE", "不适用"
    return "APPLICABLE", "适用"


def _log_v2_outbound(
    base: dict[str, Any],
    cards: list[dict],
    *,
    v1_results_count: int,
    cache_hits: int = 0,
    cache_misses: int = 0,
    build_ms: float = 0.0,
) -> None:
    """记录 2C v2 出口数量；禁止写患者号、run/attempt id 或业务原文。"""
    progress = base.get("progress") if isinstance(base.get("progress"), dict) else {}
    summary = base.get("summary") if isinstance(base.get("summary"), dict) else {}
    outbound_logger.info(
        "2c_v2_outbound status=%s outcome=%s total=%s completed=%s failed=%s "
        "v1_results=%d cards=%d violation=%s inconclusive=%s clean=%s "
        "not_applicable=%s matched_items=%d cache_hits=%d cache_misses=%d "
        "build_ms=%.1f",
        base.get("status"),
        base.get("outcome"),
        progress.get("total"),
        progress.get("completed"),
        progress.get("failed"),
        v1_results_count,
        len(cards),
        summary.get("violation"),
        summary.get("inconclusive"),
        summary.get("clean"),
        summary.get("not_applicable", 0),
        sum(len(card.get("matched_items", [])) for card in cards),
        cache_hits,
        cache_misses,
        build_ms,
    )


def _v2_fee_df(syxh: str):
    with _2c_lock:
        task = _2c_tasks.get(syxh)
        data_dir = task.get("data_dir") if task else None
    if not data_dir:
        hub_dir = get_config().resolve(_2C_HUB_CACHE) / syxh
        if hub_dir.exists():
            data_dir = str(hub_dir)
    try:
        if data_dir:
            from pathlib import Path as _P

            return CsvLoader(
                _P(data_dir) / "case_notes.csv", _P(data_dir) / "shi_fee.csv"
            ).get_fees(syxh)
        return _get_loader().get_fees(syxh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("2c v2 取 fee 失败 patient=%s: %s", syxh, exc)
        return None


@router.get("/v2/results/{syxh}")
def results_2c_v2(syxh: str):
    """2C v2：完整卡片 + CLEAN 命中 + code/name/time 关联对象。"""
    base = _results_2c_payload(syxh, include_hits=False)
    v1_results = base.pop("results", [])
    if not v1_results:
        _log_v2_outbound(base, [], v1_results_count=0)
        return _clean_v2_payload({
            "api_version": "2.0",
            **base,
            "cards": [],
        })
    attempt_id = base.get("attempt_id")
    projection_scope = _2c_projection_scope(attempt_id, v1_results)
    cards: list[dict] = []
    cache_hits = 0
    cache_misses = 0
    started = time.perf_counter()
    build_lock = _2c_projection_build_lock(projection_scope)
    with build_lock:
        fee_df = None
        fee_df_loaded = False
        metas = None
        kb_drugs = None
        store = None
        try:
            for item in v1_results:
                run_id = item["run_id"]
                cached = _2c_card_cache_get("2.0", projection_scope, run_id)
                if cached is not None:
                    cache_hits += 1
                    cards.append(cached)
                    continue

                cache_misses += 1
                if not fee_df_loaded:
                    fee_df = _v2_fee_df(syxh)
                    fee_df_loaded = True
                if metas is None:
                    metas = load_rule_meta()
                if kb_drugs is None:
                    kb_drugs = load_kb_drugs()
                if store is None:
                    store = SqliteStore(get_config().audit_db_path)
                    store.init_schema()

                run = store.find_by_run_id(run_id)
                if run is None:
                    continue
                meta = metas.get(run.rule_id)
                hit_items = resolve_hits_from_json(
                    json.dumps(
                        [e.model_dump() for e in run.evidence],
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        [tc.model_dump() for tc in run.tool_calls],
                        ensure_ascii=False,
                        default=str,
                    ),
                    meta["drug_rule_type"] if meta else None,
                    fee_df,
                    kb_drugs,
                )
                hits, matched_items = _v2_hit_payloads(hit_items, fee_df)
                category_code = meta["behavior_code"] if meta else ""
                category_title = meta["behavior_name"] if meta else ""
                applicability, applicability_label = _v2_applicability(
                    item["verdict"], run.reasoning
                )
                card = {
                    "card_id": run.run_id,
                    "run_id": run.run_id,
                    "rule_id": run.rule_id,
                    "handling_level": meta["handling_level"] if meta else None,
                    "title": category_title,
                    "description": meta["question"] if meta else "",
                    "category": {
                        "code": category_code,
                        "title": category_title,
                    },
                    "rule": {
                        "id": run.rule_id,
                        "name": meta["violation_type"] if meta else "",
                        "question": meta["question"] if meta else "",
                        "domain": meta["domain"] if meta else "",
                        "priority": meta["priority"] if meta else "",
                        "handling_level": meta["handling_level"] if meta else None,
                        "template": meta["template"] if meta else None,
                        "drug_rule_type": meta["drug_rule_type"] if meta else None,
                    },
                    "verdict": item["verdict"],
                    "verdict_label": (
                        "不适用"
                        if applicability == "NOT_APPLICABLE"
                        else item["verdict_label"]
                    ),
                    "applicability": applicability,
                    "applicability_label": applicability_label,
                    "confidence": item["confidence"],
                    "reasoning": item["reasoning"],
                    "diagnostic_code": item["diagnostic_code"],
                    "retryable": item["retryable"],
                    "matched_items": matched_items,
                    "hit_codes": [matched["code"] for matched in matched_items],
                    "hit_names": [matched["name"] for matched in matched_items],
                    "hit_times": [
                        matched["occurrence_time"] for matched in matched_items
                    ],
                    "hits": hits,
                    "evidence": item["evidence"],
                    "eligibility_evaluation": item["eligibility_evaluation"],
                    "public_explanation": present_public_explanation(
                        run, meta, hit_items
                    ),
                    "promise": public_promise_summary(run.promise_trace),
                    "finished_at": item["finished_at"],
                }
                _2c_card_cache_put("2.0", projection_scope, run_id, card)
                cards.append(card)
        finally:
            if store is not None:
                store.close()
    build_ms = (time.perf_counter() - started) * 1000
    cards.sort(key=lambda card: card["rule_id"])
    summary = base.get("summary")
    if isinstance(summary, dict):
        summary["not_applicable"] = sum(
            card["applicability"] == "NOT_APPLICABLE" for card in cards
        )
    _log_v2_outbound(
        base,
        cards,
        v1_results_count=len(v1_results),
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        build_ms=build_ms,
    )
    return _clean_v2_payload({
        "api_version": "2.0",
        **base,
        "cards": cards,
    })


_V3_CHARGE_TEXT_COLUMNS = {
    "ordering_department_code": "acord_dept_codg",
    "ordering_department_name": "acord_dept_name",
    "ordering_doctor_id": "orders_dr_code",
    "ordering_doctor_name": "orders_dr_name",
}


def _v3_number(value: Any) -> int | float | None:
    """费用数字转为有限 JSON number；空值/异常值稳定返回 null。"""
    text = _clean_fee_scalar(value)
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if not pd.notna(number) or number in (float("inf"), float("-inf")):
        return None
    return int(number) if number.is_integer() else number


def _v3_matching_fee_rows(item: dict[str, Any], fee_df: Any):
    """v2 matched item → 同一费用快照中的实际收费行，保持原行顺序和基数。"""
    if (
        fee_df is None
        or len(fee_df) == 0
        or "medins_list_name" not in fee_df.columns
    ):
        return None
    matched_name = _clean_fee_scalar(item.get("matched_fee_name"))
    if not matched_name:
        return None
    rows = fee_df[
        fee_df["medins_list_name"].fillna("").astype(str).str.strip()
        == matched_name
    ]
    code_nat = _clean_fee_scalar(item.get("code_nat"))
    code_local = _clean_fee_scalar(item.get("code_local"))
    if code_nat and "med_list_codg" in rows.columns:
        rows = rows[
            rows["med_list_codg"].fillna("").astype(str).str.strip() == code_nat
        ]
    elif code_local and "medins_list_codg" in rows.columns:
        rows = rows[
            rows["medins_list_codg"].fillna("").astype(str).str.strip()
            == code_local
        ]
    if "fee_ocur_time" in rows.columns:
        expected_time = _clean_fee_scalar(item.get("occurrence_time"))
        normalized_times = rows["fee_ocur_time"].map(_format_v2_occurrence_time)
        rows = rows[normalized_times == expected_time]
    elif _clean_fee_scalar(item.get("occurrence_time")):
        return None
    if "cnt" in rows.columns:
        positive = rows[pd.to_numeric(rows["cnt"], errors="coerce").fillna(0) > 0]
        if len(positive):
            rows = positive
    return rows


def _v3_empty_charge_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "quantity": None,
        "unit_price": None,
        **{field: "" for field in _V3_CHARGE_TEXT_COLUMNS},
    }


def _v3_matched_items(
    matched_items: list[dict[str, Any]], fee_df: Any
) -> list[dict[str, Any]]:
    """将 v2 项目/时间命中展开为 v3 收费明细行；不按展示字段去重。"""
    expanded: list[dict[str, Any]] = []
    for item in matched_items:
        rows = _v3_matching_fee_rows(item, fee_df)
        if rows is None or len(rows) == 0:
            expanded.append(_v3_empty_charge_fields(item))
            continue
        for _, row in rows.iterrows():
            expanded.append({
                **item,
                "quantity": _v3_number(row.get("cnt")),
                "unit_price": _v3_number(row.get("pric")),
                **{
                    field: _clean_fee_scalar(row.get(column))
                    for field, column in _V3_CHARGE_TEXT_COLUMNS.items()
                },
            })
    return expanded


def _log_v3_outbound(
    base: dict[str, Any],
    cards: list[dict[str, Any]],
    *,
    cache_hits: int = 0,
    cache_misses: int = 0,
    build_ms: float = 0.0,
) -> None:
    """记录 v3 出口结构数量；不得写患者、attempt、run 或业务原文。"""
    progress = base.get("progress") if isinstance(base.get("progress"), dict) else {}
    outbound_logger.info(
        "2c_v3_outbound status=%s outcome=%s total=%s completed=%s failed=%s "
        "cards=%d charge_lines=%d cache_hits=%d cache_misses=%d build_ms=%.1f",
        base.get("status"),
        base.get("outcome"),
        progress.get("total"),
        progress.get("completed"),
        progress.get("failed"),
        len(cards),
        sum(len(card.get("matched_items", [])) for card in cards),
        cache_hits,
        cache_misses,
        build_ms,
    )


@router.get("/v3/rules")
def rule_handling_levels_2c():
    """2C v3：当前规则静态处理等级目录；历史结果按 rule_id 复用。"""
    metas = load_rule_meta()
    return {
        "api_version": "3.0",
        "rules": {
            rule_id: meta["handling_level"]
            for rule_id, meta in sorted(metas.items())
        },
    }


@router.get("/v3/results/{syxh}")
def results_2c_v3(syxh: str):
    """2C v3：在完整 v2 卡片上追加逐收费行量价、科室和医生字段。"""
    payload = results_2c_v2(syxh)
    cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
    attempt_id = payload.get("attempt_id")
    projection_scope = _2c_projection_scope(attempt_id, cards)
    projected: list[dict[str, Any]] = []
    cache_hits = 0
    cache_misses = 0
    started = time.perf_counter()
    build_lock = _2c_projection_build_lock(projection_scope)
    with build_lock:
        fee_df = None
        fee_df_loaded = False
        for card in cards:
            run_id = card.get("run_id", "")
            cached = _2c_card_cache_get("3.0", projection_scope, run_id)
            if cached is not None:
                cache_hits += 1
                projected.append(cached)
                continue

            cache_misses += 1
            if card.get("matched_items") and not fee_df_loaded:
                fee_df = _v2_fee_df(syxh)
                fee_df_loaded = True
            v3_card = copy.deepcopy(card)
            matched_items = _v3_matched_items(
                v3_card.get("matched_items", []), fee_df
            )
            v3_card["matched_items"] = matched_items
            v3_card["hit_codes"] = [item["code"] for item in matched_items]
            v3_card["hit_names"] = [item["name"] for item in matched_items]
            v3_card["hit_times"] = [item["occurrence_time"] for item in matched_items]
            _2c_card_cache_put("3.0", projection_scope, run_id, v3_card)
            projected.append(v3_card)
    cards = projected
    payload["cards"] = cards
    payload["api_version"] = "3.0"
    build_ms = (time.perf_counter() - started) * 1000
    _log_v3_outbound(
        payload,
        cards,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        build_ms=build_ms,
    )
    return payload


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
        audit_disposition=(
            result.eligibility_evaluation.audit_disposition.value
            if result.eligibility_evaluation is not None
            else None
        ),
        eligibility_status=(
            result.eligibility_evaluation.eligibility_status.value
            if result.eligibility_evaluation is not None
            else None
        ),
        eligibility_evaluation=(
            result.eligibility_evaluation.model_dump(mode="json")
            if result.eligibility_evaluation is not None
            else None
        ),
    )
