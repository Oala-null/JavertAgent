# -*- coding: utf-8 -*-
"""javert list — 表格化列出规则及审计统计."""

from __future__ import annotations

import click
from tabulate import tabulate

from javert.audit.rule_loader import load_all
from javert.config import get_config
from javert.store.audit_store import SqliteStore


def run_list() -> None:
    cfg = get_config()
    rules = load_all(cfg.rules_path)
    if not rules:
        click.echo("(rules 目录为空, 先跑 javert init)")
        return

    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        summary = store.summary_by_rule()
    finally:
        store.close()

    rows = []
    for rid in sorted(rules.keys()):
        r = rules[rid]
        s = summary.get(rid, {"V": 0, "C": 0, "I": 0, "total": 0})
        verdict_str = (
            f"V:{s['V']} C:{s['C']} I:{s['I']}"
            if s["total"] > 0 else "-"
        )
        rows.append([
            rid,
            r.priority,
            r.domain,
            r.violation_type[:12],
            r.status,
            s["total"],
            verdict_str,
        ])
    click.echo(tabulate(
        rows,
        headers=["rule_id", "priority", "domain", "violation_type", "status", "runs", "verdicts"],
        tablefmt="simple",
    ))
    # 按 priority 汇总
    from collections import Counter
    prio_counts = Counter(r.priority for r in rules.values())
    status_counts = Counter(r.status for r in rules.values())
    prio_str = " / ".join(f"{p}:{prio_counts.get(p, 0)}" for p in ["P0", "P1", "P2", "P3"])
    status_str = " / ".join(f"{s}:{status_counts.get(s, 0)}" for s in ["drafting", "ready", "validated", "abandoned"])
    click.echo(f"\n共 {len(rules)} 条规则  优先级 {prio_str}  状态 {status_str}")
