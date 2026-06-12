#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_priority_csv — 把 docs/163规则可行性分析表.csv 的 priority 灌进 yaml.

操作:
  - yaml 已存在: 只 patch priority 字段, 其他字段保留
  - yaml 不存在: 从 0325.xls 拿 domain/example/question/violation_type 建骨架
  - CSV 中 priority 列为空: 报 warning 跳过

用法:
  python scripts/sync_priority_csv.py --dry-run
  python scripts/sync_priority_csv.py --write
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

# 把 src/ 加入 path 以便直接跑脚本
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from javert.audit.rule import Rule  # noqa: E402
from javert.audit.rule_init import load_xls_rules  # noqa: E402
from javert.audit.rule_loader import load_rule  # noqa: E402
from javert.audit.rule_writer import update_priority, write_rule  # noqa: E402


DEFAULT_CSV_PATH = PROJECT_ROOT / "docs" / "163规则可行性分析表.csv"
DEFAULT_XLS_PATH = PROJECT_ROOT / "2026年医疗机构自查自纠问题清单0325.csv"
DEFAULT_RULES_DIR = PROJECT_ROOT / "configs" / "rules"

PRIORITY_PATTERN = re.compile(r"^(P[0-3])")


def parse_priority_cell(cell: str) -> str | None:
    """从 CSV 的『Javert 优先级』列提取 P0/P1/P2/P3. 无效返回 None."""
    cell = (cell or "").strip()
    if not cell:
        return None
    m = PRIORITY_PATTERN.match(cell)
    return m.group(1) if m else None


