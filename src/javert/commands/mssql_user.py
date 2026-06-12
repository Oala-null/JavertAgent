# -*- coding: utf-8 -*-
"""javert mssql-user — javert_users 管理 (list / delete / reset-password).

注: 注册走 web /register, 不走 CLI (per design.md D4). 此 CLI 用于运营兜底.
"""

from __future__ import annotations

import getpass
import sys

import click

from javert.store.sqlserver_store import get_sqlserver_store
from javert.web.auth import hash_password


def _ensure_engine() -> int:
    store = get_sqlserver_store()
    engine = store.get_engine()
    if engine is None:
        click.echo("error: 142 Engine 不可用 (检查 JAVERT_SQL_* / pyodbc)", err=True)
        return 3
    health = store.health_check()
    if not health.get("sql_server"):
        click.echo(f"error: 142 不可达: {health.get('error')}", err=True)
        return 3
    return 0


def run_list() -> int:
    rc = _ensure_engine()
    if rc != 0:
        return rc
    from sqlalchemy import text
    engine = get_sqlserver_store().get_engine()
    if engine is None:
        return 3
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, username, display_name, created_at, last_login "
                "FROM javert_users ORDER BY id ASC"
            )
        ).fetchall()
    if not rows:
        click.echo("(no users)")
        return 0
    click.echo(f"{'id':>4}  {'username':<24}  {'display_name':<24}  "
               f"{'created_at':<20}  {'last_login':<20}")
    click.echo("-" * 100)
    for r in rows:
        created = r[3].strftime("%Y-%m-%d %H:%M:%S") if r[3] else "—"
        last_l = r[4].strftime("%Y-%m-%d %H:%M:%S") if r[4] else "—"
        click.echo(
            f"{int(r[0]):>4}  {(r[1] or '')[:24]:<24}  "
            f"{(r[2] or '—')[:24]:<24}  {created:<20}  {last_l:<20}"
        )
    return 0


def run_delete(username: str, *, confirm: bool) -> int:
    rc = _ensure_engine()
    if rc != 0:
        return rc
    from sqlalchemy import text
    store = get_sqlserver_store()
    engine = store.get_engine()
    if engine is None:
        return 3

    user = store.get_user_by_username(username)
    if user is None:
        click.echo(f"error: user '{username}' not found", err=True)
        return 4

    # 统计待操作行
    with engine.connect() as conn:
        n_latest = conn.execute(
            text(
                "SELECT COUNT(*) FROM javert_vio_review "
                "WHERE user_id = :uid AND is_latest = 1"
            ),
            {"uid": user.id},
        ).scalar() or 0
        n_logs = conn.execute(
            text("SELECT COUNT(*) FROM javert_audit_logs WHERE user_id = :uid"),
            {"uid": user.id},
        ).scalar() or 0

    click.echo(
        f"plan: DELETE user '{username}' (id={user.id}); "
        f"将把 {n_latest} 条 latest review 降为 is_latest=0; "
        f"保留 {n_logs} 条 audit_logs (审计 trail 不删)"
    )
    if not confirm:
        click.echo("加 --confirm 确认执行")
        return 0

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE javert_vio_review SET is_latest = 0 "
                "WHERE user_id = :uid AND is_latest = 1"
            ),
            {"uid": user.id},
        )
        conn.execute(
            text("DELETE FROM javert_users WHERE id = :uid"),
            {"uid": user.id},
        )
    store.log_action(
        user_id=None, action="user_delete_by_admin",
        target_id=username,
        payload={"deleted_id": user.id, "demoted_reviews": int(n_latest)},
    )
    click.echo(f"✓ user '{username}' deleted, {n_latest} reviews demoted")
    return 0


def run_create(username: str, display_name: str | None) -> int:
    rc = _ensure_engine()
    if rc != 0:
        return rc
    store = get_sqlserver_store()
    if store.get_user_by_username(username) is not None:
        click.echo(f"error: user '{username}' 已存在", err=True)
        return 4
    pw1 = getpass.getpass(f"密码 for {username}: ")
    pw2 = getpass.getpass("再次输入: ")
    if pw1 != pw2:
        click.echo("error: 两次输入不一致", err=True)
        return 2
    if not pw1:
        click.echo("error: 密码不可空", err=True)
        return 2
    from javert.store.sqlserver_store import DuplicateUsernameError
    try:
        new_id = store.create_user(
            username=username,
            pw_hash=hash_password(pw1),
            display_name=display_name,
        )
    except DuplicateUsernameError:
        click.echo(f"error: user '{username}' 已存在", err=True)
        return 4
    store.log_action(
        user_id=new_id, action="register_by_admin",
        target_id=username,
        payload={"display_name": display_name},
    )
    click.echo(f"✓ user '{username}' (id={new_id}) created")
    return 0


def run_reset_password(username: str) -> int:
    rc = _ensure_engine()
    if rc != 0:
        return rc
    store = get_sqlserver_store()
    user = store.get_user_by_username(username)
    if user is None:
        click.echo(f"error: user '{username}' not found", err=True)
        return 4

    pw1 = getpass.getpass(f"新密码 for {username}: ")
    pw2 = getpass.getpass("再次输入: ")
    if pw1 != pw2:
        click.echo("error: 两次输入不一致", err=True)
        return 2
    if not pw1:
        click.echo("error: 密码不可空", err=True)
        return 2

    new_hash = hash_password(pw1)
    from sqlalchemy import text
    engine = store.get_engine()
    if engine is None:
        return 3
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE javert_users SET pw_hash = :h WHERE id = :uid"),
            {"h": new_hash, "uid": user.id},
        )
    store.log_action(
        user_id=None, action="password_reset_by_admin",
        target_id=username, payload={"target_user_id": user.id},
    )
    click.echo(f"✓ password reset for '{username}'")
    return 0


# =========================================================
# Click 子命令组
# =========================================================
@click.group("mssql-user")
def mssql_user_group() -> None:
    """142 javert_users 管理 (list / delete / reset-password)."""


@mssql_user_group.command("list")
def list_cmd() -> None:
    sys.exit(run_list())


@mssql_user_group.command("create")
@click.argument("username")
@click.option("--display-name", default=None, help="显示名 (可空; null 时回退 username)")
def create_cmd(username: str, display_name: str | None) -> None:
    """新建账号. 交互式输入密码 (两次确认). 操作者用于替专家分配账号."""
    sys.exit(run_create(username, display_name))


@mssql_user_group.command("delete")
@click.argument("username")
@click.option("--confirm", is_flag=True, help="实际执行 (默认 dry-run)")
def delete_cmd(username: str, confirm: bool) -> None:
    sys.exit(run_delete(username, confirm=confirm))


@mssql_user_group.command("reset-password")
@click.argument("username")
def reset_password_cmd(username: str) -> None:
    sys.exit(run_reset_password(username))
