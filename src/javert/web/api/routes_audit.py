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
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

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
        "eligibility_evaluation": (
            result.eligibility_evaluation.model_dump(mode="json")
            if result.eligibility_evaluation is not None
            else None
        ),
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
                persist_one(result, rule, triggered_by="bff-batch")
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
# 免鉴权: middleware PUBLIC_PREFIXES 只放行这两个精确路径, 仅限内网.
# =========================================================

_VERDICT_LABEL = {"VIOLATION": "违规", "INCONCLUSIVE": "待人工复核", "CLEAN": "合规"}
_2C_RULE_CONCURRENCY = 5

# ponytail: 进程内任务表, 重启后历史提交回 unknown (裁决本体仍在 sqlite/工作台);
# 需要跨重启状态时把任务表落 sqlite
_2c_tasks: dict[str, dict] = {}
_2c_lock = threading.Lock()
_2c_queue: "queue.Queue[str]" = queue.Queue()
_2c_worker: threading.Thread | None = None


class SubmitItem(BaseModel):
    SYXH: str
    YLZZJGDM: str = ""


# ─── 142 数据中台兜底 (2C 提交真实患者, 本地 CSV 查无 → hub 取数入审) ───
_2C_HUB_CACHE = "output/hub_cache_2c"  # 每患者一目录, 每次提交重取保新鲜


def _hub_probe(syxh: str) -> bool:
    """中台是否有该患者费用数据 (submit 受理判据). 连接失败向上抛, 调用方给诚实 reason."""
    from javert.data import hub_source as hs
    cfg = get_config()
    cn = hs.connect(cfg, timeout=10)
    try:
        yq2org = hs.fetch_hospital_map(cn)
        return len(hs.fetch_fees(cn, [syxh], yq2org)) > 0
    finally:
        cn.close()


def _hub_fetch_patient(syxh: str, out) -> None:
    """中台 → out/ 6 CSV (镜像 scripts/etl_from_data_hub.py 的单患者切片)."""
    from javert.data import hub_source as hs
    cfg = get_config()
    out.mkdir(parents=True, exist_ok=True)
    cn = hs.connect(cfg)
    try:
        yq2org = hs.fetch_hospital_map(cn)
        kw = {"index": False, "encoding": "utf-8-sig"}
        hs.fetch_fees(cn, [syxh], yq2org).to_csv(out / "shi_fee.csv", **kw)
        hs.fetch_notes(cn, [syxh]).to_csv(out / "case_notes.csv", **kw)
        hs.fetch_zd(cn, [syxh], yq2org).to_csv(out / "shi_zd.csv", **kw)
        hs.fetch_ss(cn, [syxh], yq2org).to_csv(out / "shi_ss.csv", **kw)
        hs.fetch_labs(cn, [syxh]).to_csv(out / "lab_results.csv", **kw)
        hs.fetch_exams(cn, [syxh]).to_csv(out / "examinations.csv", **kw)
    finally:
        cn.close()


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
    with _2c_lock:
        source = _2c_tasks[syxh].get("source", "local")
    if source == "hub":
        hub_dir = cfg.resolve(_2C_HUB_CACHE) / syxh
        _hub_fetch_patient(syxh, hub_dir)
        cfg = _hub_cfg_for(cfg, hub_dir)
        loader = CsvLoader(cfg.notes_path, cfg.fees_path)
        zd_path = cfg.zd_path
        with _2c_lock:
            _2c_tasks[syxh]["data_dir"] = str(hub_dir)
    else:
        loader = _get_loader()
        zd_path = default_shi_zd_path()
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
                    persist_one(result, rule, triggered_by="2c-submit", sqlite_store=store)
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
                    _2c_tasks[syxh]["error"] = f"审计中断: {exc.__class__.__name__}"
        finally:
            with _2c_lock:
                if syxh in _2c_tasks:
                    _2c_tasks[syxh]["status"] = "done"


