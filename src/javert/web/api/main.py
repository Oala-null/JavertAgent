# -*- coding: utf-8 -*-
"""Javert Web FastAPI 入口.

启动:
    uv run javert web                        # 默认 127.0.0.1:8090
    uv run javert web --host 0.0.0.0 --port 8090 --reload
    JAVERT_SQL_ENABLED=false uv run javert web   # 关闭 142 双写

或直接:
    uv run uvicorn javert.web.api.main:app --reload --port 8090
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from javert.config import get_config
from javert.store.audit_store import SqliteStore
from javert.store.sqlserver_store import get_sqlserver_store
from javert.web.middleware import AuthMiddleware

from . import (
    routes_audit,
    routes_auth,
    routes_onboarding,
    routes_patients,
    routes_rules,
    routes_sse,
    routes_sync,
    routes_workbench,
)
from .heartbeat import SyncWorker

logger = logging.getLogger("javert.web.api.main")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动: 初始化 SQLite + 142 schema + SyncWorker; 关闭: 停 worker + dispose."""
    cfg = get_config()

    # 1. 本地 SQLite schema (强制)
    cfg.audit_db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        logger.info("SQLite schema 已确认: %s", cfg.audit_db_path)
    finally:
        store.close()

    # 2. 142 schema (可选, 失败仅 warn — 心跳 worker 会持续 retry)
    sql142 = get_sqlserver_store()
    if cfg.sql_enabled:
        if sql142.init_schema():
            logger.info(
                "142 Javert_audit_runs 已确认: %s/%s",
                cfg.sql_host, cfg.sql_database,
            )
        else:
            logger.warning(
                "142 schema init 失败. 心跳 worker 仍会持续 retry. "
                "可设 JAVERT_SQL_ENABLED=false 关闭."
            )
    else:
        logger.info("142 双写已被 sql_enabled=false 关闭")

    # 3. SyncWorker (心跳 + 回灌)
    worker = SyncWorker(interval_s=30, batch_size=50)
    app.state.sync_worker = worker
    if cfg.sql_enabled:
        await worker.start()
    else:
        logger.info("sql_enabled=false → 不启动 SyncWorker")

    # 4. AuditWatcher (SSE 拉 new_audit_run 用) — 仅工作台模式开
    if getattr(app.state, "with_mssql", False) and cfg.sql_enabled:
        try:
            await routes_sse.audit_watcher.start()
        except Exception as e:  # noqa: BLE001
            logger.warning("AuditWatcher 启动失败 (SSE 仅 review_submitted): %s", e)

    # 5. 数据预热 (后台线程, 不阻塞启动) — 首个访客不再付 30-90s CSV 冷读.
    #    条件借用 sql_enabled: 生产 (62) 恒 true; 测试/本地演示 false 时跳过, 免拖慢 pytest.
    if getattr(app.state, "with_mssql", False) and cfg.sql_enabled:
        def _warmup() -> None:
            try:
                from javert.web.patient_overview import get_fees_sum_map

                from . import routes_workbench
                loader = routes_workbench._get_loader()
                loader.all_notes()
                loader.all_fees()
                get_fees_sum_map(loader)
                logger.info("数据预热完成 (notes/fees/费用汇总)")
            except Exception as e:  # noqa: BLE001
                logger.warning("数据预热失败 (首个请求现付冷加载): %s", e)

        threading.Thread(target=_warmup, name="javert-warmup", daemon=True).start()

    yield

    if cfg.sql_enabled:
        await worker.stop()
    try:
        await routes_sse.audit_watcher.stop()
    except Exception:  # noqa: BLE001
        pass
    sql142.dispose()


def create_app(with_mssql: bool | None = None) -> FastAPI:
    cfg = get_config()
    if with_mssql is None:
        with_mssql = cfg.web_with_mssql

    # harden-onsite-redlines: 生产形态 (with_mssql) session secret 仍是源码默认值 →
    # 任意人可伪造用户 cookie. fail-fast 放这里而非 CLI, `uvicorn ...main:app` 直起同样拦截.
    from javert.config import JavertConfig
    if with_mssql and cfg.session_secret == JavertConfig.model_fields["session_secret"].default:
        raise RuntimeError(
            "JAVERT_SESSION_SECRET 仍是源码默认值 (可伪造会话) — "
            "请 `set -a && source .env && set +a` 或设置环境变量后再启动. "
            "本地 dev 可用 `javert web --no-mssql`."
        )

    app = FastAPI(
        title="Javert Web",
        version="0.1.0",
        description="医保自查自纠「做不了」规则审计 + 专家审核工作台",
        lifespan=lifespan,
    )
    app.state.with_mssql = bool(with_mssql)

    # 中间件 (注册顺序: Auth 在内, Session 在外, CORS 最外)
    app.add_middleware(AuthMiddleware, with_mssql=bool(with_mssql))
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.session_secret,
        session_cookie=cfg.session_cookie_name,
        max_age=cfg.session_max_age,
        same_site=cfg.session_same_site,
        https_only=cfg.session_https_only,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由
    app.include_router(routes_rules.router)
    app.include_router(routes_patients.router)
    app.include_router(routes_audit.router)
    app.include_router(routes_sync.router)
    if with_mssql:
        # slowapi 限流: 复用 routes_auth.limiter, 在此注册 exception_handler
        try:
            from slowapi import _rate_limit_exceeded_handler
            from slowapi.errors import RateLimitExceeded

            if routes_auth.limiter is not None:
                app.state.limiter = routes_auth.limiter

                def _rate_limited_handler(request, exc):
                    # raw 端点超限也留审计痕 (phi-access-audit spec: 429 请求可事后追查)
                    routes_workbench.log_raw_rate_limited(request)
                    return _rate_limit_exceeded_handler(request, exc)

                app.add_exception_handler(RateLimitExceeded, _rate_limited_handler)
        except Exception as e:  # noqa: BLE001
            logger.warning("slowapi exception_handler 注册失败: %s", e)

        app.include_router(routes_auth.router)
        app.include_router(routes_workbench.router)
        app.include_router(routes_onboarding.router)
        app.include_router(routes_sse.router)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "mssql": bool(with_mssql)}

    @app.get("/api/health")
    def health():
        # 不在每次 /api/health 都打 142 (前端 polling 频繁会压垮),
        # 复用 SyncWorker 的 last_health (心跳间隔默认 30s).
        worker = getattr(app.state, "sync_worker", None)
        if worker is not None and worker.last_run_at is not None:
            sql_server_142 = worker.last_health
        else:
            # worker 还没首次 tick, 直接同步打一下 (启动初期)
            sql_server_142 = get_sqlserver_store().health_check()
        return {
            "status": "ok",
            "version": "0.1.0",
            "model": cfg.llm_model,
            "llm_endpoint": cfg.llm_endpoint,
            "sql_enabled": cfg.sql_enabled,
            "sql_server_142": sql_server_142,
            "with_mssql": bool(with_mssql),
            "sync_worker": worker.snapshot() if worker else None,
        }

    if STATIC_DIR.exists():
        app.mount(
            "/static", StaticFiles(directory=STATIC_DIR), name="static",
        )

        @app.get("/")
        def index():
            index_html = STATIC_DIR / "index.html"
            if not index_html.exists():
                return {"error": f"index.html not found at {index_html}"}
            return FileResponse(index_html)

    return app


app = create_app()
