# -*- coding: utf-8 -*-
"""javert stats — 规则维度跨患者聚合 (本地 sqlite, 不依赖 142/工作台)."""

from __future__ import annotations

import click
from tabulate import tabulate

from javert.config import get_config
from javert.stats.cross_patient import aggregate, compute_rule_stats, load_thresholds
from javert.store.audit_store import SqliteStore


def run_stats(
    batch_tag: str | None,
    min_patients: int | None,
    min_v_rate: float | None,
) -> int:
    cfg = get_config()
    thresholds = load_thresholds()
    if min_patients is not None:
        thresholds.min_patients = min_patients
    if min_v_rate is not None:
        thresholds.v_rate = min_v_rate

    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        rows = store.latest_verdict_rows(batch_tag=batch_tag)
    finally:
        store.close()

    if not rows:
        click.echo("(无审计记录)" + (f" [batch_tag={batch_tag}]" if batch_tag else ""))
        return 0

    stats = compute_rule_stats(aggregate(rows), thresholds)

    table = [
        [
            s.rule_id,
            s.n_patients,
            s.v,
            s.i,
            f"{s.v_rate * 100:.0f}%",
            "★" if s.systemic else "",
        ]
        for s in stats
    ]
    click.echo(tabulate(
        table,
        headers=["rule_id", "患者数", "V", "I", "V率", "系统性"],
        tablefmt="simple",
    ))

    systemic = [s for s in stats if s.systemic]
    scope = f" [batch_tag={batch_tag}]" if batch_tag else ""
    click.echo(
        f"\n系统性违规 {len(systemic)} 条 / 共 {len(stats)} 条规则"
        f" (阈值 V率≥{thresholds.v_rate * 100:.0f}% 且 ≥{thresholds.min_patients} 患者){scope}"
    )
    click.echo("注: V 率分母 = 被审计患者数 (非全院患者数)")
    for s in systemic:
        click.echo(f"  ★ {s.rule_id}: {s.reason}")
    return 0
