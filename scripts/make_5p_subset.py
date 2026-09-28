#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_hub_filled → data_hub_filled_5p: 只留 5 个测试病人的交付子集.

用途: TP 库瘦身 / 243(医院机房) 部署只带测试数据.
过滤键优先级: JZLSH > SYXH > KH > LSH; 字典表(TB_DIC_*)整表保留.
用法: uv run python scripts/make_5p_subset.py [--patients a,b,...]
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize)
SCRIV = Path("/Users/shane/26er/Scriv")
SRC = SCRIV / "data_hub_filled"
DST = SCRIV / "data_hub_filled_5p"
DEFAULT_PATIENTS = ["J66252", "K03341", "J13365", "211530148", "211345984"]
KEY_PRIORITY = ["JZLSH", "SYXH", "KH", "LSH"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patients", default=",".join(DEFAULT_PATIENTS))
    args = ap.parse_args()
    pats = {p.strip().upper() for p in args.patients.split(",") if p.strip()}

    DST.mkdir(exist_ok=True)
    shutil.copy(SRC / "_ext_tables.sql", DST / "_ext_tables.sql")
    if (SRC / "_dictionaries").exists():
        shutil.copytree(SRC / "_dictionaries", DST / "_dictionaries", dirs_exist_ok=True)

    # LIS_INDICATORS 无患者键 (经 BGDH 挂报告头) — 先收集过滤后报告头的 BGDH
    keep_bgdh: set[str] = set()
    with open(SRC / "TB_LIS_REPORT.csv", encoding="utf-8-sig", newline="") as fin:
        rd = csv.DictReader(fin)
        for row in rd:
            if row["JZLSH"].strip().upper() in pats:
                keep_bgdh.add(row["BGDH"].strip().upper())

    for f in sorted(SRC.glob("TB_*.csv")):
        with open(f, encoding="utf-8-sig", newline="") as fin:
            rd = csv.reader(fin)
            header = next(rd)
            if f.stem == "TB_LIS_INDICATORS":  # 特例: 按 BGDH 挂报告头过滤
                key_idx, key_set = header.index("BGDH"), keep_bgdh
            else:
                key_idx = next((header.index(k) for k in KEY_PRIORITY if k in header), None)
                key_set = pats
            keep_all = f.stem.startswith("TB_DIC_") or key_idx is None
            with open(DST / f.name, "w", encoding="utf-8-sig", newline="") as fout:
                w = csv.writer(fout)
                w.writerow(header)
                n = 0
                for row in rd:
                    if keep_all or row[key_idx].strip().upper() in key_set:
                        w.writerow(row)
                        n += 1
        tag = "整表(字典/无患者键)" if keep_all else f"按 {header[key_idx]} 过滤"
        print(f"{f.stem}: {n} 行 ({tag})")


if __name__ == "__main__":
    main()
