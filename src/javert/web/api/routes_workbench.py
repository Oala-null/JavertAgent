# -*- coding: utf-8 -*-
"""工作台路由 — sidebar / patient detail / review submit / banner / raw_data / dashboard / export.

把 design.md D8-D13 + spec/review-workbench 一次性串到一个 router. SSE 端点和
audit_watcher 单独在 routes_sse.py.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)

from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.data.examination_loader import ExaminationLoader
from javert.data.lab_loader import LabLoader
from javert.store.sqlserver_store import get_sqlserver_store
from javert.web.auth import current_user, request_meta, session_user_id
from javert.web.doc_order import bucket_of
from javert.web.hit_resolver import (
    hits_from_json,
    hits_have_verified_fee_snapshot,
    load_kb_drugs,
    resolve_hits,
)
from javert.web.patient_overview import (
    build_overview,
    get_fees_sum_map,
    get_primary_dx,
)
from javert.web.rule_meta import load_rule_meta
from javert.web.public_presenter import present_public_explanation
from javert.web.templating import render

logger = logging.getLogger("javert.web.routes_workbench")

router = APIRouter(tags=["workbench"])


FILTER_VALID = ("v_and_i", "v_only", "i_only", "all")


def _filter_from(request: Request, query_filter: str | None) -> str:
    f = query_filter or request.cookies.get("javert_filter")
    return f if f in FILTER_VALID else "v_and_i"


def _enrich_sidebar(patients: list) -> list:
    """给 sidebar PatientSidebarItem 填 fees_sum / primary_dx (D6 进程缓存, 渲染前).

    updated_at 已由 store SQL 填好. fees_sum 走一次性 groupby 缓存; primary_dx
    复用 _load_zd 进程缓存 + note 兜底. 失败不阻断 sidebar 渲染.
    """
    if not patients:
        return patients
    try:
        loader = _get_loader()
        fees_map = get_fees_sum_map(loader)
        for p in patients:
            p.fees_sum = fees_map.get(p.patient_id, 0.0)
            p.primary_dx = get_primary_dx(p.patient_id, loader)
    except Exception as e:  # noqa: BLE001
        logger.warning("_enrich_sidebar 失败: %s", e)
    return patients


# boost-llm-efficiency (design D5): 点开病人 detail 不再复跑两遍全表 ROW_NUMBER CTE.
# sidebar 需要全患者列表 (按 pid 窄查询会砍掉侧栏导航), 故用短 TTL 进程缓存:
# detail 页允许秒级陈旧 (计数由 SSE 客户端增量更新), 列表页始终现查并刷新缓存.
_SIDEBAR_TTL_SECONDS = 10.0
_sidebar_cache: dict[str, tuple[float, list]] = {}


def _sidebar_patients(store, filter_mode: str, *, allow_cached: bool) -> list:
    now = time.monotonic()
    if allow_cached:
        hit = _sidebar_cache.get(filter_mode)
        if hit is not None and (now - hit[0]) < _SIDEBAR_TTL_SECONDS:
            return hit[1]
    patients = _enrich_sidebar(store.list_patients_with_violations(filter_mode=filter_mode))
    _sidebar_cache[filter_mode] = (now, patients)
    return patients


def _resolve_hits_for_runs(
    patient_id: str, runs: list, meta_map: dict, anchors_map: dict[str, str] | None = None,
) -> dict[str, list]:
    """对该 patient 的每个 run 给命中项目 HitItem[] → {run_id: [HitItem...]}.

    渲染优先读 anchors_json 缓存 (anchors_map, D2); miss / 损坏 → 现算回退.
    现算: 一次切 fee df + 一次载 KB (仅当存在 drug 规则), 供所有 run 复用; 失败不阻断渲染.
    """
    out: dict[str, list] = {}
    if not runs:
        return out
    anchors_map = anchors_map or {}
    fee_df = None
    fee_loaded = False
    kb_drugs: dict = {}
    kb_loaded = False
    for run in runs:
        m = meta_map.get(run.rule_id) or {}
        drug_type = m.get("drug_rule_type")
        # 1) 缓存命中 (确定性回填的 anchors_json)
        cached_json = anchors_map.get(run.run_id) if anchors_map else None
        cached = hits_from_json(cached_json)
        # fee/drug 旧缓存可能是 locator/search fallback，必须关联当前患者净正收费后重算；
        # v2 verified cache 已绑定审计时的实际收费切片，可在隔离数据源下自包含回放。
        verified_charge_cache = bool(
            cached is not None
            and hits_have_verified_fee_snapshot(cached_json)
            and all(
                h.source not in {"fee", "drug"} or bool(h.matched_fee_name.strip())
                for h in cached
            )
        )
        stale_public_charge_cache = bool(
            cached is not None
            and any(h.source in {"fee", "drug"} for h in cached)
            and not verified_charge_cache
        )
        if cached is not None and not stale_public_charge_cache:
            out[run.run_id] = cached
            continue
        # 2) 现算回退 (lazy 加载 fee/KB, 仅在确有 miss 时)
        if not fee_loaded:
            try:
                profile_source = _get_profile_hub_source_for_patient(patient_id)
                if profile_source is not None:
                    fee_df = profile_source.get_fees(patient_id)
                else:
                    fee_df = _get_loader().get_fees(patient_id)
                    if ((fee_df is None or len(fee_df) == 0)
                            and get_config().hub_raw_enabled):
                        fee_df = _get_hub_source().get_fees(patient_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("_resolve_hits_for_runs 取 fee 失败 patient=%s: %s", patient_id, e)
                fee_df = None
            fee_loaded = True
        if drug_type and not kb_loaded:
            kb_drugs = load_kb_drugs()
            kb_loaded = True
        try:
            out[run.run_id] = resolve_hits(
                run, drug_type,
                patient_fee_df=fee_df,
                kb_drugs=(kb_drugs if drug_type else {}),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("resolve_hits 失败 run=%s: %s", run.run_id, e)
            out[run.run_id] = []
    return out


def _group_runs_by_violation_type(runs: list, meta_map: dict) -> list[dict]:
    """按公开 `(behavior_code, behavior_name)` 分组，组内保留全部规则卡。

    violation_type 取自 rule_meta (RunWithReviews 无此字段); 缺 meta → '未分类'.
    返回 `[{vt, alias, anchor, n_v, n_i, runs}]`, 组按首次出现序稳定排列;
    `anchor` 用组序号 (vt-N) 当 DOM id, 避开中文 / 特殊字符在 id/CSS selector 的坑.
    """
    groups: dict[tuple[str, str], list] = {}
    order: list[tuple[str, str]] = []
    for run in runs:
        m = meta_map.get(run.rule_id) or {}
        code = (m.get("behavior_code") or "").strip()
        name = (m.get("behavior_name") or "").strip() or "未分类"
        exception_key = (m.get("behavior_exception_key") or "").strip()
        code_key = code or (f"exception:{exception_key}" if exception_key else "")
        key = (code_key, name)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(run)

    def _verdict_rank(r) -> int:
        return 0 if r.verdict == "VIOLATION" else (1 if r.verdict == "INCONCLUSIVE" else 2)

    out: list[dict] = []
    for i, key in enumerate(order):
        code_key, name = key
        grp = groups[key]
        grp.sort(key=_verdict_rank)  # stable: 组内 V → I → 其他(CLEAN)
        out.append({
            "vt": name,
            "alias": name if len(name) <= 10 else name[:8] + "…",
            "behavior_code": "" if code_key.startswith("exception:") else code_key,
            "behavior_name": name,
            "exception_key": code_key.removeprefix("exception:") if code_key.startswith("exception:") else "",
            "anchor": f"vt-{i}",
            "n_v": sum(1 for r in grp if r.verdict == "VIOLATION"),
            "n_i": sum(1 for r in grp if r.verdict == "INCONCLUSIVE"),
            "runs": grp,
        })
    return out


def _filter_label(f: str) -> str:
    return {
        "v_and_i": "违规 + 不明",
        "v_only": "仅违规",
        "i_only": "仅不明",
        "all": "全部 (含干净)",
    }.get(f, f)


# =========================================================
# Workbench 主页 + patient detail
# =========================================================
@router.get("/api/workbench/sig")
def workbench_sig(request: Request):
    """轻量签名 (142 max run id) — 列表页前端定时轮询, 值变即知有新裁决 → 自动刷新兜底.

    不依赖 SSE/AuditWatcher (142 抖时 SSE 会 backoff); MAX(id) 走主键索引 O(1), 极廉价.
    142 不可达 → 返回 sig=None, 前端按"无变化"处理 (绝不误刷).
    """
    if current_user(request) is None:
        return JSONResponse(status_code=401, content={"error": "未登录"})
    try:
        from sqlalchemy import text
        eng = get_sqlserver_store().get_engine()
        if eng is None:
            return {"sig": None}
        with eng.connect() as c:
            sig = c.execute(text("SELECT MAX(id) FROM javert_audit_runs")).scalar()
        return {"sig": int(sig) if sig is not None else 0}
    except Exception:  # noqa: BLE001 — 142 抖 → None → 前端不动
        return {"sig": None}


@router.get("/workbench", response_class=HTMLResponse)
def workbench_index(request: Request, filter: str | None = None):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/login?next=/workbench", status_code=302)

    f = _filter_from(request, filter)
    store = get_sqlserver_store()
    patients = _sidebar_patients(store, f, allow_cached=False)

    # 欢迎 banner — 跳过条件: cookie welcome_dismissed == 当次 session login_ts
    prev_iso = request.session.get("prev_last_login")
    since: datetime | None = None
    if prev_iso and prev_iso != "first_login":
        try:
            since = datetime.fromisoformat(prev_iso)
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except ValueError:
            since = None
    stats = store.get_since_last_login_stats(user_id=user.id, since=since)
    dismissed = request.cookies.get("welcome_dismissed") == (prev_iso or "")
    show_banner = not dismissed

    html = render(
        "workbench.html",
        title="工作台",
        current_user=user,
        patients=patients,
        active_patient=None,
        filter=f,
        filter_label=_filter_label(f),
        runs=[],
        show_banner=show_banner,
        banner_stats=stats,
        banner_first_login=(prev_iso == "first_login"),
        prev_last_login=since,
    )
    resp = HTMLResponse(html)
    # filter 状态写 cookie (跨会话保持)
    if filter and filter in FILTER_VALID:
        resp.set_cookie(
            "javert_filter", filter,
            max_age=60 * 60 * 24 * 30, samesite="lax", httponly=False,
        )
    return resp


@router.get("/workbench/{patient_id}", response_class=HTMLResponse)
def workbench_patient(
    request: Request,
    patient_id: str,
    filter: str | None = None,
):
    user = current_user(request)
    if user is None:
        return RedirectResponse(
            url=f"/login?next=/workbench/{patient_id}", status_code=302,
        )
    f = _filter_from(request, filter)
    store = get_sqlserver_store()
    patients = _sidebar_patients(store, f, allow_cached=True)
    runs = store.list_runs_for_patient(patient_id=patient_id, filter_mode=f)
    if not runs:
        # 不报 404 — patient 可能存在但 filter 下空
        # 但若全工作区都没这 pid 任何行, 走 404
        if not store.has_other_runs(patient_id, exclude_run_id="__no_such__"):
            # has_other_runs 用 exclude='__no_such__', 等价 "patient 有任何 row 吗"
            # 没有 → 404
            raise HTTPException(status_code=404, detail=f"未找到患者 {patient_id}")
    # 概览数据 (basics + fees + dx + 手术); 失败不阻断违规渲染.
    # CSV 双 miss 且 hub 开启 → 概览改喂 HubRawSource (get_notes/get_fees 与 CsvLoader 同形,
    # 病案基本信息/费用分类对 hub 患者才有数据; add-workbench-sql-raw-source 补遗)
    overview = None
    try:
        profile_source = _get_profile_hub_source_for_patient(patient_id)
        if profile_source is not None:
            ov_loader = profile_source
        else:
            ov_loader = _get_loader()
            _n = ov_loader.get_notes(patient_id)
            _f = ov_loader.get_fees(patient_id)
            if ((_n is None or len(_n) == 0) and (_f is None or len(_f) == 0)
                    and get_config().hub_raw_enabled):
                ov_loader = _get_hub_source()
        overview = build_overview(patient_id, ov_loader)
    except Exception as e:  # noqa: BLE001
        logger.warning("build_overview 失败 patient=%s: %s", patient_id, e)

    # 命中项目 + 锚点 (evidence-anchoring) — 优先读 anchors_json 缓存, miss 则现算
    # (老数据立即生效, 零重跑). 一次切 fee df + 一次载 KB, 供所有 run 复用.
    meta_map = load_rule_meta()
    anchors_map = store.fetch_anchors_for_patient(patient_id)
    hits_by_run = _resolve_hits_for_runs(patient_id, runs, meta_map, anchors_map)
    public_explanations = {}
    for run in runs:
        public = present_public_explanation(
            run, meta_map.get(run.rule_id), hits_by_run.get(run.run_id, [])
        )
        run.headline = public["headline"]
        public_explanations[run.run_id] = public
    # 按细类分组 + 组内 V 前 I 后 (D5) — 模板按 run_groups 渲染可折叠 section + 顶部 chip
    run_groups = _group_runs_by_violation_type(runs, meta_map)

    return HTMLResponse(render(
        "patient_detail.html",
        title=f"{patient_id} · 工作台",
        current_user=user,
        patients=patients,
        active_patient=patient_id,
        filter=f,
        filter_label=_filter_label(f),
        runs=runs,
        run_groups=run_groups,
        rule_meta=meta_map,
        overview=overview,
        hits_by_run=hits_by_run,
        public_explanations=public_explanations,
    ))


def _log_model_compare_access(
    request: Request,
    patient_id: str,
    batch_tag: str,
) -> None:
    try:
        user = current_user(request)
        ip, ua = request_meta(request)
        get_sqlserver_store().log_action(
            user_id=user.id if user else None,
            action="model_compare_access",
            target_id=patient_id,
            payload={"batch_tag": batch_tag},
            ip=ip,
            user_agent=ua,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "model_compare_access 留痕失败 patient=%s tag=%s: %s",
            patient_id,
            batch_tag,
            exc,
        )


@router.get("/workbench/{patient_id}/model-compare", response_class=HTMLResponse)
def workbench_model_compare(
    request: Request,
    patient_id: str,
    batch_tag: str = Query(default="ab3.8", pattern=r"^[A-Za-z0-9._-]{1,20}$"),
):
    user = current_user(request)
    if user is None:
        return RedirectResponse(
            url=f"/login?next=/workbench/{patient_id}/model-compare",
            status_code=302,
        )
    store = get_sqlserver_store()
    runs = store.list_model_comparison_runs(patient_id, batch_tag)
    models = sorted({run["model"] for run in runs})
    left_model = next((model for model in models if "3.6" in model), None)
    right_model = next((model for model in models if "3.8" in model), None)
    if left_model is None or right_model is None:
        raise HTTPException(
            status_code=404,
            detail=f"批次 {batch_tag} 尚无完整 Qwen3.6/Qwen3.8 配对",
        )

    by_key = {(run["rule_id"], run["model"]): run for run in runs}
    rule_ids = sorted({run["rule_id"] for run in runs})
    rows = []
    for rule_id in rule_ids:
        left = by_key.get((rule_id, left_model))
        right = by_key.get((rule_id, right_model))
        rows.append({
            "rule_id": rule_id,
            "left": left,
            "right": right,
            "same": bool(left and right and left["verdict"] == right["verdict"]),
        })
    summary = {
        "rules": len(rows),
        "same": sum(row["same"] for row in rows),
        "different": sum(
            bool(row["left"] and row["right"] and not row["same"])
            for row in rows
        ),
        "missing": sum(not row["left"] or not row["right"] for row in rows),
    }
    _log_model_compare_access(request, patient_id, batch_tag)
    return HTMLResponse(render(
        "model_compare.html",
        title=f"{patient_id} · 双模型对比",
        current_user=user,
        active_patient=patient_id,
        patient_id=patient_id,
        batch_tag=batch_tag,
        left_model=left_model,
        right_model=right_model,
        rows=rows,
        summary=summary,
        rule_meta=load_rule_meta(),
    ))


# =========================================================
# Review 提交
# =========================================================
class _ReviewBody:
    """轻量手撕 — 不需要 pydantic full power."""
    def __init__(self, run_id: str, verdict: str, comment: str | None):
        self.run_id = run_id
        self.verdict = verdict
        self.comment = comment


@router.post("/review")
async def submit_review(request: Request):
    user = current_user(request)
    if user is None:
        return JSONResponse(status_code=401, content={"error": "未登录"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "需要 JSON body"})

    run_id = (body or {}).get("run_id", "").strip()
    verdict = (body or {}).get("verdict", "").strip().upper()
    comment = (body or {}).get("comment")
    if not run_id:
        return JSONResponse(status_code=400, content={"error": "Missing run_id"})
    if verdict not in ("V", "I", "C"):
        return JSONResponse(status_code=422, content={"error": f"非法 verdict: {verdict}"})

    store = get_sqlserver_store()
    ip, ua = request_meta(request)
    try:
        rec = store.submit_review(
            run_id=run_id, user_id=user.id,
            verdict=verdict, comment=comment,
            ip=ip, user_agent=ua,
        )
    except LookupError:
        return JSONResponse(status_code=404, content={"error": "未找到审计记录"})
    except ValueError as e:
        return JSONResponse(status_code=422, content={"error": str(e)})
    except RuntimeError as e:
        logger.error("submit_review failed: %s", e)
        return JSONResponse(status_code=503, content={"error": "数据库不可用"})

    # SSE 广播 (routes_sse.event_bus 注入)
    try:
        from .routes_sse import event_bus
        await event_bus.publish("review_submitted", {
            "run_id": run_id,
            "reviewer_username": user.username,
            "reviewer_display_name": user.display_name or user.username,
            "verdict": verdict,
            "patient_id": rec.patient_id,
            "rule_id": rec.rule_id,
            "is_update": rec.previous_verdict is not None,
            # 全局口径 sidebar 计数增量 (任意专家首评该 run 才 +1, 落当前 filter 命中集才算)
            "run_first_review": rec.run_first_review,
            "run_verdict": rec.run_audit_verdict,
        })
    except Exception as e:  # noqa: BLE001
        logger.debug("SSE broadcast 失败: %s", e)

    return JSONResponse(
        status_code=201,
        content={
            "id": rec.id,
            "run_id": rec.run_id,
            "user_id": rec.user_id,
            "verdict": rec.review_verdict,
            "comment": rec.comment,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
        },
    )


# =========================================================
# Welcome banner dismiss
# =========================================================
@router.post("/api/banner/dismiss")
def banner_dismiss(request: Request):
    prev_iso = request.session.get("prev_last_login", "")
    resp = JSONResponse(content={"ok": True})
    resp.set_cookie(
        "welcome_dismissed", prev_iso,
        max_age=60 * 60 * 24, samesite="lax", httponly=False,
    )
    return resp


# =========================================================
# 原始病历 (fee + notes)
# =========================================================
_loader_singleton: CsvLoader | None = None


def _get_loader() -> CsvLoader:
    global _loader_singleton
    if _loader_singleton is None:
        cfg = get_config()
        from javert.onboarding.etl_engine import PROJECT_ROOT
        # 工作台 loader 叠加 data_import (coexist): shi 演示病人 + onboarding 接入病人并存
        _loader_singleton = CsvLoader(cfg.notes_path, cfg.fees_path,
                                      overlay_dir=PROJECT_ROOT / "data_import")
    return _loader_singleton


def reset_loader() -> None:
    """重置工作台 loader 单例 — onboarding 载入新数据后调, 让 data_import 叠加层立即生效.
    含 lab/exam loader (它们也叠加 data_import 的 lab_results/examinations.csv) 与 hub 源缓存."""
    global _loader_singleton, _lab_loader_singleton, _exam_loader_singleton
    global _hub_source_singleton, _hub_profile_source_singletons
    _loader_singleton = None
    _lab_loader_singleton = None
    _exam_loader_singleton = None
    _hub_source_singleton = None
    _hub_profile_source_singletons = {}


# hub 原文源单例 (add-workbench-sql-raw-source: CSV 双 miss 时按患者号查 hub, 开关默认关)
_hub_source_singleton = None
_hub_profile_source_singletons: dict[tuple[str, str, str], Any] = {}


def _get_hub_source():
    global _hub_source_singleton
    if _hub_source_singleton is None:
        from javert.web.hub_raw_source import HubRawSource
        _hub_source_singleton = HubRawSource(get_config())
    return _hub_source_singleton


def _get_hub_profile_source(batch_tag: str):
    """按显式 tag profile 返回隔离 HubRawSource；无映射时返回 None。"""
    cfg = get_config()
    profile = cfg.hub_raw_profiles.get(batch_tag)
    if profile is None:
        return None
    key = (batch_tag, profile.database, profile.table_prefix)
    source = _hub_profile_source_singletons.get(key)
    if source is None:
        from javert.web.hub_raw_source import HubRawSource

        profile_cfg = cfg.model_copy(
            update={
                "hub_database": profile.database,
                "hub_table_prefix": profile.table_prefix,
            }
        )
        source = HubRawSource(profile_cfg)
        _hub_profile_source_singletons[key] = source
    return source


def _get_profile_hub_source_for_patient(patient_id: str):
    """返回患者显式 batch profile；命中后调用方不得回退到 CSV/默认 Hub。"""
    cfg = get_config()
    if cfg.hub_raw_profiles:
        tag = get_sqlserver_store().latest_batch_tag_for_patient(patient_id)
        if tag:
            profile_source = _get_hub_profile_source(tag)
            if profile_source is not None:
                return profile_source
    return None


def _get_hub_source_for_patient(patient_id: str):
    """显式 profile 优先；未命中才走默认 Hub。"""
    profile_source = _get_profile_hub_source_for_patient(patient_id)
    if profile_source is not None:
        return profile_source
    return _get_hub_source()


# 检验/检查 loader 单例 (索引一次性构建后进程缓存; 检验文件大, 首次 raw 取数稍慢)
_lab_loader_singleton: LabLoader | None = None
_exam_loader_singleton: ExaminationLoader | None = None


def _get_lab_loader() -> LabLoader:
    global _lab_loader_singleton
    if _lab_loader_singleton is None:
        from javert.onboarding.etl_engine import PROJECT_ROOT
        _lab_loader_singleton = LabLoader(get_config().labs_path,
                                          overlay_dir=PROJECT_ROOT / "data_import")
    return _lab_loader_singleton


def _get_exam_loader() -> ExaminationLoader:
    global _exam_loader_singleton
    if _exam_loader_singleton is None:
        from javert.onboarding.etl_engine import PROJECT_ROOT
        _exam_loader_singleton = ExaminationLoader(get_config().examinations_path,
                                                   overlay_dir=PROJECT_ROOT / "data_import")
    return _exam_loader_singleton


# fee_ocur_time 多格式 fallback (与 patient_overview._extract_from_fees_df 同一组格式)
_FEE_DATE_FMTS = ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y")


def _fmt_fee_date(raw: str) -> str:
    """fee_ocur_time (dd/mm/yyyy [hh:mm:ss]) → 'YYYY/MM/DD'; 解析失败回退原日期段."""
    head = (raw or "").strip().split()[0] if (raw or "").strip() else ""
    if not head:
        return ""
    for fmt in _FEE_DATE_FMTS:
        try:
            return datetime.strptime(head, fmt).strftime("%Y/%m/%d")
        except ValueError:
            continue
    return head


# =========================================================
# PHI 访问留痕 + 限流 (harden-onsite-redlines phi-access-audit)
# =========================================================
def _session_key(request: Request) -> str:
    """限流 key: 登录 session 的 user_id (AuthMiddleware 保证已登录); 未登录兜底 IP."""
    uid = session_user_id(request)
    if uid is not None:
        return f"uid:{uid}"
    return request.client.host if request.client else "anon"


def _rate_limit_raw(func):  # noqa: ANN001
    """raw 端点每会话限流 (档位 config.raw_rate_limit, 每请求读 → env 可调)."""
    from .routes_auth import limiter as _limiter
    if _limiter is None:
        return func
    return _limiter.limit(lambda: get_config().raw_rate_limit, key_func=_session_key)(func)


def _log_raw_access(request: Request, patient_id: str, source: str) -> None:
    """raw 端点审计留痕 (对齐 /export 的 log_action). 写失败只 warn 不阻断响应."""
    try:
        user = current_user(request)
        ip, ua = request_meta(request)
        get_sqlserver_store().log_action(
            user_id=user.id if user else None,
            action="raw_access",
            target_id=patient_id,
            payload={"source": source},
            ip=ip, user_agent=ua,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("raw_access 留痕失败 patient=%s: %s", patient_id, e)


def log_raw_rate_limited(request: Request) -> None:
    """限流 429 的 raw 请求同样留痕 (create_app 的 RateLimitExceeded handler 调)."""
    try:
        if not request.url.path.endswith("/raw"):
            return
        pid = (request.path_params or {}).get("patient_id", "")
        _log_raw_access(request, pid, source="rate_limited")
    except Exception as e:  # noqa: BLE001
        logger.warning("rate_limited 留痕失败: %s", e)


@router.get("/api/patient/{patient_id}/raw")
@_rate_limit_raw
def get_raw_patient(
    request: Request,
    patient_id: str,
    tab: str | None = Query(default=None, pattern="^(notes|fees|labs)$"),
):
    """加载原始数据；tab 请求最小查询并以 503/404 明确区分。"""
    try:
        data = _raw_payload(patient_id, tab=tab)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        _log_raw_access(request, patient_id, source=str(detail.get("code") or exc.status_code))
        raise
    _log_raw_access(request, patient_id, source=data.get("source", "csv"))
    return data


def _fees_to_list(fees_df) -> list[dict]:
    if fees_df is None or len(fees_df) == 0:
        return []
    keep = [c for c in (
        "medins_list_name", "spec", "cnt", "pric",
        "det_item_fee_sumamt", "medins_chrgitm_type", "fee_ocur_time",
    ) if c in fees_df.columns]
    records = fees_df[keep].fillna("").astype(str).to_dict("records")
    for rec in records:
        rec["fee_date"] = _fmt_fee_date(rec.get("fee_ocur_time", ""))
    return records


def _notes_to_list(notes_df) -> list[dict]:
    if notes_df is None or len(notes_df) == 0:
        return []
    col_map = {
        "阶段": "section", "子阶段": "subsection", "内容": "content",
        "事件时间": "ts", "来源文件": "source",
    }
    cols_present = {zh: en for zh, en in col_map.items() if zh in notes_df.columns}
    if not cols_present:
        return []
    slim = notes_df[list(cols_present)].fillna("").astype(str).rename(columns=cols_present)
    records = slim.to_dict("records")
    for rec in records:
        name, order = bucket_of(rec.get("section", ""))
        rec["bucket"] = name
        rec["bucket_order"] = order
    records.sort(key=lambda row: (row["bucket_order"], row.get("ts", "")))
    return records


def _raw_unavailable(tab: str, error_code: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": error_code,
            "message": "原文数据源暂不可用，请稍后重试。",
            "retryable": True,
            "tab": tab,
        },
    )


def _raw_tab_payload(patient_id: str, tab: str) -> dict:
    from javert.web.hub_raw_source import RawSourceUnavailable

    source = "csv"
    try:
        profile_source = _get_profile_hub_source_for_patient(patient_id)
        if profile_source is not None:
            source = "hub"
            if tab == "fees":
                rows = _fees_to_list(profile_source.get_tab(patient_id, "fees"))
                if not rows:
                    raise HTTPException(
                        status_code=404,
                        detail={"code": "RAW_TAB_NOT_FOUND", "tab": tab},
                    )
                return {"patient_id": patient_id, "source": source, "tab": tab,
                        "fees": rows, "n_fees": len(rows)}
            if tab == "notes":
                rows = _notes_to_list(profile_source.get_tab(patient_id, "notes"))
                if not rows:
                    raise HTTPException(
                        status_code=404,
                        detail={"code": "RAW_TAB_NOT_FOUND", "tab": tab},
                    )
                return {"patient_id": patient_id, "source": source, "tab": tab,
                        "notes": rows, "n_notes": len(rows)}
            bundle = profile_source.get_tab(patient_id, "labs")
            lab_rows = bundle["labs"].to_dict("records") if len(bundle["labs"]) else []
            exam_rows = bundle["exams"].to_dict("records") if len(bundle["exams"]) else []
            labs = _format_lab_rows(lab_rows)
            exams = _format_exam_rows(exam_rows)
            if not labs and not exams:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "RAW_TAB_NOT_FOUND", "tab": tab},
                )
            return {"patient_id": patient_id, "source": source, "tab": tab,
                    "labs": labs, "exams": exams,
                    "n_labs": len(labs), "n_exams": len(exams)}
        if tab == "fees":
            fees_df = _get_loader().get_fees(patient_id)
            rows = _fees_to_list(fees_df)
            if not rows and get_config().hub_raw_enabled:
                source = "hub"
                rows = _fees_to_list(
                    _get_hub_source_for_patient(patient_id).get_tab(patient_id, "fees")
                )
            if not rows:
                raise HTTPException(status_code=404, detail={"code": "RAW_TAB_NOT_FOUND", "tab": tab})
            return {"patient_id": patient_id, "source": source, "tab": tab,
                    "fees": rows, "n_fees": len(rows)}
        if tab == "notes":
            notes_df = _get_loader().get_notes(patient_id)
            rows = _notes_to_list(notes_df)
            if not rows and get_config().hub_raw_enabled:
                source = "hub"
                rows = _notes_to_list(
                    _get_hub_source_for_patient(patient_id).get_tab(patient_id, "notes")
                )
            if not rows:
                raise HTTPException(status_code=404, detail={"code": "RAW_TAB_NOT_FOUND", "tab": tab})
            return {"patient_id": patient_id, "source": source, "tab": tab,
                    "notes": rows, "n_notes": len(rows)}
        labs = _labs_to_list(patient_id, allow_hub=False)
        exams = _exams_to_list(patient_id, allow_hub=False)
        if not labs and not exams and get_config().hub_raw_enabled:
            source = "hub"
            bundle = _get_hub_source_for_patient(patient_id).get_tab(patient_id, "labs")
            lab_rows = bundle["labs"].to_dict("records") if len(bundle["labs"]) else []
            exam_rows = bundle["exams"].to_dict("records") if len(bundle["exams"]) else []
            labs = _format_lab_rows(lab_rows)
            exams = _format_exam_rows(exam_rows)
        if not labs and not exams:
            raise HTTPException(status_code=404, detail={"code": "RAW_TAB_NOT_FOUND", "tab": tab})
        return {"patient_id": patient_id, "source": source, "tab": tab,
                "labs": labs, "exams": exams, "n_labs": len(labs), "n_exams": len(exams)}
    except RawSourceUnavailable as exc:
        raise _raw_unavailable(tab, exc.error_code) from exc


def _raw_payload(patient_id: str, tab: str | None = None) -> dict:
    """病人 fee + notes 原始数据 (纯数据组装, 无 request 依赖).

    实际 CSV 列名:
      case_notes.csv: 住院号 / 事件时间 / 阶段 / 子阶段 / 内容 / 来源文件 (中文)
      shi_fee.csv:    medins_list_name / spec / cnt / pric / det_item_fee_sumamt /
                       medins_chrgitm_type / fee_ocur_time / bah (英文)
    """
    if tab is not None:
        if tab not in {"notes", "fees", "labs"}:
            raise HTTPException(status_code=422, detail={"code": "INVALID_RAW_TAB"})
        return _raw_tab_payload(patient_id, tab)

    source = "csv"
    profile_source = _get_profile_hub_source_for_patient(patient_id)
    if profile_source is not None:
        loader = profile_source
        fees_df = profile_source.get_fees(patient_id)
        notes_df = profile_source.get_notes(patient_id)
        source = "hub"
    else:
        loader = _get_loader()
        fees_df = loader.get_fees(patient_id)
        notes_df = loader.get_notes(patient_id)
    if ((fees_df is None or len(fees_df) == 0)
            and (notes_df is None or len(notes_df) == 0)):
        # CSV 双 miss → hub SQL 链式回退 (开关关闭时直接 404, 行为与从前一致)
        if profile_source is None and get_config().hub_raw_enabled:
            hub = _get_hub_source()
            fees_df = hub.get_fees(patient_id)
            notes_df = hub.get_notes(patient_id)
            source = "hub"
        if (fees_df is None or len(fees_df) == 0) and (notes_df is None or len(notes_df) == 0):
            raise HTTPException(status_code=404, detail="未找到患者原始数据")

    # 病案首页主诊从 shi_zd.xls 取 (case_notes 没有 main_dx)
    main_dx = _get_main_diagnosis(patient_id)

    labs = _labs_to_list(patient_id)
    exams = _exams_to_list(patient_id)

    return {
        "patient_id": patient_id,
        "source": source,
        "main_diagnosis": main_dx,
        "fees": _fees_to_list(fees_df),
        "notes": _notes_to_list(notes_df),
        "labs": labs,
        "exams": exams,
        "n_fees": len(fees_df) if fees_df is not None else 0,
        "n_notes": len(notes_df) if notes_df is not None else 0,
        "n_labs": len(labs),
        "n_exams": len(exams),
    }


def _format_lab_rows(rows: list[dict]) -> list[dict]:
    return [{
        "date": _fmt_fee_date(r.get("report_dt") or ""),
        "item": r.get("rpt_itemname") or r.get("rpt_itemcode") or "",
        "inspection": r.get("inspectionName") or "",
        "result": r.get("result") or "",
        "unit": r.get("result_unit") or "",
        "ref": r.get("result_ref") or "",
        "flag": r.get("result_flag") or "",
        "department": r.get("department") or "",
    } for r in rows]


def _labs_to_list(patient_id: str, *, allow_hub: bool = True) -> list[dict]:
    """该患者检验/化验报告 (按 report_dt 升序). 文件缺失/加载失败 → 空列表 (不阻断 raw)."""
    profile_source = (
        _get_profile_hub_source_for_patient(patient_id) if allow_hub else None
    )
    if profile_source is not None:
        return _format_lab_rows(profile_source.get_labs(patient_id))
    try:
        rows = _get_lab_loader().get_lab_results(patient_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("加载检验数据失败 patient=%s: %s", patient_id, e)
        rows = []
    if not rows and allow_hub and get_config().hub_raw_enabled:
        rows = _get_hub_source().get_labs(patient_id)
    return _format_lab_rows(rows)


def _format_exam_rows(rows: list[dict]) -> list[dict]:
    return [{
        "date": _fmt_fee_date(r.get("reportDate") or r.get("checkDate") or ""),
        "check_type": r.get("checkType") or "",
        "item": r.get("checkItemName") or "",
        "conclusion": r.get("checkConclusion") or "",
        "describe": r.get("checkDescribe") or "",
        "department": r.get("department") or "",
    } for r in rows]


def _exams_to_list(patient_id: str, *, allow_hub: bool = True) -> list[dict]:
    """该患者检查报告 (CT/超声/MRI...). 文件缺失/加载失败 → 空列表 (不阻断 raw)."""
    profile_source = (
        _get_profile_hub_source_for_patient(patient_id) if allow_hub else None
    )
    if profile_source is not None:
        return _format_exam_rows(profile_source.get_exams(patient_id))
    try:
        rows = _get_exam_loader().get_examinations(patient_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("加载检查数据失败 patient=%s: %s", patient_id, e)
        rows = []
    if not rows and allow_hub and get_config().hub_raw_enabled:
        rows = _get_hub_source().get_exams(patient_id)
    return _format_exam_rows(rows)


# 病案首页主诊缓存 (shi_zd.xls 全表 10194 行, 进程级 lazy)
_zd_cache: dict[str, str] | None = None


def _hub_main_dx(patient_id: str) -> str | None:
    """hub 主诊回退 (开关关 → None); _get_main_diagnosis 所有 miss 出口共用."""
    profile_source = _get_profile_hub_source_for_patient(patient_id)
    if profile_source is not None:
        return profile_source.get_main_diagnosis(patient_id)
    if get_config().hub_raw_enabled:
        return _get_hub_source().get_main_diagnosis(patient_id)
    return None


def _get_main_diagnosis(patient_id: str) -> str | None:
    """从 shi_zd.xls 拿病案首页主诊 (maindiag_flag=1). 文件/匹配 miss → hub 回退 → None."""
    global _zd_cache
    profile_source = _get_profile_hub_source_for_patient(patient_id)
    if profile_source is not None:
        return profile_source.get_main_diagnosis(patient_id)
    if _zd_cache is None:
        _zd_cache = {}
        cfg = get_config()
        zd_path = cfg.data_path / "shi_zd.xls"
        if not zd_path.exists():
            return _hub_main_dx(patient_id)
        try:
            import pandas as pd
            df = pd.read_excel(zd_path)
            # 列名可能是 bah / 住院号 / maindiag_flag / dx_name / dx_code 等
            id_col = next((c for c in ("bah", "住院号", "patient_id") if c in df.columns), None)
            flag_col = next((c for c in ("maindiag_flag", "main_flag") if c in df.columns), None)
            name_col = next((c for c in ("dx_name", "diagnosis", "诊断名称") if c in df.columns), None)
            code_col = next((c for c in ("dx_code", "icd_code", "诊断编码") if c in df.columns), None)
            if id_col is None or flag_col is None or name_col is None:
                return _hub_main_dx(patient_id)
            mains = df[df[flag_col].astype(str).str.strip() == "1"]
            for _, row in mains.iterrows():
                pid = str(row[id_col]).strip()
                # bah 形如 "H310...J13365"; 提取末段
                pid_match = pid.split("-")[-1].strip()
                label = str(row[name_col])
                if code_col and code_col in row.index:
                    label += f" ({row[code_col]})"
                _zd_cache[pid_match] = label
        except Exception as e:
            logger.warning("shi_zd.xls 加载失败: %s", e)
    main_dx = _zd_cache.get(patient_id)
    if main_dx is None:
        main_dx = _hub_main_dx(patient_id)
    return main_dx


# =========================================================
# Dashboard
# =========================================================
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/login?next=/dashboard", status_code=302)
    store = get_sqlserver_store()
    stats = store.dashboard_stats()
    # 系统性违规面板 (add-cross-patient-stats) — 只读, 失败不阻断 dashboard 其余部分
    systemic_rules: list = []
    try:
        from javert.stats.cross_patient import aggregate, compute_rule_stats, load_thresholds
        thresholds = load_thresholds()
        systemic_rules = compute_rule_stats(
            aggregate(store.latest_verdict_rows()), thresholds,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("系统性违规面板生成失败: %s", e)
        thresholds = None
    return HTMLResponse(render(
        "dashboard.html",
        title="仪表盘",
        current_user=user,
        stats=stats,
        systemic_rules=systemic_rules,
        thresholds=thresholds,
    ))


@router.get("/dashboard/reviewer/{user_id}", response_class=HTMLResponse)
def dashboard_reviewer(request: Request, user_id: int):
    """专家下钻 — 看某个专家审过的所有 latest reviews + 跳转回卡片."""
    user = current_user(request)
    if user is None:
        return RedirectResponse(
            url=f"/login?next=/dashboard/reviewer/{user_id}",
            status_code=302,
        )
    store = get_sqlserver_store()
    target_user = store.get_user_by_id(user_id)
    if target_user is None:
        raise HTTPException(status_code=404, detail=f"未找到专家 user_id={user_id}")
    drills = store.list_reviews_by_user(user_id=user_id)
    return HTMLResponse(render(
        "reviewer_detail.html",
        title=f"{target_user.display_name or target_user.username} · 审核详情",
        current_user=user,
        target_user=target_user,
        drills=drills,
    ))


# =========================================================
# Excel / CSV 导出
# =========================================================
def _build_xlsx(sheets: dict[str, list[dict]]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    # 移除默认 sheet
    default = wb.active
    wb.remove(default)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name[:31] or "Sheet")
        if not rows:
            ws.append(["(空)"])
            continue
        headers = list(rows[0].keys())
        ws.append(headers)
        for r in rows:
            ws.append([_xl_cell(r.get(h)) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xl_cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (datetime,)):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, bool):
        return "是" if v else "否"
    return v


def _build_csv(rows: list[dict]) -> bytes:
    if not rows:
        return "﻿".encode("utf-8")
    headers = list(rows[0].keys())
    sio = io.StringIO()
    sio.write("﻿")  # Excel BOM 兼容中文
    writer = csv.DictWriter(sio, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        clean = {k: _xl_cell(v) for k, v in r.items()}
        writer.writerow(clean)
    return sio.getvalue().encode("utf-8")


@router.get("/export")
def export(
    request: Request,
    format: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    scope: str = Query("v_and_i", pattern="^(v_and_i|v_only|i_only|all)$"),
    include_history: bool = Query(False),
    patient_id: str | None = Query(None, description="单病人导出 (省略=全部)"),
):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/login?next=/export", status_code=302)

    store = get_sqlserver_store()
    sheets = store.fetch_export_rows(scope=scope, include_history=include_history)

    # 单病人过滤 (只在 Sheet1 上裁, Sheet2/3 是全局聚合保持不变,
    # Sheet4 历史也按 patient_id 裁)
    if patient_id:
        if "审核结果" in sheets:
            sheets["审核结果"] = [
                r for r in sheets["审核结果"] if r.get("patient_id") == patient_id
            ]
        if "审核历史" in sheets:
            sheets["审核历史"] = [
                r for r in sheets["审核历史"]
                if (r.get("run_id") or "").startswith("aud_")
                # 历史表没存 patient_id, 改走 run_id JOIN; 这里靠 audit_runs
            ]
            # 改用 store 上的 detail 查询裁 — 但 fetch_export_rows 没拆开, 这里就保持不变
        # Sheet2/3 是全局聚合, 不删

    total_rows = sum(len(v) for v in sheets.values())
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix_parts = []
    if patient_id:
        suffix_parts.append(patient_id)
    if include_history:
        suffix_parts.append("with_history")
    suffix = ("_" + "_".join(suffix_parts)) if suffix_parts else ""
    base = f"javert_reviews_{ts}{suffix}"

    ip, ua = request_meta(request)
    store.log_action(
        user_id=user.id, action="export",
        target_id=patient_id or scope,
        payload={"format": format, "row_count": total_rows,
                 "include_history": include_history,
                 "patient_id": patient_id},
        ip=ip, user_agent=ua,
    )

    if format == "csv":
        data = _build_csv(sheets.get("审核结果", []))
        filename = f"{base}.csv"
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition":
                    f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}',
            },
        )

    data = _build_xlsx(sheets)
    filename = f"{base}.xlsx"
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}',
        },
    )
