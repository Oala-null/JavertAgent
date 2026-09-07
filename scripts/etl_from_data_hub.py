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
import json
import os
import shutil
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from javert.config import get_config
from javert.data import hub_source as hs


def hospital_etl(args, cfg):
    pids = [p.strip() for p in (args.patients or "").split(",") if p.strip()]
    if args.all or len(pids) != 1:
        raise hs.HospitalLinkageError("REAL_MODE_REQUIRES_ONE_SYXH")
    print("[1/2] 关联与取数预检（不调用LLM、不写数据库）", flush=True)
    with closing(hs.connect(cfg, timeout=15)) as cn:
        cn.timeout = 60
        bundle = hs.fetch_hospital_bundle(cn, pids[0], cfg.hub_hospital_code)
    files = {"fees": "shi_fee.csv", "notes": "case_notes.csv", "zd": "shi_zd.csv",
             "ss": "shi_ss.csv", "labs": "lab_results.csv", "exams": "examinations.csv"}
    report = {"mode": "shanghai", "rows": {name: len(bundle[name]) for name in files},
              "warnings": bundle["warnings"]}
    for name, count in report["rows"].items():
        print(f"  {name}: {count} 行")
    for code, count in report["warnings"].items():
        if count:
            print(f"  WARNING {code}={count}")
    if args.check_only:
        print("[2/2] 预检完成，未写快照、未启动审计")
        return
    out = Path(args.output).absolute()
    if out.exists():
        raise hs.HospitalLinkageError("OUTPUT_ALREADY_EXISTS")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".patient-stage-", dir=out.parent))
    try:
        for name, filename in files.items():
            path = stage / filename
            bundle[name].to_csv(path, index=False, encoding="utf-8-sig")
            path.chmod(0o600)
        report_path = stage / "preflight.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.chmod(0o600)
        os.rename(stage, out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)  # 仅删除本函数mkdtemp创建的暂存目录。
    print(f"[2/2] 快照就绪：{out}；未启动审计")


def main() -> None:
    ap = argparse.ArgumentParser()
    select = ap.add_mutually_exclusive_group(required=True)
    select.add_argument("--patients", help="legacy: JZLSH列表；shanghai: 单个首页SYXH")
    select.add_argument("--all", action="store_true")
    ap.add_argument("--check-only", action="store_true", help="shanghai单例只读预检，不写CSV")
    ap.add_argument("--output", default="data_import_hub")
    args = ap.parse_args()
    cfg = get_config()
    if cfg.hub_linkage_mode == "shanghai":
        return hospital_etl(args, cfg)
    if args.check_only:
        ap.error("--check-only 需要 JAVERT_HUB_LINKAGE_MODE=shanghai")
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
    try:
        sys.exit(main())
    except hs.HospitalLinkageError as exc:
        print(f"预检未通过：{exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"取数失败：{type(exc).__name__}（未输出SQL或凭据）", file=sys.stderr)
        sys.exit(3)
