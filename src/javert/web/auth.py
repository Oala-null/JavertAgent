# -*- coding: utf-8 -*-
"""鉴权工具 — bcrypt hash + session 解析.

session cookie 由 starlette `SessionMiddleware` 管理 (itsdangerous 签名).
本模块只是提供 hash / verify / 当前 user 依赖注入的薄壳.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import Request

from javert.store.models import User
from javert.store.sqlserver_store import SqlServerStore, get_sqlserver_store

logger = logging.getLogger("javert.web.auth")


def hash_password(plain: str) -> str:
    """bcrypt 12 轮. UTF-8 编码 + 截断到 72 字节 (bcrypt 协议上限)."""
    import bcrypt as _bcrypt
    pw_bytes = plain.encode("utf-8")[:72]
    salt = _bcrypt.gensalt(rounds=12)
    return _bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    import bcrypt as _bcrypt
    try:
        pw_bytes = plain.encode("utf-8")[:72]
        return _bcrypt.checkpw(pw_bytes, hashed.encode("utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.debug("bcrypt verify 异常: %s", e)
        return False


# =========================================================
# Session helpers
# =========================================================
def session_set_user(request: Request, user_id: int, prev_last_login: Optional[str]) -> None:
    request.session["user_id"] = int(user_id)
    request.session["prev_last_login"] = prev_last_login or "first_login"


def session_clear(request: Request) -> None:
    request.session.clear()


def session_user_id(request: Request) -> int | None:
    try:
        uid = request.session.get("user_id")
    except AssertionError:
        # 未注册 SessionMiddleware 的情况 (单测 / no-mssql)
        return None
    return int(uid) if uid is not None else None


# =========================================================
# 当前 user (依赖注入)
# =========================================================
def current_user(request: Request) -> User | None:
    """从 session 拿 user_id → SqlServerStore.get_user_by_id."""
    uid = session_user_id(request)
    if uid is None:
        return None
    store = get_sqlserver_store()
    return store.get_user_by_id(uid)


def require_user(request: Request) -> User:
    """中间件保护时不会用到 (那里走 302), 但 API 内可作 422 兜底."""
    user = current_user(request)
    if user is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="未登录")
    return user


def request_meta(request: Request) -> tuple[str | None, str | None]:
    """提取 ip + user_agent (供 log_action 用)."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")[:255]
    return ip, ua or None
