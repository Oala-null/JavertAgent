"""分析 gate-v3-fresh 批次 — 验证确定性闸在全新病人上消假阳性 + 不让被驳回违规复现.

用法 (62 本地 sqlite): uv run python scripts/analyze_gate_v3.py
关键检查: 之前被专家驳回的规则 (R203/R205/R131/R153/R154/R156/R103/R105) 在新病人上应 ~0 V;
不可确认文书规则应大量落 I; gate_tag 分布看闸触发情况.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "output" / "audit.sqlite"
TAG = "gate-v3-fresh"

# 之前被专家驳回 (C/I) 的规则 — 新病人上若仍大量出 V = 修复失败
PREV_REJECTED = ["R203", "R205", "R131", "R153", "R154", "R156", "R103", "R105"]


def main() -> None:
    con = sqlite3.connect(DB)
    df = pd.read_sql(
        "select patient_id, rule_id, verdict, gate_tag, confidence "
        "from audit_runs where batch_tag=? ",
        con, params=[TAG],
    )
    con.close()
    if df.empty:
        print(f"无 {TAG} 数据 (批次可能刚启动).")
        return

    npat = df["patient_id"].nunique()
    vc = Counter(df["verdict"])
    print(f"==== gate-v3-fresh: {npat} 患者, {len(df)} 裁决 ====")
    print(f"V={vc.get('VIOLATION',0)}  I={vc.get('INCONCLUSIVE',0)}  C={vc.get('CLEAN',0)}\n")

    # gate_tag 分布
    gt = df[df["gate_tag"].fillna("") != ""]["gate_tag"].value_counts()
    print("闸触发分布 (LLM 出 V 被闸降):")
    print(gt.to_string() if not gt.empty else "  (无显式闸触发 — LLM 多自判对)")
    print()

    # 关键: 之前被驳回的规则在新病人上的 V 数
    print("之前被专家驳回规则在新病人上的 V 数 (目标 ~0):")
    sub = df[df["rule_id"].isin(PREV_REJECTED)]
    for rid in PREV_REJECTED:
        r = sub[sub["rule_id"] == rid]
        if r.empty:
            continue
        c = Counter(r["verdict"])
        v = c.get("VIOLATION", 0)
        flag = "  ⚠️有V!" if v else "  ✓"
        print(f"  {rid}: V={v} I={c.get('INCONCLUSIVE',0)} C={c.get('CLEAN',0)}{flag}")

    # 全局 V 最多的规则 (看新假阳性集中点)
    print("\n全局 V 最多规则 Top10:")
    vrules = df[df["verdict"] == "VIOLATION"]["rule_id"].value_counts().head(10)
    print(vrules.to_string() if not vrules.empty else "  (无 V)")


if __name__ == "__main__":
    main()
