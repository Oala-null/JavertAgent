#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显示 /onboarding「载入数据」生成的 data_import 现状 — 文件/行数/可审核患者.

jv-status 调本脚本; jv-run-all 用 --ids 取可审核患者列表 (费用∩文书 的 canonical 裸号).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JAVERT_DATA_DIR", "data_import"))
if not DATA_DIR.is_absolute():
    DATA_DIR = ROOT / DATA_DIR

# (中文名, 文件, 患者键列, 是否复合键)
SPECS = [
    ("费用", "shi_fee.csv", "bah", True),
    ("文书", "case_notes.csv", "住院号", False),
    ("诊断", "shi_zd.csv", "ba_id", True),
    ("手术", "shi_ss.csv", "ba_id", True),
]


def _bare_ids(path: Path, col: str, compound: bool) -> set[str] | None:
    if not path.exists():
        return None
    try:
        s = pd.read_csv(path, usecols=[col], dtype=str)[col].dropna()
    except Exception:  # noqa: BLE001
        return set()
    if compound:
        return {str(v).split("-")[-1].strip() for v in s if str(v).strip()}
    return {str(v).strip() for v in s if str(v).strip()}


def _row_count(path: Path, col: str) -> int:
    try:
        return len(pd.read_csv(path, usecols=[col], dtype=str))
    except Exception:  # noqa: BLE001
        return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", action="store_true", help="只打印可审核患者裸号 (每行一个)")
    args = ap.parse_args()

    sets: dict[str, set[str] | None] = {}
    info: dict[str, tuple[str, int, int]] = {}
    for name, fn, col, comp in SPECS:
        p = DATA_DIR / fn
        ids = _bare_ids(p, col, comp)
        sets[name] = ids
        if ids is not None:
            info[name] = (fn, _row_count(p, col), len(ids))

    fee = sets.get("费用") or set()
    notes = sets.get("文书") or set()
    auditable = sorted(fee & notes) if (fee and notes) else sorted(fee or notes or set())

    if args.ids:
        print("\n".join(auditable))
        return

    if not info:
        print(f"⚠ {DATA_DIR} 为空 — 先在 /onboarding 映射后点「载入数据」")
        return

    env_p = DATA_DIR / ".loaded.env"
    if env_p.exists():
        import datetime
        ts = datetime.datetime.fromtimestamp(env_p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"📂 已载入数据 ({DATA_DIR})  ·  写入时间 {ts}:")
    else:
        print(f"📂 已载入数据 ({DATA_DIR}):")
    for name, (fn, nrows, npat) in info.items():
        print(f"   {name}  {fn:16} {nrows:>9,} 行  {npat:>6} 患者")
    print(f"\n✅ 可审核患者 (费用∩文书): {len(auditable)}")
    if auditable:
        print("   " + ", ".join(auditable[:15]) + (" …" if len(auditable) > 15 else ""))
        print(f"\n开跑单个: jv-run {auditable[0]}    |    全部: jv-run-all (或后台 jv-run-bg + jv-watch)")


if __name__ == "__main__":
    main()