def read_csv_priorities(csv_path: Path) -> list[tuple[str, str | None]]:
    """读 CSV → [(rule_id, priority_or_None), ...] 保留出现顺序."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 不存在: {csv_path}")
    out: list[tuple[str, str | None]] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # 跳过表头
        for row in reader:
            if len(row) < 4:
                continue
            rid = (row[0] or "").strip()
            if not re.match(r"^R\d{3}$", rid):
                continue
            prio = parse_priority_cell(row[3])
            out.append((rid, prio))
    return out


def plan_actions(
    csv_rows: list[tuple[str, str | None]],
    rules_dir: Path,
    xls_rules: dict[str, Rule],
    create_priorities: set[str],
) -> list[dict]:
    """对每行 CSV 计算 action plan, 不写盘.

    Args:
        create_priorities: 哪些 priority 的缺失 yaml 要建骨架 (默认 {"P0"});
            其他 priority 的缺失 yaml 标 skip_not_in_create_scope.

    返回 list of dict: {rule_id, action, detail}
    action ∈ {warning_missing_priority, unchanged, patch, create,
             skip_no_xls, skip_not_in_create_scope, warning_load_fail}
    """
    plans: list[dict] = []
    for rid, prio in csv_rows:
        path = rules_dir / f"{rid}.yaml"
        if prio is None:
            plans.append({
                "rule_id": rid, "action": "warning_missing_priority",
                "new_priority": None, "detail": "CSV priority 列为空",
            })
            continue
        if path.exists():
            try:
                rule = load_rule(path)
            except Exception as exc:
                plans.append({
                    "rule_id": rid, "action": "warning_load_fail",
                    "new_priority": None, "detail": str(exc),
                })
                continue
            if rule.priority == prio:
                plans.append({
                    "rule_id": rid, "action": "unchanged",
                    "new_priority": prio, "detail": f"yaml 已是 {prio}",
                })
            else:
                plans.append({
                    "rule_id": rid, "action": "patch",
                    "new_priority": prio,
                    "detail": f"priority {rule.priority} → {prio}",
                })
        else:
            if prio not in create_priorities:
                plans.append({
                    "rule_id": rid, "action": "skip_not_in_create_scope",
                    "new_priority": prio,
                    "detail": f"{prio} 不在建骨架范围 ({sorted(create_priorities)})",
                })
                continue
            if rid not in xls_rules:
                plans.append({
                    "rule_id": rid, "action": "skip_no_xls",
                    "new_priority": prio,
                    "detail": "0325.xls 中无此 rule_id, 无法建骨架",
                })
                continue
            plans.append({
                "rule_id": rid, "action": "create",
                "new_priority": prio,
                "detail": f"骨架 (priority={prio}, status=drafting)",
            })
    return plans


def apply_actions(plans: list[dict], rules_dir: Path, xls_rules: dict[str, Rule]) -> None:
    """按 plan 写盘. 调用前 plans 必须已 plan_actions 过."""
    for p in plans:
        rid = p["rule_id"]
        action = p["action"]
        new_prio = p["new_priority"]
        path = rules_dir / f"{rid}.yaml"
        if action == "patch":
            update_priority(path, new_prio)
        elif action == "create":
            xls_rule = xls_rules[rid]
            new_rule = xls_rule.model_copy(update={"priority": new_prio})
            write_rule(new_rule, path)
        # unchanged / warning / skip 不动盘


def format_plan(plans: list[dict], verbose: bool = False) -> str:
    """格式化 plan. verbose=False 时只显示有动作的行 (patch/create/warning)."""
    lines = []
    counts: dict[str, int] = {}
    for p in plans:
        counts[p["action"]] = counts.get(p["action"], 0) + 1
        if not verbose and p["action"] in ("unchanged", "skip_not_in_create_scope"):
            continue
        icon = {
            "patch": "~",
            "create": "+",
            "unchanged": " ",
            "warning_missing_priority": "!",
            "warning_load_fail": "!",
            "skip_no_xls": "?",
            "skip_not_in_create_scope": ".",
        }.get(p["action"], "?")
        lines.append(f"  {icon} {p['rule_id']:<6} {p['action']:<26} {p['detail']}")
    if not verbose:
        n_hidden = counts.get("unchanged", 0) + counts.get("skip_not_in_create_scope", 0)
        if n_hidden:
            lines.append(f"  ... ({n_hidden} 行 unchanged/skip 已隐藏; 加 --verbose 查看)")
    lines.append("")
    summary_parts = []
    for k in ("create", "patch", "unchanged", "skip_not_in_create_scope",
             "warning_missing_priority", "warning_load_fail", "skip_no_xls"):
        if counts.get(k, 0):
            summary_parts.append(f"{k}={counts[k]}")
    lines.append("汇总: " + ", ".join(summary_parts) if summary_parts else "汇总: (无)")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只打印 plan, 不写盘 (默认)")
    parser.add_argument("--write", action="store_true", help="执行写盘")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--xls", type=Path, default=DEFAULT_XLS_PATH)
    parser.add_argument("--rules-dir", type=Path, default=DEFAULT_RULES_DIR)
    parser.add_argument(
        "--create-priorities",
        default="P0",
        help="哪些 priority 的缺失 yaml 要建骨架, 逗号分隔 (default: P0)",
    )
    parser.add_argument("--verbose", action="store_true", help="显示全部行 (含 unchanged/skip)")
    args = parser.parse_args()

    if not args.write and not args.dry_run:
        # 默认 dry-run, 安全
        args.dry_run = True

    create_priorities = {p.strip() for p in args.create_priorities.split(",") if p.strip()}

    print(f"CSV: {args.csv}")
    print(f"XLS: {args.xls}")
    print(f"rules_dir: {args.rules_dir}")
    print(f"create_priorities: {sorted(create_priorities)}")
    print("")

    csv_rows = read_csv_priorities(args.csv)
    print(f"CSV 解析到 {len(csv_rows)} 行 rule")

    xls_rules = load_xls_rules(args.xls)
    print(f"XLS 解析到 {len(xls_rules)} 条「做不了」规则")
    print("")

    plans = plan_actions(csv_rows, args.rules_dir, xls_rules, create_priorities)
    print(format_plan(plans, verbose=args.verbose))
    print("")

    if args.write:
        apply_actions(plans, args.rules_dir, xls_rules)
        n_changed = sum(1 for p in plans if p["action"] in ("patch", "create"))
        print(f"✓ 已写盘 {n_changed} 个文件")
    else:
        print("(dry-run; 加 --write 实际执行)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
