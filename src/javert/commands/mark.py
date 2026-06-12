# -*- coding: utf-8 -*-
"""javert mark — 变更规则 status."""

from __future__ import annotations

import sys

import click

from javert.audit.rule_loader import load_rule
from javert.audit.rule_writer import update_status
from javert.audit.state_machine import StatusTransitionError, validate_transition
from javert.config import get_config


def run_mark(rule_id: str, new_status: str, force: bool) -> None:
    cfg = get_config()
    path = cfg.rules_path / f"{rule_id}.yaml"
    rule = load_rule(path)
    try:
        validate_transition(rule.status, new_status, force=force)  # type: ignore[arg-type]
    except StatusTransitionError as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(2)
    update_status(path, new_status)
    click.echo(f"✓ {rule_id}: {rule.status} → {new_status}")
