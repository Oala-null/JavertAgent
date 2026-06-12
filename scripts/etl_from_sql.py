#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aidb 6 表 → Javert data_import 快照桥 (现场对接兜底).

主路径是 /onboarding 拖 CSV; 本桥是兜底: 当 CSV 接入不顺时, 贵院工程师把数据
**按 docs/schema 6 张 Excel 模板 1:1 填进 142 aidb 库的 6 张 intake_* 表**
(建表脚本 scripts/sql/create_aidb_tables.sql), 本桥从 aidb 一次性取数 → 复用现有
ETL (run_etl) 转成内部 CSV → 落 data_import/, 之后正常跑 audit-patient.

设计: 快照 (snapshot), 非实时直读 —— 跑批与 142 解耦最稳. 零硬编码列映射, 复用
onboarding 的 match_fields 自动认表 (与 GUI 同一套) + transform_spoke 转换 +
join_preflight 连接预检 (🟢🟡🔴). 现有 loader/工具/runner/工作台一行不改.

用法 (在能连 142 的机器上, 如部署机 62):
    # 预览 + 连接预检 (不写盘)
    uv run python scripts/etl_from_sql.py --hospital-code 协和 --dry-run
    # 写入 data_import/
    uv run python scripts/etl_from_sql.py --hospital-code 协和 --output data_import

之后跑审计 (结果实时入 142 工作台, 用批次标签区分新旧数据):
    export JAVERT_DATA_DIR=data_import JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv
    export JAVERT_SQL_ENABLED=true JAVERT_BATCH_TAG=协和
    uv run javert audit-patient <患者ID> --priority all --use-router --concurrency 5
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

import pandas as pd
import yaml

from javert.onboarding.classifier import match_fields
from javert.onboarding.etl_engine import run_etl
from javert.onboarding.join_preflight import preflight_keys
from javert.onboarding.manifest_loader import Manifest, load_manifest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("etl_from_sql")

# manifest tabular spoke key → aidb 表名 (create_aidb_tables.sql 里的 6 张表)
AIDB_TABLES: dict[str, str] = {
    "fees": "intake_fees",
    "notes": "intake_notes",
    "diagnoses": "intake_diagnoses",
    "surgeries": "intake_surgeries",
    "labs": "intake_labs",
    "examinations": "intake_examinations",
}


def load_alias() -> dict:
    """configs/field_alias.yaml (国标↔语义别名, match_fields 自动认表依据)."""
    p = PROJECT_ROOT / "configs" / "field_alias.yaml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def run_bridge(
    tables: dict[str, pd.DataFrame],
    hospital_code: str,
    output_dir: str | Path,
    manifest: Manifest,
    alias: dict,
    dry_run: bool = False,
):
    """6 表 DataFrame (友好中文列) → data_import/*.csv. 可单测 seam (不碰 SQL).

    复用 run_etl (转换 + SpokeResult 构建 + 校验) + preflight_keys (连接预检).
    每表落临时 CSV 喂 run_etl (read_source 走文件), 自动清理.

    Returns:
        ETLResult (含 .ok / .spokes / .fatal_errors); 全空表时返回 None.
    """
    with tempfile.TemporaryDirectory(prefix="javert_aidb_") as tmp:
        mapping: dict = {"hospital_code": hospital_code}
        for key, spoke in manifest.tabular_spokes().items():
            df = tables.get(key)
            if df is None or len(df) == 0:
                continue  # 可选表 (labs/exam) 空则跳过, 同 ETL
            df = df.astype(str)
            csv_path = Path(tmp) / f"{key}.csv"
            df.to_csv(csv_path, index=False, encoding="utf-8")
            # 自动认表: 友好中文列名 → 语义键 (与 /onboarding GUI 同一套)
            col_map = match_fields(spoke, list(df.columns), alias)
            mapping[key] = {"file": str(csv_path), "key_mode": "synth", "columns": col_map}

        if len(mapping) <= 1:
            log.error("aidb 6 张表全空, 无数据可转换 (至少需要 费用 + 文书)")
            return None

        result = run_etl(mapping, manifest)
        if not result.ok:
            log.error("ETL 校验失败:")
            for e in result.fatal_errors:
                log.error("  ✗ %s", e)
            return result

        out_path = Path(output_dir)
        for sr in result.spokes:
            log.info("─── %s (%s → %s): %d 行, %d 患者",
                     sr.name, sr.key, sr.output_file, len(sr.df), sr.patient_count)
            for w in sr.warnings:
                log.warning(w)
            if not dry_run and sr.output_file:
                out_path.mkdir(parents=True, exist_ok=True)
                dst = out_path / sr.output_file
                sr.df.to_csv(dst, index=False, encoding="utf-8")
                log.info("  ✓ 写入 %s", dst)

        _log_preflight(result, manifest)
        if dry_run:
            log.info("")
            log.info("(dry-run 模式, 未写盘)")
        return result


