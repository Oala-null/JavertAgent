# -*- coding: utf-8 -*-
"""tests for web/utils/humanize_zh.py — 中文相对时间字符串."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from javert.web.utils.humanize_zh import humanize_delta_zh, humanize_since_zh


def test_just_now():
    assert humanize_delta_zh(timedelta(seconds=10)) == "刚刚"
    assert humanize_delta_zh(timedelta(seconds=59)) == "刚刚"


def test_minutes():
    assert humanize_delta_zh(timedelta(minutes=5)) == "5 分钟前"
    assert humanize_delta_zh(timedelta(minutes=59)) == "59 分钟前"


def test_hours():
    assert humanize_delta_zh(timedelta(hours=2)) == "2 小时前"
    assert humanize_delta_zh(timedelta(hours=23)) == "23 小时前"


def test_days_and_yesterday():
    assert humanize_delta_zh(timedelta(days=1)) == "昨天"
    assert humanize_delta_zh(timedelta(days=3)) == "3 天前"
    assert humanize_delta_zh(timedelta(days=6)) == "6 天前"


def test_weeks():
    assert humanize_delta_zh(timedelta(days=7)) == "上周"
    assert humanize_delta_zh(timedelta(days=14)) == "2 周前"


def test_months_years():
    assert humanize_delta_zh(timedelta(days=60)) == "2 个月前"
    assert humanize_delta_zh(timedelta(days=400)).endswith("年前")


def test_negative_delta_handled():
    # 未来时间也兜底为正
    assert humanize_delta_zh(timedelta(seconds=-30)) == "刚刚"


def test_humanize_since_none():
    assert humanize_since_zh(None) == "从未"


def test_humanize_since_naive_dt_treated_beijing():
    # 142 created_at 是北京时间 (UTC+8) 的 naive datetime → 按北京解释才对得上 UTC now
    now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)   # = 北京 20:00
    then = datetime(2026, 5, 20, 18, 0, 0)  # naive 北京 18:00 = UTC 10:00 → 距 now 2 小时
    assert humanize_since_zh(then, now=now) == "2 小时前"
