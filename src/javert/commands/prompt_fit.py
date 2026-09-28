# -*- coding: utf-8 -*-
"""javert prompt-fit — 把模板渲染回规则 yaml 的 prompt_addon."""

from __future__ import annotations

import sys

import click

from javert.templating.prompt_fit_runner import run_prompt_fit


def run_prompt_fit_cli(
    rule_id: str,
    template_id: str,
    vars_path: str | None,
    interactive: bool,
    auto: bool,
    dry_run: bool,
    output_path: str | None,
    save_vars: str | None,
    force: bool = False,
) -> int:
    """三模式互斥检查; 然后委托 run_prompt_fit."""
    chosen: list[str] = []
    if vars_path is not None:
        chosen.append("--vars")
    if interactive:
        chosen.append("--interactive")
    if auto:
        chosen.append("--auto")
    if len(chosen) != 1:
        if len(chosen) > 1:
            click.echo(
                f"✗ 三种模式互斥, 当前指定: {', '.join(chosen)}",
                err=True,
            )
        else:
            click.echo("✗ 必须指定三模式之一: --vars <path> / --interactive / --auto", err=True)
        return 2
    mode = "vars" if vars_path else ("interactive" if interactive else "auto")
    return run_prompt_fit(
        rule_id,
        template_id,
        mode=mode,
        vars_file=vars_path,
        dry_run=dry_run,
        output_path=output_path,
        save_vars=save_vars,
        force=force,
    )
