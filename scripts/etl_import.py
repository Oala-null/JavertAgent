#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部医院数据 → Javert 格式转换.

用法:
    # 预览 (不写盘)
    uv run python scripts/etl_import.py --mapping configs/column_mapping.yaml --dry-run

    # 写入 data_import/ 目录
    uv run python scripts/etl_import.py --mapping configs/column_mapping.yaml

    # 写入自定义目录
    uv run python scripts/etl_import.py --mapping configs/column_mapping.yaml --output /tmp/javert_test

转换完成后, 用环境变量指向新数据:
    export JAVERT_DATA_DIR=data_import
    export JAVERT_ZD_FILE=shi_zd.csv
    export JAVERT_SS_FILE=shi_ss.csv
    uv run javert audit-patient <患者ID> --use-router
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("etl_import")

# ── shi_fee.csv 完整列序 (兼容追加计价单位/医嘱关联) ──
FEE_COLUMNS = [
    "bah", "feedetl_sn", "fee_ocur_time", "cnt", "unit", "order_id", "pric",
    "det_item_fee_sumamt", "pric_uplmt_amt", "selfpay_prop",
    "fulamt_ownpay_amt", "overlmt_amt", "preselfpay_amt", "inscp_scp_amt",
    "chrgitm_lv", "list_type", "med_list_codg", "medins_list_codg",
    "medins_list_name", "med_chrgitm_type", "prodname", "spec",
    "dosform_name", "bilg_dept_codg", "bilg_dept_name", "bilg_dr_codg",
    "bilg_dr_name", "acord_dept_codg", "acord_dept_name", "orders_dr_code",
    "orders_dr_name", "dscg_tkdrug_flag", "fee_type", "medins_chrgitm_type",
    "hosp_appr_flag", "hospital", "create_time", "id",
]

NOTES_COLUMNS = ["住院号", "事件时间", "阶段", "子阶段", "内容", "来源文件"]

ZD_COLUMNS = [
    "ba_id", "maindiag_flag", "inhosp_diag_name", "inhosp_diag_code",
    "diag_name", "diag_code", "ipt_medcas_hmpg_sn",
]

SS_COLUMNS = [
    "ba_id", "oprn_oprt_name", "oprn_oprt_code", "main_oprn_flag",
    "oprn_oprt_date", "oprn_lv_name", "anst_mtd_name",
    "oper_dr_name", "anst_dr_name",
]


def read_source(file_path: str) -> pd.DataFrame:
    p = PROJECT_ROOT / file_path
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, dtype=str, low_memory=False)
    else:
        return pd.read_excel(p, dtype=str)


def _col(df: pd.DataFrame, col_map: dict, key: str) -> pd.Series:
    """从 df 取映射列; 列不存在则返回空 Series."""
    ext_name = col_map.get(key, "")
    if ext_name and ext_name in df.columns:
        return df[ext_name].fillna("")
    return pd.Series([""] * len(df), dtype=str)


def _make_compound_id(pids: pd.Series, hospital_code: str) -> pd.Series:
    """patient_id → 'H...-patient_id ' (带尾部空格, 兼容 str.contains 匹配)."""
    return hospital_code + "-" + pids.str.strip() + " "


_SECTION_RE = re.compile(r"【([^】]+)】")


def _split_sections(text: str) -> list[tuple[str, str]]:
    """把 '【主诉】xxx【现病史】yyy' 拆成 [('主诉','xxx'), ('现病史','yyy')]."""
    markers = list(_SECTION_RE.finditer(text))
    if not markers:
        return []
    parts: list[tuple[str, str]] = []
    for i, m in enumerate(markers):
        name = m.group(1)
        start = m.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        content = text[start:end].strip()
        if content:
            parts.append((name, content))
    return parts


# ────────────── 转换函数 ──────────────

def transform_fees(src: pd.DataFrame, col_map: dict, hospital_code: str) -> pd.DataFrame:
    pids = _col(src, col_map, "patient_id")
    out = pd.DataFrame(columns=FEE_COLUMNS)
    out["bah"] = _make_compound_id(pids, hospital_code)
    out["medins_list_name"] = _col(src, col_map, "item_name")
    out["det_item_fee_sumamt"] = _col(src, col_map, "amount")
    out["fee_ocur_time"] = _col(src, col_map, "date")
    out["medins_chrgitm_type"] = _col(src, col_map, "category")
    out["cnt"] = _col(src, col_map, "quantity")
    out["unit"] = _col(src, col_map, "unit")
    out["order_id"] = _col(src, col_map, "order_id")
    out["pric"] = _col(src, col_map, "unit_price")
    out["spec"] = _col(src, col_map, "spec")
    out["medins_list_codg"] = _col(src, col_map, "item_code")
    out["acord_dept_name"] = _col(src, col_map, "dept_order")
    out["orders_dr_name"] = _col(src, col_map, "doctor_order")
    out["bilg_dept_name"] = _col(src, col_map, "dept_billing")
    out["bilg_dr_name"] = _col(src, col_map, "doctor_billing")
    out["prodname"] = _col(src, col_map, "product_name")
    out["hospital"] = hospital_code
    out = out.fillna("")
    return out


