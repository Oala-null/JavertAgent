# -*- coding: utf-8 -*-
"""`javert promise` 命令实现。"""

from __future__ import annotations

import click

from javert.promises.harness import harness_json, run_harness
from javert.promises.loader import (
    PromiseValidationError,
    load_repository,
    report_json,
    validation_report,
)


def run_promise_validate(*, json_output: bool = False) -> int:
    repository = None
    error = None
    try:
        repository = load_repository()
    except PromiseValidationError as exc:
        error = exc
    report = validation_report(error, repository)
    if json_output:
        click.echo(report_json(report))
    else:
        counts = report["counts"]
        click.echo(
            "Promise validate: "
            f"{report['status']} | definitions={counts['definitions']} "
            f"drift_cases={counts['drift_cases']} cases={counts['cases']} "
            f"exceptions={counts['explicit_exceptions']} issues={counts['issues']}"
        )
        for issue in report["issues"]:
            suffix = f" [{issue['field']}]" if issue["field"] else ""
            click.echo(f"- {issue['code']}: {issue['asset_id']}{suffix}")
    return 0 if error is None else 2


def run_promise_harness(*, json_output: bool = False) -> int:
    try:
        repository = load_repository()
    except PromiseValidationError as exc:
        report = validation_report(exc, None)
        click.echo(report_json(report) if json_output else "Promise run: validation failed")
        return 2
    report = run_harness(repository)
    if json_output:
        click.echo(harness_json(report))
    else:
        counts = report["counts"]
        click.echo(
            f"Promise run: {report['status']} | cases={counts['cases']} "
            f"passed={counts['passed']} failed={counts['failed']} "
            f"historical={counts['historical']}"
        )
        for item in report["results"]:
            click.echo(
                f"- {item['case_id']}: {item['status']} "
                f"({item['reason_code']}, {item['duration_bucket']})"
            )
    return 0 if report["status"] == "ok" else 1
