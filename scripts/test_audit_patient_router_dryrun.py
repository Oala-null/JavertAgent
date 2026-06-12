"""不调 LLM 验证 audit-patient --use-router 的 selection 路径.

直接调用 `_apply_router_prefilter`, 跟 `run_audit_patient` 走的是同一段代码,
但绕过 Runner.audit (LLM 调用). 用于端到端 lint:
  - selected (按 priority 过滤后) → router → kept 子集
  - selection_label 打印
  - router decision block 打印

跑法:
  uv run python scripts/test_audit_patient_router_dryrun.py
"""

from __future__ import annotations

import click

from javert.audit.rule_loader import load_all
from javert.commands.audit_patient import (
    _apply_router_prefilter,
    _print_router_block,
    _resolve_selection,
)
from javert.config import get_config
from javert.data.csv_loader import CsvLoader


def run_one(patient_id: str, priority: str = "P0") -> None:
    cfg = get_config()
    all_rules = load_all(cfg.rules_path)
    selected, label, _ = _resolve_selection(all_rules, priority=priority, rules_arg=None)

    loader = CsvLoader(cfg.notes_path, cfg.fees_path)

    click.echo(f"\n{'=' * 72}")
    click.echo(f"patient: {patient_id}  priority={priority}")
    click.echo(f"selected (before router): {len(selected)} rules  → label='{label}'")

    kept, new_label, decision = _apply_router_prefilter(
        selected, patient_id, loader, priority, label,
    )

    click.echo(f"\n=== applied _apply_router_prefilter ===")
    click.echo(f"new selection_label: {new_label}")
    _print_router_block(decision)
    click.echo(f"=== final selected rule_ids ({len(kept)}) ===")
    for r in kept:
        click.echo(f"  {r.rule_id}  ({r.priority}, status={r.status}, "
                   f"tmpl={getattr(r, 'derived_from_template', None)})")


def main() -> None:
    for pid in ["J40485", "J66252", "J18906"]:
        run_one(pid, priority="P0")


if __name__ == "__main__":
    main()
