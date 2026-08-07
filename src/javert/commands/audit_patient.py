# -*- coding: utf-8 -*-
"""javert audit-patient — 患者维度跑一组规则.

把规则集合 (默认 P0, 跳 abandoned) 在指定患者上跑一遍, 共享 ToolExecutor 实例
(可选共享缓存), 进度行 → stderr, 患者级 summary → stdout.

并发: 默认 concurrency=1 串行; >=2 走 ThreadPoolExecutor, 失败隔离 (单条挂不取消整 batch).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import click

from javert.audit.result import AuditResult
from javert.audit.rule import Rule
from javert.audit.rule_loader import load_all
from javert.audit.runner import Runner
from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.routing import RuleRouter, build_patient_record_for_router, default_shi_zd_path
from javert.routing.types import RouterDecision
from javert.store.audit_store import SqliteStore
from javert.store.result_persister import persist_one
from javert.tools.llm_provider import LlmUnavailableError
from javert.tools.registry import build_executor


VALID_PRIORITIES = ("P0", "P1", "P2", "P3")
_CURATED_DRUG_RULE_IDS = frozenset(f"RD{number:02d}" for number in range(10, 38))


def _resolve_selection(
    all_rules: dict[str, Rule],
    priority: str,
    rules_arg: str | None,
) -> tuple[list[Rule], str, list[str]]:
    """决定本次跑哪些规则.

    Returns:
        (rules, selection_label, force_included_abandoned)
        - rules: sorted by rule_id 的 Rule 列表
        - selection_label: summary 用的描述字符串
        - force_included_abandoned: 因 --rules 被强制纳入的 abandoned rule_id 列表
    """
    if rules_arg:
        rule_ids = [s.strip() for s in rules_arg.split(",") if s.strip()]
        selected: list[Rule] = []
        force_abandoned: list[str] = []
        for rid in rule_ids:
            if rid not in all_rules:
                raise click.UsageError(f"未知 rule_id: {rid}")
            rule = all_rules[rid]
            if rule.status == "abandoned":
                force_abandoned.append(rid)
            selected.append(rule)
        selected.sort(key=lambda r: r.rule_id)
        label = f"explicit (--rules: {len(selected)} 条)"
        if force_abandoned:
            label += f", force-included abandoned: {','.join(force_abandoned)}"
        return selected, label, force_abandoned

    # priority='all' 模式: 取全部 ready (status=ready, 不限 priority)
    if priority == "all":
        selected_all = [r for r in all_rules.values() if r.status == "ready"]
        selected_all.sort(key=lambda r: r.rule_id)
        label = f"priority=all (status=ready 全集, {len(selected_all)} 条)"
        return selected_all, label, []

    # priority 单选过滤模式
    matches = [r for r in all_rules.values() if r.priority == priority]
    selected_p = [
        r
        for r in matches
        if r.status != "abandoned"
        and not (
            r.rule_id in _CURATED_DRUG_RULE_IDS
            and r.status == "drafting"
        )
    ]
    excluded = [r.rule_id for r in matches if r.status == "abandoned"]
    migration_pending = [
        r.rule_id
        for r in matches
        if r.rule_id in _CURATED_DRUG_RULE_IDS and r.status == "drafting"
    ]
    selected_p.sort(key=lambda r: r.rule_id)
    label = f"priority={priority}"
    if excluded:
        label += f" (excluded {','.join(sorted(excluded))} [abandoned])"
    if migration_pending:
        label += (
            f" (excluded {','.join(sorted(migration_pending))} "
            "[knowledge-migration-pending])"
        )
    return selected_p, label, []


def _print_summary(
    *,
    patient_id: str,
    selection_label: str,
    cache_mode: str,
    concurrency: int,
    results: list,
    total_ms: int,
    n_selected: int,
    failed_rules: list[str],
    llm_failed_rules: list[str],
    sync_counters: dict[str, int] | None = None,
    pending_run_ids: list[str] | None = None,
) -> None:
    """stdout 打印 summary block."""
    click.echo("")
    click.echo(f"=== audit-patient {patient_id} summary ===")
    click.echo(f"rule selection: {selection_label}")
    click.echo(f"cache mode: {cache_mode}")
    click.echo(f"concurrency: {concurrency}")
    click.echo("")

    n_done = len(results)
    durations = sorted(r.duration_ms for r in results)
    avg_ms = sum(durations) / n_done if n_done else 0
    p50_ms = durations[len(durations) // 2] if durations else 0

    slowest = sorted(results, key=lambda r: r.duration_ms, reverse=True)[:3]
    slow_str = (
        ", ".join(f"{r.rule_id} ({r.duration_ms / 1000:.1f}s)" for r in slowest)
        if slowest else "(无)"
    )

    n_v = sum(1 for r in results if r.verdict == "VIOLATION")
    n_c = sum(1 for r in results if r.verdict == "CLEAN")
    n_i = sum(1 for r in results if r.verdict == "INCONCLUSIVE")

    n_tc_total = sum(len(r.tool_calls) for r in results)
    n_tc_cached = sum(1 for r in results for tc in r.tool_calls if tc.cached)
    hit_rate = (n_tc_cached / n_tc_total * 100) if n_tc_total > 0 else 0.0

    # 串行/并发同语义 (harden-onsite-redlines): 单条失败标 failed 继续, 无 pending
    n_failed = len(failed_rules) + len(llm_failed_rules)
    n_pending = n_selected - n_done - n_failed
    if n_pending < 0:
        n_pending = 0

    click.echo(
        f"Total: {total_ms / 1000:.1f}s  "
        f"avg={avg_ms / 1000:.1f}s  p50={p50_ms / 1000:.1f}s"
    )
    click.echo(f"Slowest: {slow_str}")
    click.echo(
        f"Verdicts: V={n_v} / C={n_c} / I={n_i}  "
        f"({n_done} completed, {n_pending} pending, {n_failed} failed)"
    )
    if llm_failed_rules:
        click.echo(
            f"LLM unavailable rules: {','.join(sorted(llm_failed_rules))}"
        )
    click.echo(
        f"Tool calls: {n_tc_total} total, {n_tc_cached} cached "
        f"(hit_rate={hit_rate:.1f}%)"
    )

    # 142 双写状态 (audit-engine spec)
    if sync_counters is not None:
        total_writes = sum(sync_counters.values())
        synced = sync_counters.get("synced", 0)
        pending = sync_counters.get("pending", 0)
        if sync_counters.get("skipped", 0) == total_writes and total_writes > 0:
            click.echo("mssql_sync: disabled (local-only mode)")
        else:
            click.echo(
                f"mssql_sync: {synced}/{total_writes} succeeded ({pending} pending)"
            )
            if pending > 0 and pending_run_ids:
                shown = pending_run_ids[:5]
                more = (
                    f" ... ({len(pending_run_ids) - 5} more)"
                    if len(pending_run_ids) > 5 else ""
                )
                click.echo(
                    f"slow_sync: {shown}{more}  "
                    "(catch up with: javert sync-to-mssql --pending-only)"
                )


def _format_progress(
    i: int,
    n_total: int,
    rule_id: str,
    result: AuditResult,
) -> str:
    """格式化单条进度行 (与现行串行模式一致)."""
    tc_cached = sum(1 for tc in result.tool_calls if tc.cached)
    return (
        f"[{i}/{n_total}] {rule_id} → {result.verdict[0]} "
        f"conf={result.confidence:.2f} {result.duration_ms / 1000:.1f}s "
        f"tc={len(result.tool_calls)} (cached {tc_cached})"
    )


def _run_serial(
    *,
    selected: list[Rule],
    patient_id: str,
    runner: Runner,
    share_tool_cache: bool,
    store: SqliteStore,
) -> tuple[list, list[str], list[str], dict[str, int], list[str]]:
    """串行跑. 单条失败 (LLM 或其他) 标 failed 继续, 与并发模式对齐 (harden-onsite-redlines).

    Returns: (results, failed_rules, llm_failed_rules, sync_counters, pending_run_ids)
    """
    results: list = []
    failed_rules: list[str] = []
    llm_failed_rules: list[str] = []
    sync_counters: dict[str, int] = {"synced": 0, "pending": 0, "skipped": 0}
    pending_run_ids: list[str] = []
    n_total = len(selected)

    for i, rule in enumerate(selected, 1):
        try:
            result = runner.audit(
                rule, patient_id,
                reset_cache=(not share_tool_cache),
            )
        except LlmUnavailableError as exc:
            click.echo(
                f"[{i}/{n_total}] {rule.rule_id} → LLM 不可用: {exc}",
                err=True,
            )
            llm_failed_rules.append(rule.rule_id)
            continue
        except Exception as exc:  # noqa: BLE001
            click.echo(
                f"[{i}/{n_total}] {rule.rule_id} → 异常: {exc}",
                err=True,
            )
            failed_rules.append(rule.rule_id)
            continue

        try:
            state = persist_one(
                result, rule,
                triggered_by="cli-audit-patient",
                sqlite_store=store,
                source_loader=runner.loader,
            )
            key = state.get("sync_state", "pending")
            sync_counters[key] = sync_counters.get(key, 0) + 1
            if key == "pending":
                pending_run_ids.append(result.run_id)
        except Exception as exc:  # noqa: BLE001
            click.echo(
                f"[{i}/{n_total}] {rule.rule_id} → 持久化失败 (audit 已完成): {exc}",
                err=True,
            )

        results.append(result)
        click.echo(_format_progress(i, n_total, rule.rule_id, result), err=True)

    return results, failed_rules, llm_failed_rules, sync_counters, pending_run_ids


def _run_parallel(
    *,
    selected: list[Rule],
    patient_id: str,
    runner: Runner,
    share_tool_cache: bool,
    store: SqliteStore,
    concurrency: int,
) -> tuple[list, list[str], list[str], dict[str, int], list[str]]:
    """并发跑 (ThreadPoolExecutor, 失败隔离不取消整 batch).

    Returns: (results, failed_rules, llm_failed_rules, sync_counters, pending_run_ids)
    """
    results: list = []
    failed_rules: list[str] = []
    llm_failed_rules: list[str] = []
    sync_counters: dict[str, int] = {"synced": 0, "pending": 0, "skipped": 0}
    pending_run_ids: list[str] = []
    n_total = len(selected)
    completed_counter = 0
    print_lock = threading.Lock()

    # 并发模式: 在 batch 入口 set 一次 patient_context, 整 batch 共用; 结束 clear
    runner.executor.set_patient_context(patient_id)

    def _worker(rule: Rule) -> AuditResult:
        # share_tool_cache=False 时 reset_cache=True 会清缓存 — 并发下会破坏 cache 共享,
        # 因此并发模式默认 reset_cache=False (cache 内可能为空, 但不主动清).
        # 并发 + 不 share 缓存是反模式; 由 share_tool_cache 决定但实际效果都是不清.
        return runner.audit(
            rule, patient_id,
            reset_cache=False,  # 共享 executor, 不在 worker 内 reset
            manage_patient_context=False,  # 由 batch 入口管理
        )

    try:
        max_workers = min(concurrency, n_total)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_rule = {
                pool.submit(_worker, rule): rule for rule in selected
            }
            for future in as_completed(future_to_rule):
                rule = future_to_rule[future]
                try:
                    result = future.result()
                except LlmUnavailableError as exc:
                    with print_lock:
                        completed_counter += 1
                        click.echo(
                            f"[{completed_counter}/{n_total}] {rule.rule_id} → "
                            f"LLM 不可用: {exc}",
                            err=True,
                        )
                    llm_failed_rules.append(rule.rule_id)
                    continue
                except Exception as exc:  # noqa: BLE001
                    with print_lock:
                        completed_counter += 1
                        click.echo(
                            f"[{completed_counter}/{n_total}] {rule.rule_id} → "
                            f"异常: {exc}",
                            err=True,
                        )
                    failed_rules.append(rule.rule_id)
                    continue

                # 主线程 persist (SqliteStore 已加锁, 多线程也安全, 但单点更可控)
                try:
                    state = persist_one(
                        result, rule,
                        triggered_by="cli-audit-patient",
                        sqlite_store=store,
                        source_loader=runner.loader,
                    )
                    with print_lock:
                        key = state.get("sync_state", "pending")
                        sync_counters[key] = sync_counters.get(key, 0) + 1
                        if key == "pending":
                            pending_run_ids.append(result.run_id)
                except Exception as exc:  # noqa: BLE001
                    with print_lock:
                        click.echo(
                            f"[?/{n_total}] {rule.rule_id} → 持久化失败 (audit 已完成): {exc}",
                            err=True,
                        )

                results.append(result)
                with print_lock:
                    completed_counter += 1
                    click.echo(
                        _format_progress(completed_counter, n_total, rule.rule_id, result),
                        err=True,
                    )
    finally:
        runner.executor.clear_patient_context()

    return results, failed_rules, llm_failed_rules, sync_counters, pending_run_ids


def _apply_router_prefilter(
    selected: list[Rule],
    patient_id: str,
    loader: CsvLoader,
    priority: str,
    selection_label: str,
) -> tuple[list[Rule], str, RouterDecision]:
    """跑 Stage A RuleRouter, 把 selected 收缩到 router.final_rules 集合内.

    router enabled_priorities = (priority,) — 跟 cli --priority 对齐, 避免双重过滤不一致.
    """
    enabled_prios = ("P0", "P1", "P2", "P3") if priority == "all" else (priority,)
    router = RuleRouter.from_defaults(enabled_priorities=enabled_prios)
    zd_path = default_shi_zd_path()
    record = build_patient_record_for_router(
        patient_id, loader,
        shi_zd_path=zd_path if zd_path.exists() else None,
    )
    decision = router.route(record)

    final_set = set(decision.final_rules)
    kept = [r for r in selected if r.rule_id in final_set]
    kept.sort(key=lambda r: r.rule_id)

    new_label = (
        f"{selection_label} · router-prefilter "
        f"({len(kept)}/{len(selected)} kept, {decision.stats.get('java_rules_triggered', 0)} java rules triggered)"
    )
    return kept, new_label, decision


def _print_router_block(decision: RouterDecision) -> None:
    """stderr 打印 router stage A 摘要 (java rules + final list)."""
    click.echo("=== router stage A (deterministic prefilter) ===", err=True)
    click.echo(
        f"fees scanned, java rules triggered: "
        f"{decision.stats.get('java_rules_triggered', 0)} / 11, "
        f"entries matched: {decision.stats.get('java_entries_matched', 0)}",
        err=True,
    )
    if decision.java_triggered:
        for jr in sorted(decision.java_triggered.keys()):
            evs = decision.java_triggered[jr]
            # 取最多 3 个 unique fee_name 作示例
            seen: set[str] = set()
            examples: list[str] = []
            for ev in evs:
                if ev.matched_fee_name not in seen:
                    seen.add(ev.matched_fee_name)
                    examples.append(ev.matched_fee_name)
                    if len(examples) >= 3:
                        break
            click.echo(
                f"  {jr}  ({len(evs)} hits)  e.g. {examples}",
                err=True,
            )
    click.echo(
        f"final rules: {decision.n_final}  "
        f"(kept={decision.stats.get('kept', decision.n_final)} / "
        f"scanned={decision.stats.get('total_yaml_scanned', '?')})",
        err=True,
    )
    click.echo("", err=True)


def run_audit_patient(
    patient_id: str,
    priority: str = "P0",
    rules_arg: str | None = None,
    share_tool_cache: bool = True,
    concurrency: int = 1,
    use_router: bool = False,
) -> int:
    """主入口. 返回 process exit code."""
    if use_router and rules_arg:
        click.echo("✗ --use-router 与 --rules 互斥; 显式 --rules 时不走 router prefilter", err=True)
        return 2

    cfg = get_config()
    all_rules = load_all(cfg.rules_path)

    try:
        selected, selection_label, _force_abandoned = _resolve_selection(
            all_rules, priority, rules_arg,
        )
    except click.UsageError as exc:
        click.echo(f"✗ {exc.message}", err=True)
        return 2

    if not selected:
        click.echo("✗ 没有匹配的规则 (priority filter 后为空)", err=True)
        return 2

    loader = CsvLoader(cfg.notes_path, cfg.fees_path)

    # ─── Stage A: router prefilter (可选) ───
    router_decision: RouterDecision | None = None
    if use_router:
        selected, selection_label, router_decision = _apply_router_prefilter(
            selected, patient_id, loader, priority, selection_label,
        )
        if not selected:
            # router 把全部都 prune 了 → 病案完全干净, 不跑 LLM
            click.echo(f"=== audit-patient {patient_id} ===", err=True)
            click.echo(f"rule selection: {selection_label}", err=True)
            _print_router_block(router_decision)
            click.echo(f"✓ router 判定无可疑规则, 跳过 LLM 审计 (V=0 C=0 I=0, 0.0s)", err=True)
            return 0

    executor = build_executor(loader, cfg)
    runner = Runner(executor=executor, config=cfg, emit=lambda _msg: None, loader=loader)

    cache_mode = (
        "shared (reset only between patients)"
        if share_tool_cache or concurrency > 1
        else "cold-start (reset per rule)"
    )
    click.echo(f"=== audit-patient {patient_id} ===", err=True)
    click.echo(f"rule selection: {selection_label}", err=True)
    if router_decision is not None:
        _print_router_block(router_decision)
    click.echo(f"cache mode: {cache_mode}", err=True)
    click.echo(f"concurrency: {concurrency}", err=True)
    click.echo("", err=True)

    results: list = []
    failed_rules: list[str] = []
    llm_failed_rules: list[str] = []
    sync_counters: dict[str, int] = {"synced": 0, "pending": 0, "skipped": 0}
    pending_run_ids: list[str] = []
    t_total_start = time.perf_counter()

    store = SqliteStore(cfg.audit_db_path)
    store.init_schema()
    try:
        if concurrency > 1:
            (results, failed_rules, llm_failed_rules,
             sync_counters, pending_run_ids) = _run_parallel(
                selected=selected,
                patient_id=patient_id,
                runner=runner,
                share_tool_cache=share_tool_cache,
                store=store,
                concurrency=concurrency,
            )
        else:
            (results, failed_rules, llm_failed_rules,
             sync_counters, pending_run_ids) = _run_serial(
                selected=selected,
                patient_id=patient_id,
                runner=runner,
                share_tool_cache=share_tool_cache,
                store=store,
            )
    finally:
        store.close()

    total_ms = int((time.perf_counter() - t_total_start) * 1000)

    # 输出按 rule_id 排序 (并发完成顺序与 rule_id 顺序解耦, summary 仍按 rule_id)
    results.sort(key=lambda r: r.rule_id)

    _print_summary(
        patient_id=patient_id,
        selection_label=selection_label,
        cache_mode=cache_mode,
        concurrency=concurrency,
        results=results,
        total_ms=total_ms,
        n_selected=len(selected),
        failed_rules=failed_rules,
        llm_failed_rules=llm_failed_rules,
        sync_counters=sync_counters,
        pending_run_ids=pending_run_ids,
    )

    if failed_rules or llm_failed_rules:
        return 1
    return 0
