# -*- coding: utf-8 -*-
"""松中心 (song) 检验数据 xlsx → LabLoader 吃的 lab_results.csv.

源: data/song/szx_lab/*.xlsx (5 份检验数据 test_item + 1 份检查细菌 bacteria).
键: source_inpat_no → zyh (8 位裸住院号, 与 szx_doc.source_inpat_no 一致, 100% 对得上).
输出列 = manifest labs.output_schema 16 列 (缺的补空).

用法: uv run python scripts/import_szx_lab.py
       uv run python scripts/import_szx_lab.py --check   # 只跑自检不写盘
"""
from __future__ import annotations
import glob, sys
from pathlib import Path
import pandas as pd

SRC = Path("data/song/szx_lab")
OUT = Path("data/song/lab_results.csv")
DOC = Path("data/song/szx_doc.csv")

# LabLoader.LAB_USECOLS (manifest labs.output_schema) — 顺序即输出列序
COLS = ["zyh", "rpt_itemname", "rpt_itemcode", "result", "result_unit", "result_ref",
        "result_flag", "diagnosisOpinion", "department", "report_dt", "specimen",
        "inspectionName", "age", "sex", "trier", "auditor"]

# 两套源 schema → 统一列. 公共列 + 各自的项名/代码/结果.
COMMON = {"source_inpat_no": "zyh", "unit_name": "result_unit", "reference_range": "result_ref",
          "abnormal_flag_code": "result_flag", "report_time": "report_dt",
          "sample_name": "specimen", "report_name": "inspectionName"}
TESTITEM = {**COMMON, "test_item_name": "rpt_itemname", "test_item_code": "rpt_itemcode",
            "indicatorresult": "result"}
BACTERIA = {**COMMON, "bacteria_name": "rpt_itemname", "bacteria_code": "rpt_itemcode",
            "colony_counting": "result"}

# 无异常方向的 flag → 空 (对齐 LabLoader.NORMAL_FLAGS, 使 abnormal_only / 工作台红底准确).
# 保留: H HH L LL 阳 Err 请核实 (后两者=需核实, 标出来合理).
FLAG_TO_BLANK = {"(null)", "否", "nan", "N", "正常"}


def load() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(SRC / "*.xlsx"))):
        df = pd.read_excel(f, dtype=str)
        m = BACTERIA if "bacteria_name" in df.columns else TESTITEM
        frames.append(df.rename(columns=m))
    df = pd.concat(frames, ignore_index=True).fillna("")
    for c in COLS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLS].copy()
    df["zyh"] = df["zyh"].str.strip()
    df["result_flag"] = df["result_flag"].str.strip().where(
        ~df["result_flag"].str.strip().isin(FLAG_TO_BLANK), "")
    df = df[df["zyh"] != ""].drop_duplicates(ignore_index=True)  # 处理 3/4 号重叠
    return df


def selfcheck(df: pd.DataFrame) -> None:
    assert list(df.columns) == COLS, "输出列不匹配 LabLoader schema"
    assert (df["zyh"] != "").all(), "存在空 zyh"
    lab_pids = set(df["zyh"])
    doc_pids = set(pd.read_csv(DOC, usecols=["source_inpat_no"], dtype=str)["source_inpat_no"].str.strip())
    overlap = len(lab_pids & doc_pids) / len(lab_pids)
    assert overlap > 0.99, f"lab↔doc 患者重合仅 {overlap:.1%}, 键可能错了"
    print(f"自检通过: {len(df)} 行 / {len(lab_pids)} 患者, lab↔doc 重合 {overlap:.1%}")


if __name__ == "__main__":
    df = load()
    selfcheck(df)
    if "--check" not in sys.argv:
        df.to_csv(OUT, index=False)
        print(f"已写 {OUT} ({OUT.stat().st_size/1e6:.0f} MB)")
