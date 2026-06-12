# -*- coding: utf-8 -*-
"""选 30 个老病人作为 v1.2 重跑 batch 的核心集合.

策略 (v0.7 ab 实验):
  - 7 个**必带** = 专家批注覆盖 (J61556 / J26355 / J29579 / J13365 / J30112 / J98941 / J22714)
  - 23 个从本地 audit.sqlite 选 V 数最高且不在 7 个里面的 (跨 11+ V 模式 + R131/R153/R203 高产 patient)
  - 总 30, 输出到 data/v1_2_oldset.txt 供 scripts/run_v1_2_oldset.py 跑

为什么要 negative regression: 已经被 audit 多次 + 专家未质疑的 patient (R141/R143/R146/R161 高一致 V)
也混入, 验证新数据 + 新工具不会反过来让正确判定漂移成误报.

输出:
  data/v1_2_oldset.txt — 30 行 patient_id, 一行一个
  data/v1_2_oldset_meta.json — 选病人时的 V/I/C 统计 (审计追溯用)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = ROOT / "output" / "audit.sqlite"
OUT_TXT = ROOT / "data" / "v1_2_oldset.txt"
OUT_META = ROOT / "data" / "v1_2_oldset_meta.json"

# 7 个专家批注覆盖 — 即便本地 sqlite 没有也必须包含 (将由 142 出数据)
ANNOTATED_PATIENTS: tuple[str, ...] = (
    "J61556", "J26355", "J29579", "J13365", "J30112", "J98941", "J22714",
)

# audit.sqlite 中已知是测试/烟测的, 跳过
EXCLUDE_PATIENTS: tuple[str, ...] = ("K_DIRECT_SYNC",)


def _query_top_v_patients(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """从 sqlite 拿 V 最多 + 排除 annotated/excluded 的 top N patients."""
    excl_clause_parts = []
    params = {"limit": limit + 10}  # 多取一些, 排除后还够
    for i, pid in enumerate(ANNOTATED_PATIENTS + EXCLUDE_PATIENTS):
        excl_clause_parts.append(f":excl_{i}")
        params[f"excl_{i}"] = pid
    excl_clause = ", ".join(excl_clause_parts)
    sql = f"""
        WITH latest AS (
            SELECT patient_id, rule_id, verdict, run_id,
                   ROW_NUMBER() OVER (PARTITION BY patient_id, rule_id ORDER BY created_at DESC) AS rn
            FROM audit_runs
        )
        SELECT patient_id,
               SUM(CASE WHEN verdict = 'VIOLATION' THEN 1 ELSE 0 END) AS v_count,
               SUM(CASE WHEN verdict = 'INCONCLUSIVE' THEN 1 ELSE 0 END) AS i_count,
               SUM(CASE WHEN verdict = 'CLEAN' THEN 1 ELSE 0 END) AS c_count
        FROM latest
        WHERE rn = 1
          AND patient_id NOT IN ({excl_clause})
        GROUP BY patient_id
        ORDER BY v_count DESC, i_count DESC, patient_id ASC
        LIMIT :limit
    """
    rows = []
    for r in conn.execute(sql, params):
        rows.append({
            "patient_id": r[0],
            "v_count": int(r[1] or 0),
            "i_count": int(r[2] or 0),
            "c_count": int(r[3] or 0),
        })
    return rows


def main():
    if not SQLITE_PATH.exists():
        raise SystemExit(f"未找到 {SQLITE_PATH}; 先跑过 audit-patient 才能选病人")

    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    target_count = 30
    extra_needed = target_count - len(ANNOTATED_PATIENTS)  # 23

    conn = sqlite3.connect(SQLITE_PATH)
    extra = _query_top_v_patients(conn, extra_needed)[:extra_needed]
    conn.close()

    extra_pids = [r["patient_id"] for r in extra]
    final_list = list(ANNOTATED_PATIENTS) + extra_pids

    # 写 patient list
    OUT_TXT.write_text("\n".join(final_list) + "\n", encoding="utf-8")
    print(f"已写 {len(final_list)} 个 patient_id 到 {OUT_TXT}")

    # 写 metadata
    meta = {
        "total": len(final_list),
        "annotated_7": list(ANNOTATED_PATIENTS),
        "top_v_23": extra,
        "note": (
            "专家批注 7 个 (J61556..J22714) 来自 docs/javert_vio_review.csv 全表; "
            "其他 23 个来自 output/audit.sqlite latest-per (rule,patient) 按 V 数排序前 23 "
            "(排除 annotated + 测试用 patient). 跑 v1.2 时全集 ready 规则 + JAVERT_BATCH_TAG=v1.2."
        ),
    }
    OUT_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已写 metadata 到 {OUT_META}")

    print("\n--- 7 必带 (专家批注): ---")
    for pid in ANNOTATED_PATIENTS:
        print(f"  {pid}")
    print("\n--- 23 V 多 (sqlite latest, 含 negative regression): ---")
    for r in extra:
        print(f"  {r['patient_id']:10s}  V={r['v_count']:2d} I={r['i_count']:2d} C={r['c_count']:3d}")


if __name__ == "__main__":
    main()
