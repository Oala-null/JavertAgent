# -*- coding: utf-8 -*-
"""跑完 v1.2 batch 后, 跟 v1.0 baseline diff.

对 30 老病人, 对每条 (patient, rule), 比对:
- v1.0 baseline (batch_tag IS NULL, 取 created_at desc 在 v1.2 跑之前)
- v1.2 (batch_tag = 'v1.2', 取 created_at desc)

翻转分类:
- V → C (好): v1.2 修复了误报
- V → I (中性): 之前确定违规, 现在变不明 — 视情况
- I → V (进步): ETL gap 解锁后确认了
- I → C (中性进步): 解锁后排除
- C → V (退化, 红): 之前认为合理现在判违规 — 需人工复核
- 其余: 不变

数据源: 142 SQL Server Javert_audit_runs (或本地 sqlite). 推荐部署后从 142 读.

输出: output/v1_2_oldset/diff_v1_0_v1_2.html
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _query_sqlite(db_path: Path, patient_ids: list[str]) -> dict[tuple[str, str], list[dict]]:
    """从本地 sqlite 拉 audit_runs (latest-per-batch). 返回 {(pid, rid): [{batch_tag, verdict, ...}, ...]}."""
    placeholders = ",".join("?" * len(patient_ids))
    sql = f"""
        SELECT patient_id, rule_id, verdict, confidence, batch_tag, created_at, run_id
        FROM audit_runs
        WHERE patient_id IN ({placeholders})
        ORDER BY created_at DESC
    """
    conn = sqlite3.connect(db_path)
    rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in conn.execute(sql, patient_ids):
        rows[(r[0], r[1])].append({
            "verdict": r[2],
            "confidence": r[3],
            "batch_tag": r[4],
            "created_at": r[5],
            "run_id": r[6],
        })
    conn.close()
    return rows


def _classify(v_old: str | None, v_new: str | None) -> str:
    """翻转分类. None = 没跑."""
    if v_old is None and v_new is None:
        return "missing"
    if v_old is None:
        return "v1.2_only"
    if v_new is None:
        return "v1.0_only"
    if v_old == v_new:
        return f"same_{v_old[0]}"
    return f"{v_old[0]}_to_{v_new[0]}"


def build_html(diffs: list[dict], out_path: Path):
    rows_html = []
    cat_counts: dict[str, int] = defaultdict(int)
    for d in diffs:
        cat_counts[d["category"]] += 1

    def _verdict_badge(v: str | None) -> str:
        if not v:
            return '<span class="muted">—</span>'
        color = {"VIOLATION": "v", "INCONCLUSIVE": "i", "CLEAN": "c"}.get(v, "u")
        label = {"VIOLATION": "V", "INCONCLUSIVE": "I", "CLEAN": "C"}.get(v, "?")
        return f'<span class="badge badge-{color}">{label}</span>'

    diffs.sort(key=lambda d: (d["patient_id"], d["rule_id"]))

    for d in diffs:
        rows_html.append(
            "<tr class='cat-" + d["category"] + "'>"
            f"<td>{escape(d['patient_id'])}</td>"
            f"<td><strong>{escape(d['rule_id'])}</strong></td>"
            f"<td>{_verdict_badge(d['v1_0'])}</td>"
            f"<td>{_verdict_badge(d['v1_2'])}</td>"
            f"<td>{escape(d['category'])}</td>"
            f"<td class='muted'>{escape(d['note'])}</td>"
            "</tr>"
        )

    cat_html = "".join(
        f"<li><span class='cat-pill cat-{escape(k)}'>{escape(k)}</span> × {v}</li>"
        for k, v in sorted(cat_counts.items(), key=lambda x: -x[1])
    )

    html = f"""<!DOCTYPE html>
