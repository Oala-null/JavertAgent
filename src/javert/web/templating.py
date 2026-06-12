# -*- coding: utf-8 -*-
"""Jinja2 环境单例 — 注入 humanize 过滤器 + verdict 颜色 helper."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .utils.humanize_zh import humanize_delta_zh, humanize_since_zh


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    """静态资源版本号 = app.js/style.css 最新 mtime — 每次部署 (scp 改 mtime) 自动变,
    模板据此给资源 URL 加 ?v=, 浏览器换 URL 拉新, 杜绝缓存看到旧前端 (无需手动 bump)."""
    try:
        ts = [
            (STATIC_DIR / f).stat().st_mtime
            for f in ("app.js", "style.css", "onboarding.js")
            if (STATIC_DIR / f).exists()
        ]
        return str(int(max(ts))) if ts else "0"
    except Exception:  # noqa: BLE001
        return "0"


def _verdict_color(verdict: str | None) -> str:
    """Javert / 专家 verdict 字符串 → CSS class 后缀."""
    v = (verdict or "").upper()
    if v in ("V", "VIOLATION"):
        return "v"
    if v in ("I", "INCONCLUSIVE"):
        return "i"
    if v in ("C", "CLEAN"):
        return "c"
    return "unknown"


def _verdict_label_zh(verdict: str | None) -> str:
    v = (verdict or "").upper()
    return {
        "V": "认同",
        "I": "改判不明",
        "C": "驳回",
        "VIOLATION": "违规",
        "INCONCLUSIVE": "不明",
        "CLEAN": "干净",
    }.get(v, verdict or "")


def _format_dt(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime(fmt)


def build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["humanize_delta"] = humanize_delta_zh
    env.filters["humanize_since"] = humanize_since_zh
    env.filters["verdict_color"] = _verdict_color
    env.filters["verdict_label"] = _verdict_label_zh
    env.filters["format_dt"] = _format_dt
    env.globals["now"] = lambda: datetime.now(timezone.utc)
    env.globals["asset_v"] = _asset_version()
    return env


_ENV: Environment | None = None


def get_env() -> Environment:
    global _ENV
    if _ENV is None:
        _ENV = build_env()
    return _ENV


def render(name: str, /, **ctx) -> str:
    return get_env().get_template(name).render(**ctx)
