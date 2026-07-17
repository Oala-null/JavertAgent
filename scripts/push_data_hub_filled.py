#!/usr/bin/env python3
"""把 data_hub_filled/*.csv 推到显式 --database（仅允许自有库；host/凭据走 .env）。

用法:
    uv run python scripts/push_data_hub_filled.py --database TP_data_hub
    uv run python scripts/push_data_hub_filled.py --database TP_data_hub --only TB_HIS_ZY_FEE_DETAIL_FS
    uv run python scripts/push_data_hub_filled.py --database TP_data_hub --recreate

标准表 DDL 由 data_hub_schema.json 生成 (与 Data_Hub 原 DDL 同构, 含 PK);
扩展表走 data_hub_filled/_ext_tables.sql。'' → NULL 仅对非 varchar 列生效。
默认自有库白名单为 TP_data_hub；142 sh_yb_platform 是 DE 维护的只读源，不得写入。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import pyodbc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from javert.config import load_config  # noqa: E402

SCRIV = Path("/Users/shane/26er/Scriv")
DATA = SCRIV / "data_hub_filled"
SCHEMA = json.loads((SCRIV / "data_hub_schema.json").read_text(encoding="utf-8"))

# 凭证不入源码 (v3): 复用 config sql_* (.env 提供 JAVERT_SQL_PASSWORD 等)
_cfg = load_config()
if not _cfg.sql_password:
    sys.exit("JAVERT_SQL_PASSWORD 未配置 — source .env 后重试")
CS = (f"DRIVER={{{_cfg.sql_driver}}};SERVER={_cfg.sql_host},{_cfg.sql_port};DATABASE=%s;"
      f"UID={_cfg.sql_user};PWD={_cfg.sql_password};TrustServerCertificate=yes;Encrypt=no;LoginTimeout=60")

# 扩展表的非 varchar 列 (与 _ext_tables.sql 保持一致)
EXT_TYPED = {
    "TB_CIS_MEDICAL_DOCUMENT": {"DLXH": "int", "JLSJ": "datetime"},
    "TB_HIS_ZY_FEE_DETAIL_EXT": {c: "decimal" for c in
        ["PRIC_UPLMT_AMT", "SELFPAY_PROP", "FULAMT_OWNPAY_AMT", "OVERLMT_AMT",
         "PRESELFPAY_AMT", "INSCP_SCP_AMT"]},
    "TB_BA_SYSSK_EXT": {c: "datetime" for c in ["SSKSSJ", "SSJSSJ", "MZKSSJ", "MZJSSJ"]},
}
CHUNK = 50_000
CHUNK_BIGTEXT = 10_000  # 含 nvarchar(max) 正文的表


def ddl_from_schema(table: str) -> str:
    meta = SCHEMA[table]
    lines = []
    for c in meta["columns"]:
        null = "NULL" if c["nullable"] else "NOT NULL"
        lines.append(f"  [{c['name']}] {c['type']} {null}")
    if meta["pk"]:
        pk = ",".join(f"[{c}]" for c in meta["pk"])
        lines.append(f"  CONSTRAINT [PK_{table}] PRIMARY KEY CLUSTERED ({pk})")
    return f"CREATE TABLE [dbo].[{table}] (\n" + ",\n".join(lines) + "\n)"


def nonvarchar_cols(table: str) -> set[str]:
    if table in EXT_TYPED:
        return set(EXT_TYPED[table])
    return {c["name"] for c in SCHEMA[table]["columns"]
            if not c["type"].startswith(("varchar", "nvarchar", "char", "text"))}


def has_bigtext(table: str) -> bool:
    return table == "TB_CIS_MEDICAL_DOCUMENT"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", required=True, help="写入目标库；142 自有库使用 TP_data_hub")
    ap.add_argument("--only")
    ap.add_argument("--recreate", action="store_true")
    ap.add_argument("--data-dir", help="改用其他数据目录 (如 data_hub_filled_5p 测试子集)")
    args = ap.parse_args()
    data = Path(args.data_dir) if args.data_dir else DATA
    target_db = args.database.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]+", target_db):
        sys.exit(f"拒绝写入: 非法数据库名 {target_db!r}")

    # 保险栓: 建库、建表、增量写和 --recreate 都只允许对自有库执行。
    # 防止默认读取库 sh_yb_platform 被误灌；243 同名产品库需显式加入白名单。
    owned = {d.strip() for d in
             (os.environ.get("JAVERT_OWNED_DBS") or "TP_data_hub").split(",") if d.strip()}
    if target_db not in owned:
        sys.exit(f"拒绝写入: 目标库 {target_db!r} 不在自有库白名单 {sorted(owned)} "
                 f"(误灌防线; 自有环境需显式设置 JAVERT_OWNED_DBS)")

    master = pyodbc.connect(CS % "master", timeout=60, autocommit=True)
    mc = master.cursor()
    if not mc.execute("SELECT 1 FROM sys.databases WHERE name=?", target_db).fetchone():
        mc.execute(f"CREATE DATABASE [{target_db}] COLLATE Chinese_PRC_CI_AS")
        print(f"已建库 {target_db} (Chinese_PRC_CI_AS)")
    master.close()

    cn = pyodbc.connect(CS % target_db, timeout=60, autocommit=False)
    cur = cn.cursor()
    cur.fast_executemany = True

    csvs = sorted(data.glob("TB_*.csv"), key=lambda p: p.stat().st_size)  # 小表先行, 快速反馈
    if args.only:
        csvs = [p for p in csvs if p.stem == args.only]

    ext_sql = (data / "_ext_tables.sql").read_text(encoding="utf-8")

    summary = []
    for path in csvs:
        table = path.stem
        t0 = time.time()
        exists = cur.execute("SELECT 1 FROM sys.tables WHERE name=?", table).fetchone()
        if exists and args.recreate:
            cur.execute(f"DROP TABLE [dbo].[{table}]")
            cn.commit()
            exists = None
        if not exists:
            if table in EXT_TYPED:  # 扩展表: 从 _ext_tables.sql 取对应块
                block = re.search(rf"CREATE TABLE \[dbo\]\.\[{table}\].*?\);", ext_sql, re.S)
                cur.execute(block.group(0).rstrip(";"))
            else:
                cur.execute(ddl_from_schema(table))
            cn.commit()

        expected = None  # 惰性: 只有需要跳过判断时才数
        db_n = cur.execute(f"SELECT COUNT(*) FROM [dbo].[{table}]").fetchone()[0]
        if db_n > 0 and not args.recreate:
            print(f"⏭  {table}: 库中已有 {db_n} 行, 跳过 (要重灌用 --recreate)", flush=True)
            summary.append((table, db_n, "skip"))
            continue

        nv = nonvarchar_cols(table)
        chunk = CHUNK_BIGTEXT if has_bigtext(table) else CHUNK
        total = 0
        for df in pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=chunk,
                              encoding="utf-8-sig"):
            cols = list(df.columns)
            # pandas3 str-dtype 的 where(None)/NaN 经 tolist 变 float NaN, pyodbc 按 float
            # 绑给 datetime/decimal 列必炸 — 显式转 Python None (nv 列空串也 → None)
            data_cols = []
            for c in cols:
                to_null = c in nv
                data_cols.append([
                    None if (v is None or (isinstance(v, float) and v != v)
                             or (to_null and v == "")) else v
                    for v in df[c]
                ])
            rows = list(map(list, zip(*data_cols)))
            placeholders = ",".join("?" * len(cols))
            collist = ",".join(f"[{c}]" for c in cols)
            sql = f"INSERT INTO [dbo].[{table}] ({collist}) VALUES ({placeholders})"
            # 整列全 NULL 时 fast_executemany 推不出绑定类型 (07006) → 显式绑 WVARCHAR
            sizes = [(pyodbc.SQL_WVARCHAR, 4000, 0) if all(v is None for v in col) else None
                     for col in data_cols]
            cur.setinputsizes(sizes if any(sz is not None for sz in sizes) else None)
            try:
                cur.executemany(sql, rows)
            except pyodbc.Error as e:
                if "07006" not in str(e):
                    raise
                cn.rollback()
                cur.fast_executemany = False  # 兜底: 该块降速逐行绑定
                cur.setinputsizes(None)
                cur.executemany(sql, rows)
                cur.fast_executemany = True
            cn.commit()
            total += len(df)
            print(f"   {table}: {total} 行 ...", flush=True)
        db_n = cur.execute(f"SELECT COUNT(*) FROM [dbo].[{table}]").fetchone()[0]
        ok = "✓" if db_n == total else f"✗ 库中 {db_n} != CSV {total}"
        print(f"{ok} {table}: {total} 行, {time.time()-t0:.0f}s", flush=True)
        summary.append((table, db_n, ok))

    print("\n==== 汇总 ====")
    for t, n, s in summary:
        print(f"  {t:38s} {n:>9,d} {s}")
    cn.close()


if __name__ == "__main__":
    sys.exit(main())