<html lang='zh-CN'><head><meta charset='UTF-8'><title>v1.0 vs v1.2 diff</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 1400px; margin: 20px auto; padding: 0 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 13px; }}
  th {{ background: #f5f5f5; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-weight: 600; font-size: 12px; }}
  .badge-v {{ background: #fee; color: #b91c1c; }}
  .badge-i {{ background: #fef3c7; color: #b45309; }}
  .badge-c {{ background: #d1fae5; color: #047857; }}
  .badge-u {{ background: #eee; color: #555; }}
  .muted {{ color: #888; }}
  .cat-V_to_C {{ background: #ecfdf5; }}
  .cat-C_to_V {{ background: #fef2f2; }}
  .cat-I_to_V, .cat-I_to_C {{ background: #fef9c3; }}
  .cat-pill {{ display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; background: #eee; }}
  .cat-pill.cat-V_to_C {{ background: #d1fae5; }}
  .cat-pill.cat-C_to_V {{ background: #fee; color: #b91c1c; }}
  h1 {{ color: #1e40af; }}
  ul.cat-list {{ list-style: none; padding: 0; }}
  ul.cat-list li {{ display: inline-block; margin-right: 12px; }}
</style></head>
<body>
<h1>v1.0 baseline vs v1.2 重跑 — diff 报告</h1>
<p class='muted'>生成时间: {datetime.now().isoformat(timespec='seconds')}</p>
<p>共 {len(diffs)} 条 (patient, rule) 比对.</p>
<ul class='cat-list'>{cat_html}</ul>

<table>
<thead><tr><th>病人</th><th>规则</th><th>v1.0</th><th>v1.2</th><th>翻转</th><th>说明</th></tr></thead>
<tbody>
{''.join(rows_html)}
</tbody></table>

<p class='muted' style='margin-top:24px;'>
颜色: <span class='badge badge-v'>红 V</span> = VIOLATION,
<span class='badge badge-i'>黄 I</span> = INCONCLUSIVE,
<span class='badge badge-c'>绿 C</span> = CLEAN.
行底色: 绿 = 误报修复 (V→C); 红 = 退化 (C→V) 待核查; 黄 = I 解锁.
</p>
</body></html>
"""
    out_path.write_text(html, encoding="utf-8")
    print(f"已写 diff HTML 到 {out_path}")
    print("\n翻转分类统计:")
    for k, v in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:14s} {v}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sqlite", default=str(ROOT / "output" / "audit.sqlite"),
        help="本地 sqlite 路径 (默认 output/audit.sqlite)",
    )
    parser.add_argument(
        "--patient-list", default=str(ROOT / "data" / "v1_2_oldset.txt"),
        help="patient_id list (一行一个)",
    )
    parser.add_argument(
        "--out", default=str(ROOT / "output" / "v1_2_oldset" / "diff_v1_0_v1_2.html"),
        help="输出 HTML 路径",
    )
    args = parser.parse_args()

    pids = [l.strip() for l in Path(args.patient_list).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not pids:
        raise SystemExit(f"patient list 为空: {args.patient_list}")

    db_path = Path(args.sqlite)
    if not db_path.exists():
        raise SystemExit(f"sqlite 不存在: {db_path}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = _query_sqlite(db_path, pids)

    diffs = []
    for (pid, rid), runs in rows.items():
        # batch_tag = None 视为 v1.0; batch_tag='v1.2' 视为 v1.2
        v1_0 = next((r["verdict"] for r in runs if not r["batch_tag"]), None)
        v1_2 = next((r["verdict"] for r in runs if r["batch_tag"] == "v1.2"), None)
        if v1_0 is None and v1_2 is None:
            continue
        cat = _classify(v1_0, v1_2)
        note = ""
        if cat == "v1.2_only":
            note = "v1.0 baseline 未跑过该规则"
        elif cat == "v1.0_only":
            note = "v1.2 重跑漏了该规则 (router prune 或 error)"
        elif cat == "C_to_V":
            note = "退化: v1.0 判 clean, v1.2 判违规 — 人工核查"
        elif cat == "V_to_C":
            note = "误报修复: v1.0 V, v1.2 C — 新数据 / 新工具发挥作用"
        diffs.append({
            "patient_id": pid,
            "rule_id": rid,
            "v1_0": v1_0,
            "v1_2": v1_2,
            "category": cat,
            "note": note,
        })

    build_html(diffs, out_path)


if __name__ == "__main__":
    main()
