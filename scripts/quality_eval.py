"""质量评估器 — 对 audit_runs 中 V/I 裁决自动打标 high-conf / questionable / malformed.

启发式 (基于 J66252 三窗对比观察提炼):

  malformed   = reasoning 含 'malformed' / 'tool budget exhausted' / 'deadline'
                或 confidence < 0.10 (技术失败, 不是真信号)
  high_conf   = confidence >= 0.85 AND evidence_count >= 2 AND reasoning_len >= 200
                AND verdict in (VIOLATION, INCONCLUSIVE)
  questionable = 其余 V/I (conf 较低或 evidence 少, 需人工 review)
  consistent_c = CLEAN with conf >= 0.90

入口:
  from scripts.quality_eval import evaluate_run, evaluate_batch
  badge = evaluate_run(audit_run_row)  → 'high_conf' / 'questionable' / 'malformed' / 'consistent_c' / 'unknown'
"""

from __future__ import annotations

import json
from typing import Optional

MALFORMED_MARKERS = (
    "malformed",
    "tool budget exhausted",
    "deadline",
    "repair failed",
    "未能输出 verdict",
)


def evaluate_run(verdict: str, confidence: Optional[float],
                  reasoning: Optional[str], evidence: list | str | None) -> str:
    """对单条 audit_run 打标.

    Returns:
        'malformed'    — 技术失败 (LLM 没收敛, conf=0, malformed JSON)
        'high_conf'    — V/I 高质量 (conf≥0.85, evidence≥2, reasoning≥200 字)
        'questionable' — V/I 较弱 (conf 中等或 evidence 少)
        'consistent_c' — CLEAN 高置信 (conf≥0.90)
        'weak_c'       — CLEAN 但 conf 较低
        'unknown'      — 无法判断
    """
    reasoning = reasoning or ""
    if isinstance(evidence, str):
        try:
            ev = json.loads(evidence) if evidence else []
        except (json.JSONDecodeError, TypeError):
            ev = []
    elif isinstance(evidence, list):
        ev = evidence
    else:
        ev = []

    conf = confidence or 0.0
    reasoning_lower = reasoning.lower()

    # 1) malformed 检测 (优先)
    for marker in MALFORMED_MARKERS:
        if marker.lower() in reasoning_lower:
            return "malformed"
    if conf < 0.10 and verdict in ("INCONCLUSIVE",):
        # 0.00 INCONCLUSIVE 多半是技术失败
        return "malformed"

    # 2) CLEAN
    if verdict == "CLEAN":
        return "consistent_c" if conf >= 0.90 else "weak_c"

    # 3) V/I 高质量
    if verdict in ("VIOLATION", "INCONCLUSIVE"):
        if conf >= 0.80 and len(ev) >= 1 and len(reasoning) >= 150:
            return "high_conf"
        return "questionable"

    return "unknown"


# 标签 → 显示用 (色 + 中文)
LABEL_DISPLAY = {
    "high_conf":    {"label": "高置信", "color": "#dc2626", "bg": "#fef2f2"},
    "questionable": {"label": "可疑",   "color": "#d97706", "bg": "#fffbeb"},
    "malformed":    {"label": "技术失败", "color": "#6b7280", "bg": "#f3f4f6"},
    "consistent_c": {"label": "高置信清白", "color": "#16a34a", "bg": "#f0fdf4"},
    "weak_c":       {"label": "弱清白",   "color": "#84cc16", "bg": "#f7fee7"},
    "unknown":      {"label": "未知",    "color": "#9ca3af", "bg": "#f9fafb"},
}


def evaluate_batch(rows: list[dict]) -> dict:
    """对一批 audit_runs (字典列表) 评估, 返回统计 + 每条标签."""
    counts = {k: 0 for k in LABEL_DISPLAY}
    per_run = []
    for r in rows:
        label = evaluate_run(
            r.get("verdict"),
            r.get("confidence"),
            r.get("reasoning"),
            r.get("evidence_json") or r.get("evidence"),
        )
        per_run.append({**r, "quality_label": label})
        counts[label] += 1
    return {"counts": counts, "rows": per_run}


if __name__ == "__main__":
    # 用 J66252 三窗 self-test
    import sqlite3
    con = sqlite3.connect("output/audit.sqlite")
    for label, t1, t2 in (
        ("v0.4", "2026-05-18 06:00", "2026-05-18 12:00"),
        ("off",  "2026-05-20 07:16", "2026-05-20 07:30"),
        ("on_v2","2026-05-20 09:08", "2026-05-20 09:13"),
    ):
        cur = con.execute("""
            SELECT a.rule_id, a.verdict, a.confidence, a.reasoning, a.evidence_json
            FROM audit_runs a
            INNER JOIN (
                SELECT rule_id, MAX(created_at) AS mc
                FROM audit_runs
                WHERE patient_id='J66252' AND created_at > ? AND created_at <= ?
                GROUP BY rule_id
            ) latest ON a.rule_id=latest.rule_id AND a.created_at=latest.mc
            WHERE a.patient_id='J66252' AND a.created_at > ? AND a.created_at <= ?
        """, (t1, t2, t1, t2))
        rows = [
            {"rule_id": r[0], "verdict": r[1], "confidence": r[2], "reasoning": r[3], "evidence_json": r[4]}
            for r in cur.fetchall()
        ]
        result = evaluate_batch(rows)
        print(f"\n=== window: {label} ({len(rows)} runs) ===")
        for k, v in result["counts"].items():
            if v > 0:
                print(f"  {k:15} {v}")
        # 高置信 V 列表
        hc_v = [r for r in result["rows"]
                if r["quality_label"] == "high_conf" and r["verdict"] == "VIOLATION"]
        print(f"  → high_conf V: {[r['rule_id'] for r in hc_v]}")
        # questionable V
        q_v = [r for r in result["rows"]
               if r["quality_label"] == "questionable" and r["verdict"] == "VIOLATION"]
        if q_v:
            print(f"  → questionable V: {[r['rule_id'] for r in q_v]}")
        # malformed V/I
        m = [r for r in result["rows"] if r["quality_label"] == "malformed"]
        if m:
            print(f"  → malformed: {[(r['rule_id'], r['verdict']) for r in m]}")
