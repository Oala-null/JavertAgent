# -*- coding: utf-8 -*-
"""javert init — snapshot + sample_pilot + rule_init + AuditStore.init + LLM 探活."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from javert.audit.rule_init import init_pilot_rules, PILOT_RULE_IDS
from javert.config import PROJECT_ROOT, get_config
from javert.data.csv_loader import CsvLoader
from javert.data.sample_pilot import sample_pilot_patients, write_pilot_roster
from javert.data.snapshot import take_snapshot
from javert.store.audit_store import SqliteStore
from javert.tools.llm_provider import Qwen35Provider

logger = logging.getLogger("javert.commands.init")


def _step_snapshot(cfg, refresh: bool, upstream: str) -> bool:
    upstream_dir = Path(upstream)
    if not upstream_dir.is_absolute():
        upstream_dir = (PROJECT_ROOT / upstream_dir).resolve()
    if not upstream_dir.exists():
        click.echo(f"✗ 上游目录不存在: {upstream_dir}", err=True)
        return False
    try:
        take_snapshot(
            upstream_data_dir=upstream_dir,
            target_data_dir=cfg.data_path,
            notes_filename=cfg.notes_file,
            fees_filename=cfg.fees_file,
            refresh=refresh,
        )
    except FileExistsError as exc:
        click.echo(f"⚠ 跳过 snapshot ({exc})")
        return True
    except Exception as exc:
        click.echo(f"✗ snapshot 失败: {exc}", err=True)
        return False
    click.echo(f"✓ 数据已快照到 {cfg.data_path}")
    return True


def _step_sample_pilot(cfg) -> bool:
    if cfg.pilot_roster_path.exists():
        click.echo(f"⚠ 跳过 pilot 采样: {cfg.pilot_roster_path.name} 已存在")
        return True
    try:
        loader = CsvLoader(cfg.notes_path, cfg.fees_path)
        pids = sample_pilot_patients(loader.all_notes(), loader.all_fees())
        write_pilot_roster(
            pids,
            cfg.pilot_roster_path,
            comment="pilot 50 thyroid patients (auto-sampled by javert init)",
        )
    except Exception as exc:
        click.echo(f"✗ pilot 采样失败: {exc}", err=True)
        return False
    click.echo(f"✓ pilot 名单写入 {cfg.pilot_roster_path} ({len(pids)} 患者)")
    return True


def _step_rule_init(cfg) -> bool:
    xls_candidate = PROJECT_ROOT / "2026年医疗机构自查自纠问题清单0325.csv"
    if not xls_candidate.exists():
        click.echo(f"✗ 0325 表不存在: {xls_candidate}", err=True)
        return False
    try:
        actions = init_pilot_rules(xls_candidate, cfg.rules_path)
    except Exception as exc:
        click.echo(f"✗ rule init 失败: {exc}", err=True)
        return False
    created = sum(1 for v in actions.values() if v == "created")
    skipped = sum(1 for v in actions.values() if v == "skipped")
    missing = sum(1 for v in actions.values() if v == "missing_in_xls")
    click.echo(
        f"✓ rules: {created} 个新建, {skipped} 个已存在, "
        f"{missing} 个 xls 中缺失 (目标 {len(PILOT_RULE_IDS)})"
    )
    return missing == 0


def _step_audit_store(cfg, rebuild: bool) -> bool:
    db_path = cfg.audit_db_path
    if rebuild and db_path.exists():
        db_path.unlink()
        click.echo(f"⚠ 已删除旧 db: {db_path}")
    store = SqliteStore(db_path)
    try:
        store.init_schema()
    finally:
        store.close()
    click.echo(f"✓ audit_store 准备好: {db_path}")
    return True


def _step_llm_ping(cfg) -> bool:
    provider = Qwen35Provider(cfg)
    ok = provider.verify_model()
    if ok:
        click.echo(f"✓ LLM 通: {cfg.llm_endpoint} (model={provider.model_name})")
    else:
        click.echo(f"✗ LLM 不通: {cfg.llm_endpoint}", err=True)
    return ok


def run_init(refresh_data: bool, rebuild_store: bool, upstream: str) -> None:
    cfg = get_config()
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    cfg.audit_db_path.parent.mkdir(parents=True, exist_ok=True)

    steps = [
        ("snapshot", lambda: _step_snapshot(cfg, refresh_data, upstream)),
        ("sample_pilot", lambda: _step_sample_pilot(cfg)),
        ("rule_init", lambda: _step_rule_init(cfg)),
        ("audit_store", lambda: _step_audit_store(cfg, rebuild_store)),
        ("llm_ping", lambda: _step_llm_ping(cfg)),
    ]
    failures = []
    for name, fn in steps:
        ok = fn()
        if not ok:
            failures.append(name)
    if failures:
        click.echo(f"\n部分步骤失败: {', '.join(failures)}", err=True)
        sys.exit(1)
    click.echo("\n初始化完成. 试着跑: javert dry-run R191 --patient <住院号>")