def transform_notes(src: pd.DataFrame, col_map: dict) -> pd.DataFrame:
    pid_col = col_map.get("patient_id", "")
    section_col = col_map.get("section", "")
    content_col = col_map.get("content", "")
    doc_name_col = col_map.get("doc_name", "")
    time_col = col_map.get("time", "")

    rows: list[dict] = []
    for _, row in src.iterrows():
        pid = str(row.get(pid_col, "") or "").strip() if pid_col else ""
        doc_name = str(row.get(doc_name_col, "") or "").strip() if doc_name_col and doc_name_col in src.columns else ""
        section = str(row.get(section_col, "") or "").strip() if section_col else ""
        content = str(row.get(content_col, "") or "").strip() if content_col else ""
        time_val = str(row.get(time_col, "") or "").strip() if time_col and time_col in src.columns else ""

        # 尝试按 【段落名】 拆分内容
        parts = _split_sections(content)
        if parts:
            for sub_section, sub_content in parts:
                rows.append({
                    "住院号": pid,
                    "事件时间": time_val,
                    "阶段": section or doc_name,
                    "子阶段": sub_section,
                    "内容": sub_content,
                    "来源文件": "etl_import",
                })
        else:
            rows.append({
                "住院号": pid,
                "事件时间": time_val,
                "阶段": doc_name,
                "子阶段": section,
                "内容": content,
                "来源文件": "etl_import",
            })
    return pd.DataFrame(rows, columns=NOTES_COLUMNS)


def transform_diagnoses(src: pd.DataFrame, col_map: dict, hospital_code: str) -> pd.DataFrame:
    pids = _col(src, col_map, "patient_id")
    out = pd.DataFrame(columns=ZD_COLUMNS)
    out["ba_id"] = _make_compound_id(pids, hospital_code)
    out["maindiag_flag"] = _col(src, col_map, "main_flag")
    out["inhosp_diag_name"] = _col(src, col_map, "diag_name")
    out["inhosp_diag_code"] = _col(src, col_map, "diag_code")
    out["diag_name"] = _col(src, col_map, "diag_name")
    out["diag_code"] = _col(src, col_map, "diag_code")
    out["ipt_medcas_hmpg_sn"] = range(1, len(src) + 1)
    out = out.fillna("")
    return out


def transform_surgeries(src: pd.DataFrame, col_map: dict, hospital_code: str) -> pd.DataFrame:
    pids = _col(src, col_map, "patient_id")
    out = pd.DataFrame(columns=SS_COLUMNS)
    out["ba_id"] = _make_compound_id(pids, hospital_code)
    out["oprn_oprt_name"] = _col(src, col_map, "surgery_name")
    out["oprn_oprt_code"] = _col(src, col_map, "surgery_code")
    out["main_oprn_flag"] = _col(src, col_map, "main_flag")
    out["oprn_oprt_date"] = _col(src, col_map, "surgery_date")
    out["oprn_lv_name"] = _col(src, col_map, "level")
    out["anst_mtd_name"] = _col(src, col_map, "anesthesia")
    out["oper_dr_name"] = _col(src, col_map, "surgeon")
    out["anst_dr_name"] = _col(src, col_map, "anesthesiologist")
    out = out.fillna("")
    return out


# ────────────── 校验 ──────────────

REQUIRED = {
    "fees": ["patient_id", "item_name", "amount", "date", "category"],
    "notes": ["patient_id", "section", "content"],
    "diagnoses": ["patient_id", "diag_name", "diag_code", "main_flag"],
    "surgeries": ["patient_id", "surgery_name", "main_flag"],
}


def validate_mapping(mapping: dict) -> list[str]:
    errors: list[str] = []
    for table, required_keys in REQUIRED.items():
        section = mapping.get(table)
        if not section:
            errors.append(f"缺少 {table} 配置节")
            continue
        if not section.get("file"):
            errors.append(f"{table}.file 未配置")
        col_map = section.get("columns", {})
        for key in required_keys:
            ext_name = col_map.get(key, "")
            if not ext_name:
                errors.append(f"{table}.columns.{key} 未配置 (必填)")
    return errors


