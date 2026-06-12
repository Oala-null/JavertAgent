# -*- coding: utf-8 -*-
"""javert show — 重放历史 audit 的 trace."""

from __future__ import annotations

import json
import sys

import click

from javert.config import get_config
from javert.store.audit_store import SqliteStore


def _print_result(result) -> None:
    click.echo(f"=== run_id={result.run_id} rule={result.rule_id} patient={result.patient_id} ===")
    click.echo(f"started_at={result.started_at.isoformat()} model={result.model}")
    click.echo("")
    for i, tc in enumerate(result.tool_calls, 1):
        args = json.dumps(tc.arguments, ensure_ascii=False)
        cached = ", cached" if tc.cached else ""
        click.echo(f"[Tool #{i}] {tc.tool_name}({args}) ({tc.duration_ms}ms{cached})")
        click.echo("    " + tc.result.replace("\n", "\n    ")[:1000])
        click.echo("")
    click.echo(f"[Verdict] {result.verdict[0]} conf={result.confidence:.2f} duration={result.duration_ms / 1000:.1f}s")
    click.echo("")
    click.echo("--- evidence ---")
    for e in result.evidence:
        click.echo(f"  [{e.source}] {e.locator}: {e.text[:200]}")
    click.echo("")
    click.echo("--- reasoning ---")
    click.echo(result.reasoning)


def run_show(identifier: str, patient: str | None) -> None:
    cfg = get_config()
    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        if identifier.startswith("aud_"):
            result = store.find_by_run_id(identifier)
        else:
            if not patient:
                click.echo("rule_id 模式必须提供 --patient", err=True)
                sys.exit(2)
            result = store.find_by_rule_patient_latest(identifier, patient)
        if result is None:
            click.echo(f"未找到记录: {identifier}", err=True)
            sys.exit(1)
        _print_result(result)
    finally:
        store.close()
