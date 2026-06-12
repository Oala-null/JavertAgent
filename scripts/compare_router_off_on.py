"""有无 router 的 selection 对比 (不调 LLM, 纯 selection 路径).

对 v0.4 实测的 10 个患者跑两遍 selection:
  - off: audit-patient --priority P0       → 默认 57 条 P0 ready
  - on:  audit-patient --priority P0 --use-router → router 命中子集

输出:
  | patient | off | on | 省 | 省% |
  + 末行: 累计省的 LLM 调用数 + router 时间总和

跑法:
  uv run python scripts/compare_router_off_on.py
"""

from __future__ import annotations

import time
from typing import Optional

import click

from javert.audit.rule_loader import load_all
from javert.commands.audit_patient import _apply_router_prefilter, _resolve_selection
from javert.config import get_config
from javert.data.csv_loader import CsvLoader


# v0.4 全量审计跑过的 10 个患者 + 备注
PATIENTS: list[tuple[str, str]] = [
    ("J66252", "甲状腺恶性 (主测试, v0.4 9V)"),
    ("J18906", "脑干血管瘤 (v0.4 7V)"),
    ("J13365", "肺癌骨转 (v0.4 7V)"),
    ("K33745", "v0.4 base"),
    ("J24278", "v0.4 ext"),
    ("J19333", "v0.4 ext"),
    ("J90508", "v0.4 ext"),
    ("J61556", "弥漫大B (v0.4 16V 最重)"),
    ("J40485", "甲状腺良性 1日 (v0.4 0V 最简单)"),
    ("K03341", "v0.4 base"),
]


def _resolve_all_ready(all_rules: dict) -> list:
    """status=ready 全集 (不限 priority), 用于 'all' 模式."""
    selected = [r for r in all_rules.values() if r.status == "ready"]
    selected.sort(key=lambda r: r.rule_id)
    return selected


def run_compare(patient_id: str, priority: str = "P0", scope: str = "priority") -> dict:
    """scope='priority' 按 --priority 选; scope='all' 取全部 ready."""
    cfg = get_config()
    all_rules = load_all(cfg.rules_path)
    if scope == "all":
        selected = _resolve_all_ready(all_rules)
    else:
        selected, _, _ = _resolve_selection(all_rules, priority=priority, rules_arg=None)
    n_off = len(selected)

    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    t0 = time.perf_counter()
    # router 内部按 priority 过滤 — scope=all 时让 router 也放开
    enabled_prios = ("P0", "P1", "P2", "P3") if scope == "all" else (priority,)
    from javert.routing import RuleRouter, build_patient_record_for_router, default_shi_zd_path
    router = RuleRouter.from_defaults(enabled_priorities=enabled_prios)
    zd_path = default_shi_zd_path()
    record = build_patient_record_for_router(
        patient_id, loader,
        shi_zd_path=zd_path if zd_path.exists() else None,
    )
    decision = router.route(record)
    final_set = set(decision.final_rules)
    kept = [r for r in selected if r.rule_id in final_set]
    router_ms = (time.perf_counter() - t0) * 1000
    n_on = len(kept)
    return {
        "patient": patient_id,
        "off": n_off,
        "on": n_on,
        "saved": n_off - n_on,
        "saved_pct": (n_off - n_on) / n_off * 100 if n_off else 0,
        "kept_ids": [r.rule_id for r in kept],
        "java_triggered": list(sorted(decision.java_triggered.keys())),
        "router_ms": router_ms,
    }


def fmt_row(r: dict, label: Optional[str] = None) -> str:
    note = f" — {label}" if label else ""
    return (
        f"| {r['patient']:7} | {r['off']:3} | {r['on']:3} | "
        f"{r['saved']:3} | {r['saved_pct']:5.1f}% | "
        f"{r['router_ms']:6.1f} ms | "
        f"{','.join(r['java_triggered']) or '-':<32}"
        f"{note}"
    )


def _print_table(scope_title: str, scope: str, priority: str = "P0") -> tuple[int, int, float]:
    click.echo(f"\n=== {scope_title} ===\n")
    click.echo(
        f"| {'patient':7} | {'off':3} | {'on':3} | "
        f"{'省':3} | {'省%':5}  | router 耗时 | java rules triggered"
    )
    click.echo("|" + "-" * 80)

    total_off = 0
    total_on = 0
    total_ms = 0.0
    rows: list[dict] = []
    for pid, label in PATIENTS:
        try:
            r = run_compare(pid, priority=priority, scope=scope)
        except Exception as e:
            click.echo(f"| {pid:7} | err: {e}")
            continue
        rows.append(r)
        total_off += r["off"]
        total_on += r["on"]
        total_ms += r["router_ms"]
        click.echo(fmt_row(r, label))

    avg_saved = sum(r["saved_pct"] for r in rows) / len(rows) if rows else 0

    click.echo("|" + "-" * 80)
    click.echo(
        f"| {'TOTAL':7} | {total_off:3} | {total_on:3} | "
        f"{total_off - total_on:3} | {avg_saved:5.1f}% | "
        f"{total_ms:6.1f} ms | (avg-saved% 加权)"
    )
    saved_secs = (total_off - total_on) * 70
    click.echo(
        f"\n累计 LLM 调用: off {total_off}  vs  on {total_on}, "
        f"累计省 {total_off - total_on} 条 (avg-saved% {avg_saved:.1f}/病案)"
    )
    click.echo(
        f"按 LLM 单条 70s 中位估算: 省下 ~{saved_secs / 60:.1f} min "
        f"({len(rows)} 病案累计, 串行)"
    )
    return total_off, total_on, total_ms


def main() -> None:
    # 1) P0 only (audit-patient 默认行为)
    p0_off, p0_on, _ = _print_table(
        "audit-patient --priority P0 有无 --use-router 对比",
        scope="priority", priority="P0",
    )

    # 2) ready 全集 (P0 + P1 + P2, 111 条)
    all_off, all_on, _ = _print_table(
        "全部 ready 规则 (111 条 P0+P1+P2) 有无 --use-router 对比",
        scope="all",
    )

    # 综合
    click.echo("\n=== 综合摘要 ===")
    click.echo(f"P0 only:   {p0_off} → {p0_on}  ({(p0_off-p0_on)/p0_off*100:.1f}% 省)")
    click.echo(f"全 ready:  {all_off} → {all_on}  ({(all_off-all_on)/all_off*100:.1f}% 省)")


if __name__ == "__main__":
    main()
