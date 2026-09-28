# -*- coding: utf-8 -*-
"""javert dry-run — 单跑一条规则, 打印完整 trace."""

from __future__ import annotations

import sys

import click

from javert.audit.rule_loader import load_rule
from javert.audit.runner import Runner, stdout_emit
from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.store.result_persister import persist_one
from javert.tools.llm_provider import LlmUnavailableError
from javert.tools.registry import build_executor


def run_dry_run(rule_id: str, patient_id: str) -> None:
    cfg = get_config()
    rule_path = cfg.rules_path / f"{rule_id}.yaml"
    rule = load_rule(rule_path)
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    executor = build_executor(loader, cfg)
    runner = Runner(executor=executor, config=cfg, emit=stdout_emit, loader=loader)

    click.echo(f"=== dry-run rule={rule_id} patient={patient_id} ===")
    try:
        result = runner.audit(rule, patient_id)
    except LlmUnavailableError as exc:
        click.echo(f"\n✗ LLM 不可用: {exc}", err=True)
        sys.exit(2)

    state = persist_one(
        result, rule, triggered_by="cli-dry-run", source_loader=loader,
    )
    sync_label = {
        "synced": f"✓ {cfg.sql_host}",
        "pending": "⏳ 待回灌 (本地已存)",
        "skipped": "⊘ sql_enabled=false",
    }.get(state["sync_state"], "?")
    click.echo(f"\n[Saved] run_id={result.run_id}  SQLite=✓  142={sync_label}")
