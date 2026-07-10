#!/usr/bin/env python3
"""反向取数桥: sh_yb_platform (TB_* 国标表) → Javert 内部 6 文件 (流 B).

用法:
    uv run python scripts/etl_from_data_hub.py --patients 211530148,J66252 --output data_import_hub
    uv run python scripts/etl_from_data_hub.py --all --output data_import_hub

产出列契约 = configs/schema_manifest.yaml 各 spoke 的 output_schema (v0.7 外部数据同一契约),
跑审计:
    export JAVERT_DATA_DIR=data_import_hub JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv \\
           JAVERT_LABS_FILE=lab_results.csv JAVERT_EXAMINATIONS_FILE=examinations.csv \\
           JAVERT_SQL_ENABLED=false
    uv run javert audit-patient 211530148 --use-router --concurrency 5

映射逻辑在 src/javert/data/hub_source.py (add-workbench-sql-raw-source D2 提取,
与工作台 HubRawSource 共用) — 本脚本只留 CLI + 落盘.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from javert.config import get_config
from javert.data import hub_source as hs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patients", help="逗号分隔 JZLSH 列表")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--output", default="data_import_hub")
    args = ap.parse_args()
    if not args.all and not args.patients:
        ap.error("--patients 或 --all 二选一")
    pids = None if args.all else [p.strip() for p in args.patients.split(",")]
    out = Path(args.output)
    out.mkdir(exist_ok=True)

    cn = hs.connect(get_config())
    yq2org = hs.fetch_hospital_map(cn)

    shi_fee = hs.fetch_fees(cn, pids, yq2org)
    shi_fee.to_csv(out / "shi_fee.csv", index=False, encoding="utf-8-sig")

    notes = hs.fetch_notes(cn, pids)
    notes.to_csv(out / "case_notes.csv", index=False, encoding="utf-8-sig")

    shi_zd = hs.fetch_zd(cn, pids, yq2org)
    shi_zd.to_csv(out / "shi_zd.csv", index=False, encoding="utf-8-sig")

    shi_ss = hs.fetch_ss(cn, pids, yq2org)
    shi_ss.to_csv(out / "shi_ss.csv", index=False, encoding="utf-8-sig")

    labs = hs.fetch_labs(cn, pids)
    labs.to_csv(out / "lab_results.csv", index=False, encoding="utf-8-sig")

    exams = hs.fetch_exams(cn, pids)
    exams.to_csv(out / "examinations.csv", index=False, encoding="utf-8-sig")

    print(f"→ {out}/")
    for name, df, pidcol in [("shi_fee", shi_fee, "bah"), ("case_notes", notes, "住院号"),
                             ("shi_zd", shi_zd, "ba_id"), ("shi_ss", shi_ss, "ba_id"),
                             ("lab_results", labs, "zyh"), ("examinations", exams, "zyh")]:
        print(f"  {name:14s} {len(df):>8,d} 行  {df[pidcol].nunique()} 患者")


if __name__ == "__main__":
    sys.exit(main())
