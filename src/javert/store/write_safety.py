"""所有 SQL Server 自有库写入共用的连接前门禁。"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any


DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_\u3400-\u9fff]+$")


def owned_databases(raw: str | None = None) -> set[str]:
    value = raw if raw is not None else (os.environ.get("JAVERT_OWNED_DBS") or "TP_data_hub")
    return {item.strip() for item in value.split(",") if item.strip()}


def validate_owned_database(
    database: str,
    *,
    required_exact: str | None = None,
    owned_raw: str | None = None,
) -> str:
    target = database.strip()
    if not DATABASE_NAME_RE.fullmatch(target):
        raise ValueError("拒绝写入：数据库名含非法字符")
    if required_exact is not None and target != required_exact:
        raise ValueError(f"拒绝写入：知识库目标必须精确为 {required_exact!r}")
    allowed = owned_databases(owned_raw)
    if target not in allowed:
        raise ValueError(f"拒绝写入：目标库不在 JAVERT_OWNED_DBS 白名单 {sorted(allowed)!r}")
    return target


def connect_owned_database(
    database: str,
    connector: Callable[[str], Any],
    *,
    required_exact: str | None = None,
    owned_raw: str | None = None,
) -> Any:
    """先验证、后调用 connector；失败时保证零连接尝试。"""
    target = validate_owned_database(
        database,
        required_exact=required_exact,
        owned_raw=owned_raw,
    )
    return connector(target)
