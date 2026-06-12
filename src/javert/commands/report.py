# -*- coding: utf-8 -*-
"""javert report — 按规则汇总 verdict 分布."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click
from tabulate import tabulate

from javert.audit.rule_loader import load_all
from javert.config import get_config
from javert.store.audit_store import SqliteStore


def _parse_since(since_str: str | None) -> datetime | None:
    if since_str is None:
        return None
    try:
        return datetime.fromisoformat(since_str)
    except ValueError as exc:
        raise click.BadParameter(f"--since 格式不对 ({exc})") from exc


def run_report(since: str | None, rule_id: str | None) -> None:
    cfg = get_config()
    since_dt = _parse_since(since)
    rules = load_all(cfg.rules_path) if cfg.rules_path.exists() else {}

    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        summary = store.summary_by_rule(since=since_dt, rule_id=rule_id)
        medians = store.median_duration(rule_id=rule_id, since=since_dt)
    finally:
        store.close()

    if not summary:
        click.echo("(无审计记录)")
        return

    rows = []
    for rid in sorted(summary.keys()):
        s = summary[rid]
        domain = rules[rid].domain if rid in rules else "?"
        status = rules[rid].status if rid in rules else "?"
        rows.append([
            rid,
            domain,
            status,
            s["total"],
            f"V:{s['V']} C:{s['C']} I:{s['I']}",
            f"{s['mean_confidence']:.2f}",
            f"{int(medians.get(rid, 0))}ms",
        ])
    click.echo(tabulate(
        rows,
        headers=["rule_id", "domain", "status", "runs", "verdicts", "mean_conf", "median_dur"],
        tablefmt="simple",
    ))
