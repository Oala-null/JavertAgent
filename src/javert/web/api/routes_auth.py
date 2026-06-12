# -*- coding: utf-8 -*-
"""鉴权路由 — /login /register /logout (GET 表单 + POST 处理)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from javert.config import get_config
from javert.store.sqlserver_store import DuplicateUsernameError, get_sqlserver_store
from javert.web.auth import (
    hash_password,
    request_meta,
    session_clear,
    session_set_user,
    verify_password,
)
from javert.web.templating import render

logger = logging.getLogger("javert.web.routes_auth")

router = APIRouter(tags=["auth"])


# slowapi 限流 — 模块级 Limiter; create_app 在 app.state.limiter 注册同一实例 +
# exception_handler. 单测 / no-mssql 不进 create_app 也能 import (decorator no-op).
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    limiter = Limiter(key_func=get_remote_address)
except Exception:  # noqa: BLE001
    limiter = None  # type: ignore[assignment]


def _rate_limit(spec: str):  # noqa: ANN202
    def decorator(func):  # noqa: ANN001
        if limiter is None:
            return func
        return limiter.limit(spec)(func)
    return decorator


def _sanitize_next(next_: str | None) -> str:
    if not next_:
        return "/workbench"
    if not next_.startswith("/") or next_.startswith("//"):
        return "/workbench"
    return next_


# =========================================================
# GET 表单
# =========================================================
@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str | None = None,
    error: str | None = None,
    msg: str | None = None,
):
    notice = None
    if msg == "pw_changed":
        notice = "密码已修改, 请用新密码登录"
    return HTMLResponse(render(
        "login.html",
        next=_sanitize_next(next),
        error=error,
        notice=notice,
        title="登录",
        current_user=None,
    ))


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, error: str | None = None):
    if not get_config().allow_register:
        return HTMLResponse(
            render(
                "register_closed.html",
                title="注册已关闭",
                current_user=None,
            ),
            status_code=403,
        )
    return HTMLResponse(render(
        "register.html",
        error=error,
        title="注册",
        current_user=None,
    ))


# =========================================================
# POST 处理
# =========================================================
@router.post("/register")
@_rate_limit("3/minute")
async def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    display_name: str | None = Form(None),
):
    if not get_config().allow_register:
        return HTMLResponse(
            render("register_closed.html", title="注册已关闭", current_user=None),
            status_code=403,
        )
    username = (username or "").strip()
    if not username or len(username) > 64:
        return HTMLResponse(render(
            "register.html",
            error="用户名必填且不超过 64 字符",
            title="注册",
            current_user=None,
        ), status_code=400)
    if not all(ch.isprintable() and not ch.isspace() for ch in username):
        return HTMLResponse(render(
            "register.html",
            error="用户名仅允许可打印非空白字符",
            title="注册",
            current_user=None,
        ), status_code=400)
    if not password:
        return HTMLResponse(render(
            "register.html",
            error="密码必填",
            title="注册",
            current_user=None,
        ), status_code=400)

    store = get_sqlserver_store()
    ip, ua = request_meta(request)
    try:
        pw_hash = hash_password(password)
        new_id = store.create_user(
            username=username,
            pw_hash=pw_hash,
            display_name=(display_name or None),
        )
    except DuplicateUsernameError:
        store.log_action(
            user_id=None,
            action="register_fail",
            target_id=username,
            payload={"reason": "duplicate_username"},
            ip=ip, user_agent=ua,
        )
        return HTMLResponse(render(
            "register.html",
            error="用户名已存在",
            title="注册",
            current_user=None,
        ), status_code=409)
    except RuntimeError as e:
        logger.warning("register: SQL Server 不可用: %s", e)
        return HTMLResponse(render(
            "register.html",
            error="数据库不可用, 请联系管理员",
            title="注册",
            current_user=None,
        ), status_code=503)

    store.log_action(
        user_id=new_id, action="register", target_id=username,
        payload={"password_len": len(password)},
        ip=ip, user_agent=ua,
    )
    # 自动登录: prev_last_login = first_login sentinel; bump last_login
    store.update_last_login(new_id)
    session_set_user(request, new_id, prev_last_login="first_login")
    store.log_action(
        user_id=new_id, action="login", target_id=username,
        ip=ip, user_agent=ua,
    )
    return RedirectResponse(url="/workbench", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/login")
@_rate_limit("5/minute")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
):
    username = (username or "").strip()
    store = get_sqlserver_store()
    ip, ua = request_meta(request)
    target = _sanitize_next(next)

    pw_hash = store.get_pw_hash(username)
    if pw_hash is None:
        store.log_action(
            user_id=None, action="login_fail", target_id=username,
            payload={"reason": "no_such_user"}, ip=ip, user_agent=ua,
        )
        return HTMLResponse(render(
            "login.html",
            next=target, error="用户名或密码错误",
            title="登录", current_user=None,
        ), status_code=401)

    if not verify_password(password, pw_hash):
        store.log_action(
            user_id=None, action="login_fail", target_id=username,
            payload={"reason": "bad_password"}, ip=ip, user_agent=ua,
        )
        return HTMLResponse(render(
            "login.html",
            next=target, error="用户名或密码错误",
            title="登录", current_user=None,
        ), status_code=401)

    user = store.get_user_by_username(username)
    if user is None:
        return HTMLResponse(render(
            "login.html",
            next=target, error="账号异常, 请联系管理员",
            title="登录", current_user=None,
        ), status_code=500)

    # 先 SELECT 老 last_login 再 UPDATE — D12 用法
    prev = store.update_last_login(user.id)
    prev_iso = prev.isoformat() if prev else "first_login"
    session_set_user(request, user.id, prev_last_login=prev_iso)
    store.log_action(
        user_id=user.id, action="login", target_id=username,
        ip=ip, user_agent=ua,
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/account/password", response_class=HTMLResponse)
def password_page(request: Request, error: str | None = None, ok: bool = False):
    from javert.web.auth import current_user as _cur
    user = _cur(request)
    if user is None:
        return RedirectResponse(url="/login?next=/account/password", status_code=302)
    return HTMLResponse(render(
        "password_change.html",
        title="修改密码",
        current_user=user,
        error=error,
        ok=ok,
    ))


@router.post("/account/password")
async def password_submit(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    from javert.web.auth import current_user as _cur
    user = _cur(request)
    if user is None:
        return RedirectResponse(url="/login?next=/account/password", status_code=302)

    store = get_sqlserver_store()
    ip, ua = request_meta(request)

    def _err(msg: str, code: int = 400):
        return HTMLResponse(
            render("password_change.html",
                   title="修改密码", current_user=user,
                   error=msg, ok=False),
            status_code=code,
        )

    # 校验旧密码
    pw_hash = store.get_pw_hash(user.username)
    if pw_hash is None or not verify_password(old_password, pw_hash):
        store.log_action(
            user_id=user.id, action="password_change_fail",
            target_id=user.username,
            payload={"reason": "bad_old_password"},
            ip=ip, user_agent=ua,
        )
        return _err("旧密码错误", code=401)

    # 校验新密码非空 + 与确认一致 + 与旧密码不同
    if not new_password:
        return _err("新密码不可空")
    if new_password != confirm_password:
        return _err("两次新密码不一致")
    if new_password == old_password:
        return _err("新密码与旧密码相同, 请换一个")

    new_hash = hash_password(new_password)
    if not store.update_pw_hash(user.id, new_hash):
        return _err("数据库写入失败, 请联系运维", code=503)

    store.log_action(
        user_id=user.id, action="password_change",
        target_id=user.username,
        ip=ip, user_agent=ua,
    )

    # 安全默认: 改密后强制重新登录 (session 清掉)
    session_clear(request)
    return RedirectResponse(
        url="/login?next=/workbench&msg=pw_changed",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/logout")
async def logout(request: Request):
    from javert.web.auth import current_user as _cur
    user = _cur(request)
    if user is not None:
        ip, ua = request_meta(request)
        get_sqlserver_store().log_action(
            user_id=user.id, action="logout", target_id=user.username,
            ip=ip, user_agent=ua,
        )
    session_clear(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