def _2c_ensure_worker() -> None:
    global _2c_worker
    with _2c_lock:
        if _2c_worker is not None and _2c_worker.is_alive():
            return
        _2c_worker = threading.Thread(target=_2c_worker_loop, daemon=True, name="audit-2c")
        _2c_worker.start()


@router.post("/submit", status_code=202)
def submit_2c(items: list[SubmitItem]):
    """2C 提交: 逐患者校验数据存在 → 受理入队. 重复提交在跑中的患者幂等 (不重跑)."""
    loader = _get_loader()
    accepted: list[dict] = []
    rejected: list[dict] = []
    for item in items:
        syxh = item.SYXH.strip()
        if not syxh:
            rejected.append({"SYXH": item.SYXH, "reason": "SYXH 为空"})
            continue
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
                accepted.append({"SYXH": syxh, "source": existing.get("source", "local")})
                continue  # 已在跑, 幂等受理
            _2c_tasks[syxh] = {
                "YLZZJGDM": item.YLZZJGDM,
                "status": "running",
                "source": source,
                "total": None,
                "run_ids": [],
                "failed": [],
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }
        _2c_queue.put(syxh)
        accepted.append({"SYXH": syxh, "source": source})
    if accepted:
        _2c_ensure_worker()
    return {"accepted": accepted, "rejected": rejected}


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


@router.get("/results/{syxh}")
def results_2c(syxh: str):
    """2C 查结果: unknown / running(增量) / done + summary + results[]."""
    with _2c_lock:
        task = _2c_tasks.get(syxh)
        status = task["status"] if task else "unknown"
        ylzzjgdm = task["YLZZJGDM"] if task else ""
        total = task["total"] if task else None
        run_ids = list(task["run_ids"]) if task else []
        data_dir = task.get("data_dir") if task else None
        error = task.get("error", "") if task else ""

    if task is None:
        # 服务重启后任务表清空, 但裁决本体在 sqlite — 回退查历史 (每规则最新一条),
        # 有则按 done 返回, 2C 不必因我方重启而重跑
        run_ids = _latest_runs_for_patient(syxh)
        if run_ids:
            status = "done"
            total = len(run_ids)
            hub_dir = get_config().resolve(_2C_HUB_CACHE) / syxh
            if hub_dir.exists():
                data_dir = str(hub_dir)

    results: list[dict] = []
    if run_ids:
        metas = load_rule_meta()
        cfg = get_config()
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
        store = SqliteStore(cfg.audit_db_path)
        try:
            store.init_schema()
            for rid in run_ids:
                r = store.find_by_run_id(rid)
                if r is None:
                    continue
                meta = metas.get(r.rule_id)
                # 命中项目 (确定性, 复用工作台 hit_resolver): V/I 才算, 给 2C 侧
                # join 自己的费用明细 (code_nat=国家医保码 / matched_fee_name=明细原始项目名)
                hits: list[dict] = []
                hit_codes: list[str] = []
                hit_names: list[str] = []
                if r.verdict in ("VIOLATION", "INCONCLUSIVE"):
                    hit_items = resolve_hits_from_json(
                        json.dumps([e.model_dump() for e in r.evidence], ensure_ascii=False),
                        json.dumps(
                            [tc.model_dump() for tc in r.tool_calls],
                            ensure_ascii=False, default=str,
                        ),
                        meta["drug_rule_type"] if meta else None,
                        fee_df,
                        load_kb_drugs(),
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
                    "behavior_name": meta["behavior_name"] if meta else "",
                    "verdict": r.verdict,
                    "verdict_label": _VERDICT_LABEL.get(r.verdict, r.verdict),
                    "confidence": r.confidence,
                    "reasoning": humanize_reasoning(r.reasoning),
                    "eligibility_evaluation": (
                        r.eligibility_evaluation.model_dump(mode="json")
                        if r.eligibility_evaluation is not None
                        else None
                    ),
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

    out: dict[str, Any] = {
        "SYXH": syxh,
        "YLZZJGDM": ylzzjgdm,
        "status": status,
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
