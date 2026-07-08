# -*- coding: utf-8 -*-
"""init_m1_precheck — 从存量 M1 规则 prompt_addon 抽取 A/B 项目集 → 写 precheck 块.

pilot-deterministic-precheck 迁移 (设计 D4): 不重渲, 解析既有规整
`A 类 (...): "x" / "y"` / `B 类 (...): "…"` 行 → `Rule.precheck.{a_items,b_items}`。

保守: A、B 各须抽到 ≥1 项且 prompt_addon 无 bespoke 分歧标记 (STEP/触发器/
search_examinations/跨日期比对) 才写; 否则跳过 → 该规则保留原 LLM 路径。
ruamel round-trip 保注释/字段顺序, precheck 追加到末尾。

用法:
    uv run python scripts/init_m1_precheck.py            # dry-run, 只打印命中/跳过清单
    uv run python scripts/init_m1_precheck.py --write     # 实写 precheck 块
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "configs" / "rules"

_A_LINE = re.compile(r"A\s*类\s*\([^)]*\)\s*[:：]\s*(.+)")
_B_LINE = re.compile(r"B\s*类\s*\([^)]*\)\s*[:：]\s*(.+)")
_QUOTED = re.compile(r'["“]([^"”]+)["”]')

# bespoke 分歧标记: 命中任一 → 该规则已被人工改写为非纯 M1 形态, 不做预检.
_DIVERGENCE = (
    "search_examinations", "触发器", "STEP", "checkDate",
    "不同日期", "不同时相", "checkDate", "跨 ", "跨天",
)


def _items_from(line_re: re.Pattern, addon: str) -> list[str]:
    m = line_re.search(addon)
    if not m:
        return []
    return [x.strip() for x in _QUOTED.findall(m.group(1)) if x.strip()]


def extract_precheck(addon: str) -> tuple[list[str], list[str], str]:
    """→ (a_items, b_items, skip_reason). skip_reason 非空表示不写."""
    if any(mk in addon for mk in _DIVERGENCE):
        return [], [], "bespoke prompt (含分歧标记, 非纯 M1)"
    a = _items_from(_A_LINE, addon)
    b = _items_from(_B_LINE, addon)
    if not a or not b:
        return a, b, f"抽取不全 (A={len(a)}, B={len(b)})"
    return a, b, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="实写 (默认 dry-run)")
    args = ap.parse_args()

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)

    wrote: list[str] = []
    skipped: list[tuple[str, str]] = []

    for path in sorted(RULES_DIR.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            data = yaml.load(f)
        if data is None or data.get("derived_from_template") != "M1":
            continue
        if data.get("status") != "ready":
            continue
        rid = data.get("rule_id", path.stem)
        addon = str(data.get("prompt_addon") or "")
        a, b, reason = extract_precheck(addon)
        if reason:
            skipped.append((rid, reason))
            continue
        wrote.append(f"{rid}: A={a} | B={b}")
        if args.write:
            data["precheck"] = {"a_items": list(a), "b_items": list(b)}
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f)

    mode = "WROTE" if args.write else "DRY-RUN (加 --write 实写)"
    print(f"=== init_m1_precheck [{mode}] ===\n")
    print(f"获得 precheck ({len(wrote)}):")
    for w in wrote:
        print(f"  ✓ {w}")
    print(f"\n跳过 ({len(skipped)}):")
    for rid, reason in skipped:
        print(f"  ✗ {rid} — {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
