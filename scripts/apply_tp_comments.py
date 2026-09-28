#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TP_data_hub 补列注释 (MS_Description 扩展属性), 幂等.

来源: 国标表 ← Scriv/Data_Hub/TB_*.md 里的 sp_addextendedproperty 原文;
      扩展表 ← Scriv/data_hub_filled/_ext_tables.sql 的行内 `--` 注释.
用法: uv run python scripts/apply_tp_comments.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import pyodbc  # noqa: E402

from javert.config import load_config  # noqa: E402
from javert.data.hub_source import build_conn_str  # noqa: E402

SCRIV = Path("/Users/shane/26er/Scriv")

# 国标 md: 抓 (desc, table, column) 三元组
PROP_RE = re.compile(
    r"'MS_Description',\s*N'([^']*)'\s*,\s*\n?'SCHEMA',\s*N'dbo',\s*\n?"
    r"'TABLE',\s*N'(\w+)'\s*(?:,\s*\n?'COLUMN',\s*N'(\w+)')?", re.S)
# 扩展表 sql: 抓 [COL] type ... -- 注释
EXT_COL_RE = re.compile(r"^\s*\[(\w+)\][^,]+,\s*--\s*(.+?)\s*$", re.M)
EXT_TBL_RE = re.compile(r"CREATE TABLE \[dbo\]\.\[(\w+)\] \((.*?)\n\);", re.S)


def collect() -> list[tuple[str, str | None, str]]:
    props: list[tuple[str, str | None, str]] = []
    for md in sorted((SCRIV / "Data_Hub").glob("TB_*.md")):
        for m in PROP_RE.finditer(md.read_text(encoding="utf-8")):
            desc, tbl, col = m.group(1).strip(), m.group(2), m.group(3)
            if desc:
                props.append((tbl, col, desc))
    ext = (SCRIV / "data_hub_filled/_ext_tables.sql").read_text(encoding="utf-8")
    for tm in EXT_TBL_RE.finditer(ext):
        tbl, body = tm.group(1), tm.group(2)
        props.append((tbl, None, "扩展表 (国标 46 表之外自建), 见 Scriv/Data_Hub/扩展表_DE对接样例_v2.md"))
        for cm in EXT_COL_RE.finditer(body):
            props.append((tbl, cm.group(1), cm.group(2)))
    return props


def main() -> None:
    cn = pyodbc.connect(build_conn_str(load_config()), timeout=60, autocommit=True)
    cur = cn.cursor()
    existing_tables = {r[0] for r in cur.execute("SELECT name FROM sys.tables").fetchall()}
    ok = skip = 0
    for tbl, col, desc in collect():
        if tbl not in existing_tables:
            skip += 1
            continue
        lvl = ", 'COLUMN', ?" if col else ""
        params = [desc, tbl] + ([col] if col else [])
        try:  # 已存在 → update, 否则 add (幂等)
            cur.execute(f"EXEC sp_updateextendedproperty 'MS_Description', ?, 'SCHEMA', 'dbo', 'TABLE', ?{lvl}", params)
        except pyodbc.Error:
            try:
                cur.execute(f"EXEC sp_addextendedproperty 'MS_Description', ?, 'SCHEMA', 'dbo', 'TABLE', ?{lvl}", params)
            except pyodbc.Error:
                skip += 1  # 列不存在 (如 DDL 差异) — 跳过
                continue
        ok += 1
    print(f"注释写入 {ok} 条, 跳过 {skip} 条 (表/列不存在)")
    n = cur.execute("SELECT COUNT(*) FROM sys.extended_properties WHERE name='MS_Description'").fetchone()[0]
    print(f"库内 MS_Description 总数: {n}")


if __name__ == "__main__":
    main()