def validate_columns(df: pd.DataFrame, col_map: dict, table_name: str) -> list[str]:
    warnings: list[str] = []
    for key, ext_name in col_map.items():
        if ext_name and ext_name not in df.columns:
            tag = "[必填]" if key in REQUIRED.get(table_name, []) else "[可选]"
            warnings.append(f"  {table_name}: 映射列 '{ext_name}' ({key}) 在文件中不存在 {tag}")
    return warnings


# ────────────── 主流程 ──────────────

def run(mapping_path: str, output_dir: str, dry_run: bool = False, legacy: bool = False):
    """默认 manifest 驱动 (etl_engine); --legacy 走旧硬编码 4 表路径 (fallback / 回归 oracle)."""
    if legacy:
        return run_legacy(mapping_path, output_dir, dry_run)
    return run_manifest(mapping_path, output_dir, dry_run)


def run_manifest(mapping_path: str, output_dir: str, dry_run: bool = False):
    """manifest 驱动: 遍历 schema_manifest tabular spoke 转换 (设计 D1)."""
    from javert.onboarding.etl_engine import run_etl
    from javert.onboarding.manifest_loader import load_manifest

    with open(mapping_path, encoding="utf-8") as f:
        mapping = yaml.safe_load(f)

    manifest = load_manifest()
    result = run_etl(mapping, manifest)
    if not result.ok:
        log.error("ETL 校验失败:")
        for e in result.fatal_errors:
            log.error("  ✗ %s", e)
        sys.exit(1)

    out_path = Path(output_dir)
    all_warnings: list[str] = []
    for sr in result.spokes:
        log.info("─── %s (%s → %s) ───", sr.name, sr.key, sr.output_file)
        for w in sr.warnings:
            log.warning(w)
            all_warnings.append(w)
        log.info("  转换完成: %d 行, %d 个患者", len(sr.df), sr.patient_count)
        if not dry_run and sr.output_file:
            out_path.mkdir(parents=True, exist_ok=True)
            dst = out_path / sr.output_file
            sr.df.to_csv(dst, index=False, encoding="utf-8")
            log.info("  ✓ 写入 %s", dst)

    # ── 连接预检 (GUI/CLI 共用同源逻辑, 设计 D6) ──
    from javert.onboarding.join_preflight import preflight_keys, preflight_time_windows
    spoke_meta = {sr.key: manifest.spoke(sr.key) for sr in result.spokes}
    pf = preflight_keys(result.spokes, spoke_meta)
    if pf is not None:
        log.info("")
        log.info("─── 连接预检 ───")
        log.info("  %s 键交集覆盖率: %.0f%% (%d/%d, 参照 %s) → %s %s",
                 pf.emoji, pf.coverage * 100, pf.hit, pf.total, pf.primary, pf.emoji, pf.label)
        for sk, rate in pf.per_spoke.items():
            log.info("    · %s: %.0f%%", sk, rate * 100)
        if pf.verdict == "red":
            log.warning("  🔴 键几乎不交 — 大概率列映射错或缺桥表 (key_mode/bridge), 建议核对后再审计")
    for tw in preflight_time_windows(result.spokes, spoke_meta):
        log.info("  %s 时间窗口 %s vs 住院期: %.0f%% 落窗口外 (%d/%d)%s",
                 tw["emoji"], tw["name"], tw["out_rate"] * 100, tw["out_window"],
                 tw["checked"], (" — " + tw["note"]) if tw["note"] else "")

    log.info("")
    log.info("═══ 汇总 ═══")
    for sr in result.spokes:
        log.info("  %s: %d 行, %d 患者", sr.output_file, len(sr.df), sr.patient_count)
    if all_warnings:
        log.info("")
        log.info("⚠ 可选列缺失 (不影响核心审计, 但部分功能受限):")
        for w in all_warnings:
            log.info(w)
    if dry_run:
        log.info("")
        log.info("(dry-run 模式, 未写盘)")
    else:
        _print_next_steps(out_path)


def _print_next_steps(out_path: Path) -> None:
    log.info("")
    log.info("✓ 转换完成, 文件在 %s/", out_path)
    log.info("")
    log.info("下一步 — 用新数据运行审计:")
    log.info("")
    rel = out_path.relative_to(PROJECT_ROOT) if out_path.is_relative_to(PROJECT_ROOT) else out_path
    log.info("  export JAVERT_DATA_DIR=%s", rel)
    log.info("  export JAVERT_ZD_FILE=shi_zd.csv")
    log.info("  export JAVERT_SS_FILE=shi_ss.csv")
    log.info("  uv run javert audit-patient <患者ID> --priority all --use-router")


