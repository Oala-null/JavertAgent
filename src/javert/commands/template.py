# -*- coding: utf-8 -*-
"""javert template — 列出 / 查看 / 校验模板."""

from __future__ import annotations

import sys

import click
from tabulate import tabulate

from javert.config import get_config
from javert.templating import (
    TemplateValidationError,
    load_all_templates,
    load_template,
)


def run_template_list() -> int:
    cfg = get_config()
    try:
        tmpls = load_all_templates(cfg.templates_path)
    except TemplateValidationError as exc:
        click.echo(f"✗ 模板加载失败: {exc}", err=True)
        return 1
    if not tmpls:
        click.echo(f"(templates 目录为空: {cfg.templates_path})")
        return 0
    rows = []
    for tid in sorted(tmpls.keys()):
        t = tmpls[tid]
        rows.append([
            tid,
            t.name,
            t.description[:30] or "-",
            len(t.fields),
            t.status,
        ])
    click.echo(tabulate(
        rows,
        headers=["template_id", "name", "description", "fields", "status"],
        tablefmt="simple",
    ))
    click.echo(f"\n共 {len(tmpls)} 个模板")
    return 0


def run_template_show(template_id: str) -> int:
    cfg = get_config()
    path = cfg.templates_path / f"{template_id}.yaml"
    try:
        tpl = load_template(path)
    except (FileNotFoundError, TemplateValidationError) as exc:
        click.echo(f"✗ 模板加载失败: {exc}", err=True)
        return 1
    click.echo(f"# {tpl.template_id} ({tpl.name})")
    click.echo(f"  description: {tpl.description}")
    click.echo(f"  status: {tpl.status}")
    click.echo(f"\n## master_prompt ({len(tpl.master_prompt)} chars)")
    if tpl.master_prompt:
        for line in tpl.master_prompt.splitlines():
            click.echo(f"  | {line}")
    else:
        click.echo("  (空)")
    click.echo(f"\n## fields ({len(tpl.fields)})")
    if tpl.fields:
        rows = []
        for f in tpl.fields:
            rows.append([
                f.name,
                f.type,
                "Y" if f.required else "-",
                "Y" if f.default is not None else "-",
                ",".join(f.options) if f.options else "-",
                (f.desc or "")[:40],
            ])
        click.echo(tabulate(
            rows,
            headers=["name", "type", "req", "def", "options", "desc"],
            tablefmt="simple",
        ))
    else:
        click.echo("  (无)")
    for label, body in (
        ("keywords_template", tpl.keywords_template),
        ("tools_template", tpl.tools_template),
        ("signal_template", tpl.signal_template),
    ):
        if body.strip():
            click.echo(f"\n## {label}")
            for line in body.splitlines():
                click.echo(f"  | {line}")
    return 0


def run_template_validate(template_id: str) -> int:
    cfg = get_config()
    path = cfg.templates_path / f"{template_id}.yaml"
    try:
        tpl = load_template(path)
    except (FileNotFoundError, TemplateValidationError) as exc:
        click.echo(f"✗ {template_id}: {exc}", err=True)
        return 1
    if tpl.is_empty:
        click.echo(f"{template_id}: empty (not yet filled)")
        return 0
    if tpl.status == "partial":
        click.echo(f"{template_id}: partial (master_prompt 或 fields 之一未填)")
        return 0
    click.echo(f"✓ {template_id}: ready")
    return 0