def _log_preflight(result, manifest: Manifest) -> None:
    """连接预检 🟢🟡🔴 (复用 join_preflight, 与 etl_import/GUI 同源)."""
    spoke_meta = {sr.key: manifest.spoke(sr.key) for sr in result.spokes}
    pf = preflight_keys(result.spokes, spoke_meta)
    if pf is None:
        return
    log.info("")
    log.info("─── 连接预检 ───")
    log.info("  %s 键交集覆盖率: %.0f%% (%d/%d, 参照 %s) → %s",
             pf.emoji, pf.coverage * 100, pf.hit, pf.total, pf.primary, pf.label)
    for sk, rate in pf.per_spoke.items():
        log.info("    · %s: %.0f%%", sk, rate * 100)
    if pf.verdict == "red":
        log.warning("  🔴 键几乎不交 — 大概率住院号各表不一致, 或列映射错; 核对后再审计")


def _print_next_steps(out_path: Path, hospital_code: str) -> None:
    rel = out_path.relative_to(PROJECT_ROOT) if out_path.is_relative_to(PROJECT_ROOT) else out_path
    log.info("")
    log.info("✓ 转换完成, 文件在 %s/", out_path)
    log.info("")
    log.info("下一步 — 用新数据跑审计 (结果实时入 142 工作台, 打批次标签便于区分新旧数据):")
    log.info("  export JAVERT_DATA_DIR=%s JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv", rel)
    log.info("  export JAVERT_SQL_ENABLED=true JAVERT_BATCH_TAG=%s", hospital_code)
    log.info("  uv run javert audit-patient <患者ID> --priority all --use-router --concurrency 5")


def _read_aidb_tables(manifest: Manifest) -> dict[str, pd.DataFrame]:
    """连 142 aidb 库, SELECT * 各 intake_* 表 → {spoke_key: DataFrame}. 缺表/读失败跳过."""
    from sqlalchemy import text

    from javert.config import get_config
    from javert.store.sqlserver_store import SqlServerStore

    cfg = get_config()
    # 桥读源库 aidb (复用结果库的连接逻辑, 只换 database)
    src_cfg = cfg.model_copy(update={"sql_database": cfg.sql_source_database})
    engine = SqlServerStore(src_cfg).get_engine()
    if engine is None:
        log.error("无法连接源库 [%s] @ %s (检查 pyodbc 是否装 / 网络 / JAVERT_SQL_ENABLED).",
                  cfg.sql_source_database, cfg.sql_host)
        log.error("桥需在能连 142 的机器上跑 (如部署机 62).")
        sys.exit(1)

    tables: dict[str, pd.DataFrame] = {}
    for key in manifest.tabular_spokes():
        tname = AIDB_TABLES[key]
        try:
            with engine.connect() as conn:
                df = pd.read_sql(text(f"SELECT * FROM {tname}"), conn)
        except Exception as e:  # noqa: BLE001  缺表/无权限 → 跳过 (可选表本就可缺)
            log.warning("读取 aidb.%s 失败 (表不存在或无权限?): %s — 跳过", tname, e)
            continue
        # 去掉建表自增 id 列 (非友好 schema 字段; match_fields 不认, 删掉更干净)
        if "id" in df.columns:
            df = df.drop(columns=["id"])
        log.info("aidb.%s: %d 行", tname, len(df))
        tables[key] = df.astype(str)
    return tables


def main() -> int:
    parser = argparse.ArgumentParser(description="aidb 6 表 → Javert data_import 快照桥")
    parser.add_argument("--hospital-code", required=True,
                        help="合成复合患者键用的医院/批次代码 (每批固定, 别中途换; 也建议拿来当 JAVERT_BATCH_TAG)")
    parser.add_argument("--output", default="data_import", help="输出目录 (默认 data_import)")
    parser.add_argument("--dry-run", action="store_true", help="只预览行数/患者数 + 连接预检, 不写盘")
    args = parser.parse_args()

    manifest = load_manifest()
    alias = load_alias()
    tables = _read_aidb_tables(manifest)

    result = run_bridge(tables, args.hospital_code, args.output, manifest, alias, dry_run=args.dry_run)
    if result is None or not result.ok:
        return 1
    if not args.dry_run:
        _print_next_steps(Path(args.output), args.hospital_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
