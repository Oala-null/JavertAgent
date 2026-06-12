# -*- coding: utf-8 -*-
"""javert run — 批跑一条规则 (单患者或 pilot 50)."""

from __future__ import annotations

import sys

import click

from javert.audit.rule_loader import load_rule
from javert.audit.runner import Runner
from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.data.snapshot import load_pilot_roster
from javert.store.audit_store import SqliteStore
from javert.store.result_persister import persist_one
from javert.tools.llm_provider import LlmUnavailableError
from javert.tools.registry import build_executor


def run_batch(rule_id: str, patient: str | None, use_pilot: bool) -> None:
    cfg = get_config()
    rule = load_rule(cfg.rules_path / f"{rule_id}.yaml")

    if use_pilot:
        pids = load_pilot_roster(cfg.pilot_roster_path)
    else:
        pids = [patient]  # type: ignore[list-item]

    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    executor = build_executor(loader, cfg)
    runner = Runner(executor=executor, config=cfg, emit=lambda _msg: None, loader=loader)

    # 批量场景复用一个 SqliteStore 连接 (循环内共享)
    store = SqliteStore(cfg.audit_db_path)
    store.init_schema()
    sql142_synced = 0
    sql142_pending = 0
    failed = 0
    try:
        for i, pid in enumerate(pids, 1):
            try:
                result = runner.audit(rule, pid)
                state = persist_one(
                    result, rule,
                    triggered_by="cli-run",
                    sqlite_store=store,
                )
                if state["sync_state"] == "synced":
                    sql142_synced += 1
                elif state["sync_state"] == "pending":
                    sql142_pending += 1
                click.echo(
                    f"[{i}/{len(pids)}] {rule_id} {pid} → {result.verdict[0]} "
                    f"conf={result.confidence:.2f} {result.duration_ms}ms "
                    f"142={state['sync_state']}",
                    err=True,
                )
            except LlmUnavailableError as exc:
                click.echo(f"[{i}/{len(pids)}] {rule_id} {pid} → LLM 不可用: {exc}", err=True)
                failed += 1
            except Exception as exc:
                click.echo(f"[{i}/{len(pids)}] {rule_id} {pid} → 异常: {exc}", err=True)
                failed += 1
    finally:
        store.close()
    click.echo(
        f"\n本地写入完成. 142 已同步 {sql142_synced}/{len(pids)}, "
        f"待回灌 {sql142_pending} (后台 worker 会 retry)",
        err=True,
    )
    if failed:
        click.echo(f"\n{failed}/{len(pids)} 失败", err=True)
        sys.exit(1)
