# -*- coding: utf-8 -*-
"""harden-onsite-redlines Task 4.2: fees 患者匹配 新旧语义 diff.

旧: `patient_id in bah` 子串; 新: 全键精确 或 复合键末段精确 (strip).
对给定 fees CSV, 以「全部键末段 + 全键」为患者号 universe, 逐患者对比两种
语义的命中行数, 打印所有差异 (预期差异都是子串误归属的修正).

用法:
    uv run python scripts/diff_fee_match.py                    # config fees_path
    uv run python scripts/diff_fee_match.py --fees /path/x.csv [--overlay dir]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fees", default=None, help="fees CSV 路径 (默认 config fees_path)")
    ap.add_argument("--overlay", default=None, help="可选 overlay 目录 (叠加 shi_fee.csv)")
    args = ap.parse_args()

    if args.fees:
        fees_path = Path(args.fees)
    else:
        from javert.config import get_config
        fees_path = get_config().fees_path
    if not fees_path.exists():
        print(f"✗ fees 文件不存在: {fees_path}")
        return 2

    df = pd.read_csv(fees_path, dtype={"bah": str}, low_memory=False)
    if args.overlay:
        p = Path(args.overlay) / "shi_fee.csv"
        if p.exists():
            df = pd.concat([df, pd.read_csv(p, dtype={"bah": str}, low_memory=False)],
                           ignore_index=True)
    col = df["bah"] if "bah" in df.columns else df.iloc[:, 0]
    key = col.astype(str)
    sizes = key.groupby(key).size()  # 键 → 行数

    # 患者号 universe: 全键(strip) + 复合键末段(strip)
    universe: set[str] = set()
    for k in sizes.index:
        ks = k.strip()
        universe.add(ks)
        universe.add(ks.rsplit("-", 1)[-1].strip())
    universe.discard("")

    # 新语义索引: pid → 行数
    new_counts: dict[str, int] = {}
    for k, n in sizes.items():
        ks = k.strip()
        for cand in {ks, ks.rsplit("-", 1)[-1].strip()}:
            new_counts[cand] = new_counts.get(cand, 0) + int(n)

    n_diff = 0
    keys_list = list(sizes.items())
    for pid in sorted(universe):
        old_n = sum(int(n) for k, n in keys_list if pid in k)
        new_n = new_counts.get(pid, 0)
        if old_n != new_n:
            n_diff += 1
            absorbed = [k for k, _ in keys_list
                        if pid in k and k.strip() != pid
                        and k.strip().rsplit("-", 1)[-1].strip() != pid]
            print(f"DIFF pid={pid!r}: old={old_n} new={new_n} "
                  f"误归属键 (旧语义吃进来的): {absorbed[:5]}")

    print(f"\n患者号 universe: {len(universe)}, 键数: {len(sizes)}, 总行: {len(df)}")
    if n_diff == 0:
        print("✓ 新旧命中集合完全一致 (无子串误归属, 收紧零行为变化)")
    else:
        print(f"⚠ {n_diff} 个患者号命中集合有差异 — 请逐条核对以上 DIFF 行 "
              "(预期均为长号误归属短号的修正)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
