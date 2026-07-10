# -*- coding: utf-8 -*-
"""javert ensure-mssql-schema — 142 幂等建 4 张 javert_* 表."""

from __future__ import annotations

import os
import sys

import click

from javert.config import PROJECT_ROOT
from javert.store.sqlserver_store import get_sqlserver_store


_DROP_SQL = """
IF OBJECT_ID(N'dbo.javert_audit_logs', N'U') IS NOT NULL
    DROP TABLE javert_audit_logs;
IF OBJECT_ID(N'dbo.javert_vio_review', N'U') IS NOT NULL
    DROP TABLE javert_vio_review;
IF OBJECT_ID(N'dbo.javert_audit_runs', N'U') IS NOT NULL
    DROP TABLE javert_audit_runs;
IF OBJECT_ID(N'dbo.javert_users', N'U') IS NOT NULL
    DROP TABLE javert_users;
"""


def run_ensure_schema(*, drop_first: bool = False) -> int:
    store = get_sqlserver_store()
    health = store.health_check()
    if not health.get("sql_server"):
        click.echo(f"error: SQL Server 不可达: {health.get('error')}", err=True)
        return 3

    if drop_first:
        if os.environ.get("JAVERT_ALLOW_DROP") != "1":
            click.echo(
                "error: --drop-first 需要 JAVERT_ALLOW_DROP=1 环境变量 (不可逆)",
                err=True,
            )
            return 2
        engine = store.get_engine()
        if engine is None:
            click.echo("error: Engine 不可用", err=True)
            return 3
        from sqlalchemy import text
        click.echo("⚠ DROP 4 张 javert_* 表 ...")
        with engine.begin() as conn:
            for stmt in _DROP_SQL.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(text(s))
        click.echo("✓ 已 DROP")

    ddl_path = PROJECT_ROOT / "scripts" / "sql" / "create_javert_tables.sql"
    click.echo(f"执行 DDL: {ddl_path}")
    ok = store.init_schema(ddl_path)
    if not ok:
        click.echo("error: DDL 执行失败 (详见日志)", err=True)
        return 1

    # 验证 4 张表都存在
    engine = store.get_engine()
    if engine is None:
        return 1
    from sqlalchemy import text
    expected = ["javert_audit_runs", "javert_users", "javert_vio_review", "javert_audit_logs"]
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name FROM sys.tables WHERE name IN "
                "('javert_audit_runs', 'javert_users', 'javert_vio_review', 'javert_audit_logs')"
            )
        ).fetchall()
        found = {r[0] for r in rows}
    missing = [t for t in expected if t not in found]
    if missing:
        click.echo(f"warn: 表缺失 {missing}", err=True)
        return 1
    click.echo(f"✓ 4 张表就绪: {sorted(found)}")
    return 0