def run_legacy(mapping_path: str, output_dir: str, dry_run: bool = False):
    with open(mapping_path, encoding="utf-8") as f:
        mapping = yaml.safe_load(f)

    errors = validate_mapping(mapping)
    if errors:
        log.error("映射配置校验失败:")
        for e in errors:
            log.error("  ✗ %s", e)
        sys.exit(1)

    hospital_code = mapping.get("hospital_code", "H99999999999")
    out_path = Path(output_dir)

    tables = [
        ("fees",       "case_notes.csv 无关, 费用",     "shi_fee.csv",  transform_fees),
        ("notes",      "病历文书",                       "case_notes.csv", transform_notes),
        ("diagnoses",  "病案首页-诊断",                  "shi_zd.csv",   transform_diagnoses),
        ("surgeries",  "病案首页-手术",                  "shi_ss.csv",   transform_surgeries),
    ]

    all_warnings: list[str] = []
    results: list[tuple[str, int, int, str]] = []

    for table_name, label, out_filename, transform_fn in tables:
        section = mapping[table_name]
        src_file = section["file"]
        col_map = section.get("columns", {})

        log.info("─── %s (%s) ───", label, src_file)

        try:
            src_df = read_source(src_file)
        except FileNotFoundError as e:
            log.error("  ✗ %s", e)
            sys.exit(1)

        log.info("  源文件: %d 行 × %d 列", len(src_df), len(src_df.columns))
        log.info("  源文件列: %s", ", ".join(src_df.columns.tolist()))

        warnings = validate_columns(src_df, col_map, table_name)
        all_warnings.extend(warnings)
        for w in warnings:
            log.warning(w)

        has_fatal = any("[必填]" in w for w in warnings)
        if has_fatal:
            log.error("  ✗ 存在必填列缺失, 无法继续")
            sys.exit(1)

        if table_name in ("fees", "diagnoses", "surgeries"):
            out_df = transform_fn(src_df, col_map, hospital_code)
        else:
            out_df = transform_fn(src_df, col_map)

        pids = set()
        if table_name == "notes":
            pids = set(out_df["住院号"].unique())
        elif "bah" in out_df.columns:
            pids = {v.split("-")[-1].strip() for v in out_df["bah"].unique() if v.strip()}
        elif "ba_id" in out_df.columns:
            pids = {v.split("-")[-1].strip() for v in out_df["ba_id"].unique() if v.strip()}

        log.info("  转换完成: %d 行, %d 个患者", len(out_df), len(pids))
        results.append((out_filename, len(out_df), len(pids), label))

        if not dry_run:
            out_path.mkdir(parents=True, exist_ok=True)
            dst = out_path / out_filename
            out_df.to_csv(dst, index=False, encoding="utf-8")
            log.info("  ✓ 写入 %s", dst)

    # ── 汇总 ──
    log.info("")
    log.info("═══ 汇总 ═══")
    for fname, rows, n_patients, label in results:
        log.info("  %s: %d 行, %d 患者", fname, rows, n_patients)

    if all_warnings:
        log.info("")
        log.info("⚠ 可选列缺失 (不影响核心审计, 但部分功能受限):")
        for w in all_warnings:
            log.info(w)

    if dry_run:
        log.info("")
        log.info("(dry-run 模式, 未写盘)")
    else:
        log.info("")
        log.info("✓ 转换完成, 文件在 %s/", out_path)
        log.info("")
        log.info("下一步 — 用新数据运行审计:")
        log.info("")
        rel = out_path.relative_to(PROJECT_ROOT) if out_path.is_relative_to(PROJECT_ROOT) else out_path
        log.info("  export JAVERT_DATA_DIR=%s", rel)
        log.info("  export JAVERT_ZD_FILE=shi_zd.csv")
        log.info("  export JAVERT_SS_FILE=shi_ss.csv")
        log.info("  uv run javert audit-patient <患者ID> --priority all --use-router")


def main():
    parser = argparse.ArgumentParser(description="外部医院数据 → Javert 格式转换")
    parser.add_argument("--mapping", default="configs/column_mapping.yaml",
                        help="列名映射 YAML (默认 configs/column_mapping.yaml)")
    parser.add_argument("--output", default="data_import",
                        help="输出目录 (默认 data_import/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只校验+预览, 不写盘")
    parser.add_argument("--legacy", action="store_true",
                        help="走旧硬编码 4 表路径 (fallback / 回归 oracle; 默认 manifest 驱动)")
    args = parser.parse_args()
    run(args.mapping, args.output, args.dry_run, args.legacy)


if __name__ == "__main__":
    main()
