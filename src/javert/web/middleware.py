# -*- coding: utf-8 -*-
"""Auth + degraded-mode 中间件.

设计:
  - PROTECTED_PREFIXES 列表外的路径无需登录 (public)
  - 当 with_mssql=False 时, 工作台路径全 503
  - 当 with_mssql=True 但 142 不通时, 工作台路径也 503 (UI 提示)
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from javert.web.auth import session_user_id

logger = logging.getLogger("javert.web.middleware")


PROTECTED_PREFIXES = (
    "/workbench",
    "/review",
    "/dashboard",
    "/export",
    "/sse/reviews",
    "/api/banner",
    "/api/patient",
    "/api/workbench",
    "/onboarding",
    "/api/onboarding",
)

PUBLIC_PREFIXES = (
    "/login",
    "/register",
    "/logout",
    "/static",
    "/healthz",
    "/api/health",
    # 已有 SPA 路由 (规则浏览) 不强制鉴权; 工作台是独立线
    "/api/rules",
    "/api/patients/sample",
    "/api/patients/pools",
    "/api/sync",
    "/api/audit",
    "/",  # 老 index.html 入口仍开放
)


def _path_protected(path: str) -> bool:
    if any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES):
        return False
    return any(path == p or path.startswith(p + "/") or path.startswith(p) for p in PROTECTED_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, with_mssql: bool = True):
        super().__init__(app)
        self.with_mssql = with_mssql

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # no-mssql 模式: 所有工作台路径 503
        if not self.with_mssql and _path_protected(path):
            return JSONResponse(
                status_code=503,
                content={"error": "工作台需要 SQL Server 连接 — 联系运维 (no-mssql 模式)"},
            )

        if _path_protected(path):
            uid = session_user_id(request)
            if uid is None:
                # API 端点返 401, 浏览器端 302
                # /review 也是纯 JSON 端点 (无 GET, 仅 POST 接受 JSON body)
                is_api = (
                    path.startswith("/api/")
                    or path.startswith("/sse/")
                    or path == "/review"
                )
                if is_api:
                    return JSONResponse(status_code=401, content={"error": "未登录"})
                return RedirectResponse(
                    url=f"/login?next={path}",
                    status_code=302,
                )

        return await call_next(request)
