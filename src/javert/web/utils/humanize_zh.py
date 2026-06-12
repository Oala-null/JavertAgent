# -*- coding: utf-8 -*-
"""中文相对时间格式化."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 142 SQL Server 的 created_at 由 GETDATE() 默认值写入 = 服务器本地时间 (北京 UTC+8),
# 不是 UTC. 工作台展示的相对时间都来自 142, 故 naive datetime 一律按北京时间解释,
# 否则刚跑完的病人会显示"8 小时前"(now 是 UTC, then 被误当 UTC → 差 8 小时).
CHINA_TZ = timezone(timedelta(hours=8))


def humanize_delta_zh(td: timedelta) -> str:
    """正负 timedelta → "刚刚" / "5 分钟前" / "2 小时前" / "3 天前" / "上周" / "2 个月前"."""
    if td.total_seconds() < 0:
        td = -td  # 把未来时间也当"X 后", 但我们只用过去, 兜底
    total = int(td.total_seconds())
    if total < 60:
        return "刚刚"
    if total < 60 * 60:
        return f"{total // 60} 分钟前"
    if total < 60 * 60 * 24:
        return f"{total // 3600} 小时前"
    if total < 60 * 60 * 24 * 7:
        days = total // (60 * 60 * 24)
        return "昨天" if days == 1 else f"{days} 天前"
    if total < 60 * 60 * 24 * 30:
        weeks = total // (60 * 60 * 24 * 7)
        return "上周" if weeks == 1 else f"{weeks} 周前"
    if total < 60 * 60 * 24 * 365:
        months = total // (60 * 60 * 24 * 30)
        return f"{months} 个月前"
    years = total // (60 * 60 * 24 * 365)
    return f"{years} 年前"


def humanize_since_zh(then: datetime | None, now: datetime | None = None) -> str:
    if then is None:
        return "从未"
    if now is None:
        now = datetime.now(timezone.utc)
    if then.tzinfo is None:
        then = then.replace(tzinfo=CHINA_TZ)  # 142 naive datetime = 北京时间, 非 UTC
    return humanize_delta_zh(now - then)
