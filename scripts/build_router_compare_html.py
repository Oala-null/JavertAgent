"""J66252 router 对比 3-tab HTML — v0.4 baseline vs off (本次 P0 全跑) vs on v2 (单闸 router).

每 tab 复用 build_clerk_report.py 的 render_patient_tab; 三 tab 同一病人不同规则集合.

跑法:
  uv run python scripts/build_router_compare_html.py
输出:
  output/router_compare_J66252.html
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 复用 build_clerk_report.py 的渲染/加载逻辑 (PATIENTS monkey-patch)
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import build_clerk_report as clerk

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "output" / "audit.sqlite"
OUT_HTML = ROOT / "output" / "router_compare_J66252.html"

PATIENT_ID = "J66252"

# 三个时间窗 (确认见 audit_runs 时间分布)
WINDOWS = [
    {
        "key": "v0.4",
        "label": "v0.4 baseline (5-18 全 ready)",
        "cutoff_start": "2026-05-18 06:00:00",
        "cutoff_end":   "2026-05-18 12:00:00",
        "note":         "5-18 跑 111 ready 规则 (P0+P1+P2). Clerk report v0.4 数据来源.",
        "tone":         "#6b7280",
    },
    {
        "key": "off",
        "label": "off (P0 全跑, 不开 router)",
        "cutoff_start": "2026-05-20 07:16:00",
        "cutoff_end":   "2026-05-20 07:30:00",
        "note":         "本次跑 57 条 P0, 串行 concurrency=5, 共 72.7 min LLM. baseline 用来验证 router 漏检.",
        "tone":         "#1e3a8a",
    },
    {
        "key": "on_v2",
        "label": "on v2 (router 单闸 + 弹性 keyword)",
        "cutoff_start": "2026-05-20 09:08:00",
        "cutoff_end":   "2026-05-20 09:13:00",
        "note":         "单闸 router (去 Case A Java 字典 AND 闸) + 弹性 keyword 子串. 18 条 LLM 24.5 min.",
        "tone":         "#16a34a",
    },
]


def load_runs_in_window(patient_id: str, t_start: str, t_end: str) -> list[dict]:
    """从 audit_runs 取窗内 patient 的 runs, 同一 rule_id 取 latest."""
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""
        SELECT a.run_id, a.rule_id, a.patient_id, a.verdict, a.confidence,
               a.reasoning, a.evidence_json, a.duration_ms, a.created_at
        FROM audit_runs a
        INNER JOIN (
            SELECT rule_id, MAX(created_at) AS mc
            FROM audit_runs
            WHERE patient_id = ? AND created_at > ? AND created_at <= ?
            GROUP BY rule_id
        ) latest ON a.rule_id = latest.rule_id AND a.created_at = latest.mc
        WHERE a.patient_id = ? AND a.created_at > ? AND a.created_at <= ?
        ORDER BY a.created_at ASC
    """, (patient_id, t_start, t_end, patient_id, t_start, t_end))
    rows = cur.fetchall()
    con.close()
    runs = []
    for r in rows:
        run_id, rid, pid, verdict, conf, reasoning, ev_json, dur, created = r
        runs.append({
            "run_id": run_id,
            "rule_id": rid,
            "patient_id": pid,
            "verdict": verdict,
            "confidence": conf,
            "reasoning": reasoning or "",
            "evidence": json.loads(ev_json) if ev_json else [],
            "duration_ms": dur,
            "created_at": created,
        })
    runs.sort(key=lambda r: (
        0 if r["verdict"] == "VIOLATION" else (1 if r["verdict"] == "INCONCLUSIVE" else 2),
        -float(r["confidence"] or 0),
        r["rule_id"],
    ))
    return runs


def render_tab_for_window(window: dict, basics: dict, rules: dict, zd: dict, ss: dict) -> str:
    """渲染单 window 的 patient tab. tab id 用 window key 区分."""
    runs = load_runs_in_window(PATIENT_ID, window["cutoff_start"], window["cutoff_end"])
    # render_patient_tab 期望 tab id = "tab-{pid}", 我们改成 "tab-{key}" 避免 3 个 tab 冲突
    raw = clerk.render_patient_tab(PATIENT_ID, basics[PATIENT_ID], runs, rules, zd, ss)
    raw = raw.replace(f'id="tab-{PATIENT_ID}"', f'id="tab-{window["key"]}"')
    # 在 h2 顶部插入窗描述
    badge = (
        f"<div style='background:#f0f9ff;border-left:4px solid {window['tone']};"
        f"padding:10px 14px;margin:-10px 0 16px 0;font-size:13px'>"
        f"<b style='color:{window['tone']}'>{window['label']}</b><br>"
        f"<span style='color:#6b7280'>{window['note']}</span><br>"
        f"<span style='color:#9ca3af;font-size:12px'>"
        f"时间窗: {window['cutoff_start']} → {window['cutoff_end']} · "
        f"共 {len(runs)} 条裁决</span></div>"
    )
    raw = raw.replace("<h2>", badge + "<h2>", 1)
    return raw, runs


def _v_count(runs: list[dict], verdict: str) -> int:
    return sum(1 for r in runs if r["verdict"] == verdict)


def build_html(tabs_html: list[str], runs_by_key: dict[str, list[dict]]) -> str:
    nav_buttons = ""
    for w in WINDOWS:
        rs = runs_by_key[w["key"]]
        nav_buttons += (
            f"<button class='tab-btn' data-target='tab-{w['key']}'>"
            f"<div class='nav-pid'>{w['key']}</div>"
            f"<div class='nav-dx'>{clerk.esc(w['label'])[:24]}</div>"
            f"<div class='nav-meta'>"
            f"<span class='nv'>V {_v_count(rs, 'VIOLATION')}</span>"
            f"<span class='ni'>I {_v_count(rs, 'INCONCLUSIVE')}</span>"
            f"<span class='nc'>C {_v_count(rs, 'CLEAN')}</span>"
            f" · {len(rs)}</div></button>"
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 顶部全局摘要: 三窗 V/C/I 横向对比
    summary_rows = ""
    for w in WINDOWS:
        rs = runs_by_key[w["key"]]
        total_dur = sum(r["duration_ms"] or 0 for r in rs)
        summary_rows += (
            f"<tr style='background:#fafafa'>"
            f"<td style='font-family:monospace;font-weight:bold'>{w['key']}</td>"
            f"<td>{w['label']}</td>"
            f"<td style='text-align:right'>{len(rs)}</td>"
            f"<td style='text-align:right;color:#dc2626;font-weight:bold'>{_v_count(rs, 'VIOLATION')}</td>"
            f"<td style='text-align:right;color:#d97706'>{_v_count(rs, 'INCONCLUSIVE')}</td>"
            f"<td style='text-align:right;color:#16a34a'>{_v_count(rs, 'CLEAN')}</td>"
            f"<td style='text-align:right'>{total_dur/60000:.1f} min</td>"
            f"</tr>"
        )

    # V verdict 跨 window 对比表
    all_v_rules: set[str] = set()
    for rs in runs_by_key.values():
        for r in rs:
            if r["verdict"] in ("VIOLATION", "INCONCLUSIVE"):
                all_v_rules.add(r["rule_id"])

    v_diff_rows = ""
    if all_v_rules:
        for rid in sorted(all_v_rules):
            v_diff_rows += f"<tr><td style='font-family:monospace;font-weight:bold'>{rid}</td>"
            for w in WINDOWS:
                rs = runs_by_key[w["key"]]
                hit = next((r for r in rs if r["rule_id"] == rid), None)
                if hit is None:
                    v_diff_rows += "<td style='text-align:center;color:#9ca3af'>—</td>"
                else:
                    v = hit["verdict"]
                    color = {"VIOLATION": "#dc2626", "INCONCLUSIVE": "#d97706", "CLEAN": "#16a34a"}[v]
                    short = {"VIOLATION": "V", "INCONCLUSIVE": "I", "CLEAN": "C"}[v]
                    v_diff_rows += (
                        f"<td style='text-align:center;color:{color};font-weight:bold'>"
                        f"{short} <span style='color:#6b7280;font-weight:normal;font-size:11px'>"
                        f"({hit['confidence']:.2f})</span></td>"
                    )
            v_diff_rows += "</tr>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>J66252 Router 对比 · v0.4 vs off vs on v2</title>
<style>
  * {{ box-sizing: border-box }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; background: #f3f4f6; color: #1f2937; font-size: 14px }}
  header {{ background: linear-gradient(135deg, #6d28d9, #3b82f6);
           color: white; padding: 18px 24px }}
  header h1 {{ margin: 0; font-size: 20px }}
  header .sub {{ opacity: 0.92; margin-top: 4px; font-size: 13px }}
  .summary-block {{ background: white; padding: 14px 20px; margin: 14px 24px 0;
                     border-radius: 8px }}
  .summary-block h3 {{ margin: 0 0 8px; font-size: 14px; color: #1f2937 }}
  .summary-table {{ border-collapse: collapse; width: 100%; font-size: 13px }}
  .summary-table th, .summary-table td {{ border: 1px solid #e5e7eb;
        padding: 6px 10px; text-align: left }}
  .summary-table th {{ background: #f9fafb; font-weight: 600 }}
  nav.tabs {{ background: white; padding: 12px 24px; display: flex; gap: 6px;
             overflow-x: auto; border-bottom: 1px solid #e5e7eb;
             position: sticky; top: 0; z-index: 10; margin-top: 12px }}
  .tab-btn {{ padding: 10px 14px; border: 1px solid #d1d5db; background: white;
             cursor: pointer; border-radius: 6px; min-width: 160px;
             text-align: center; white-space: nowrap }}
  .tab-btn:hover {{ background: #f9fafb; border-color: #93c5fd }}
  .tab-btn.active {{ background: #6d28d9; color: white; border-color: #6d28d9 }}
  .tab-btn.active .nav-dx {{ color: #cbd5e1 }}
  .nav-pid {{ font-family: monospace; font-weight: bold; font-size: 13px }}
  .nav-dx {{ font-size: 11px; color: #6b7280; margin: 2px 0 4px }}
  .nav-meta {{ font-size: 11px; display: flex; gap: 5px; justify-content: center }}
  .nv {{ color: #dc2626 }} .ni {{ color: #d97706 }} .nc {{ color: #16a34a }}
  .tab-btn.active .nv, .tab-btn.active .ni, .tab-btn.active .nc {{ color: white }}
  main {{ padding: 20px 24px }}
  .patient-tab {{ display: none; background: white; padding: 22px;
                  border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05) }}
  .patient-tab.active {{ display: block }}
  h2 {{ margin-top: 0; border-bottom: 2px solid #6d28d9; padding-bottom: 8px }}
  h2 .badge-v, h2 .badge-i, h2 .badge-c {{ font-size: 12px; padding: 3px 9px;
        margin-left: 6px; border-radius: 12px; color: white; font-weight: normal }}
  .badge-v {{ background: #dc2626 }}
  .badge-i {{ background: #d97706 }}
  .badge-c {{ background: #16a34a }}
  h3 {{ margin-top: 26px; color: #6d28d9; border-left: 4px solid #6d28d9;
        padding-left: 10px }}
  .block {{ margin-bottom: 28px }}
  .meta-table, .data-table {{ border-collapse: collapse; width: 100%; margin-top: 6px }}
  .meta-table th, .meta-table td, .data-table th, .data-table td {{
      border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left;
      vertical-align: top }}
  .meta-table th, .data-table th {{ background: #f9fafb; font-weight: 600 }}
  .data-table td.num {{ text-align: right; font-variant-numeric: tabular-nums }}
  .data-table th:nth-child(n+3) {{ text-align: right }}
  .subblock {{ margin-top: 14px }}
  .subblock summary {{ cursor: pointer; padding: 6px 0; font-size: 14px }}
  .note-box {{ background: #fafafa; border-left: 3px solid #94a3b8;
               padding: 8px 12px; margin-top: 4px; line-height: 1.6;
               font-size: 13px; white-space: pre-wrap }}
  .dx-list {{ margin: 6px 0 0 22px; line-height: 1.7 }}
  .audit-summary {{ display: flex; gap: 16px; margin-top: 10px; flex-wrap: wrap }}
  .stat-card {{ flex: 1; min-width: 130px; padding: 16px; border-radius: 8px;
                border: 2px solid; text-align: center }}
  .stat-num {{ font-size: 32px; font-weight: bold }}
  .stat-lab {{ margin-top: 4px; color: #6b7280; font-size: 12px }}
  .rule-card {{ border: 1px solid #e5e7eb; border-left: 5px solid;
                border-radius: 4px; padding: 12px; margin-bottom: 10px;
                background: #fafafa }}
  .rule-head {{ display: flex; gap: 10px; align-items: center;
                flex-wrap: wrap; margin-bottom: 8px }}
  .verdict-badge {{ color: white; padding: 3px 10px; border-radius: 4px;
                    font-size: 12px; font-weight: bold }}
  .rule-id {{ font-family: monospace; font-size: 13px }}
  .rule-title {{ font-weight: 600; color: #1f2937 }}
  .rule-meta {{ font-size: 12px; color: #6b7280; margin-left: auto }}
  .reasoning {{ background: white; padding: 10px; border-radius: 4px;
                line-height: 1.55 }}
  details summary {{ cursor: pointer; margin-top: 6px; color: #6d28d9 }}
  .ev {{ margin-top: 6px; padding-left: 20px }}
  .ev li {{ margin-bottom: 8px; line-height: 1.5 }}
  .ev .loc {{ color: #6b7280; font-size: 12px }}
  .muted {{ color: #9ca3af }}
  .spec-tag {{ font-size: 11px; color: #6b7280; margin-top: 2px }}
  .clean-pill {{ display: inline-block; padding: 2px 7px; margin: 2px;
                 background: #f0fdf4; color: #166534; border-radius: 3px;
                 font-family: monospace; font-size: 12px; cursor: help }}
  .bar {{ width: 100%; height: 14px; background: #f3f4f6; border-radius: 3px;
          overflow: hidden }}
  .bar-fill {{ height: 100%; background: linear-gradient(90deg,#a78bfa,#6d28d9) }}
</style>
</head>
<body>
<header>
  <h1>J66252 · Router 三轮对比 (甲状腺恶性肿瘤主测试病例)</h1>
  <div class="sub">生成 {now} · 三 tab: v0.4 baseline vs off (P0 全跑) vs on v2 (router 单闸)</div>
</header>

<div class="summary-block">
  <h3>① 三窗汇总 verdict</h3>
  <table class="summary-table">
    <thead><tr><th>key</th><th>说明</th><th>规则数</th><th style="text-align:right">V</th><th style="text-align:right">I</th><th style="text-align:right">C</th><th style="text-align:right">LLM 总耗时</th></tr></thead>
    <tbody>{summary_rows}</tbody>
  </table>
</div>

<div class="summary-block">
  <h3>② V/I 规则跨窗 diff (∪{len(all_v_rules)} 条曾命中过的规则)</h3>
  <p class="muted" style="margin:4px 0">"—" 表示该 window 未跑此规则 (router prune 或不在 priority 集合).</p>
  <table class="summary-table">
    <thead><tr><th>rule_id</th>{"".join(f"<th style='text-align:center'>{w['key']}</th>" for w in WINDOWS)}</tr></thead>
    <tbody>{v_diff_rows}</tbody>
  </table>
</div>

<nav class="tabs">{nav_buttons}</nav>

<main>{"".join(tabs_html)}</main>

<script>
  const btns = document.querySelectorAll('.tab-btn');
  const tabs = document.querySelectorAll('.patient-tab');
  function activate(target) {{
    btns.forEach(b => b.classList.toggle('active', b.dataset.target === target));
    tabs.forEach(t => t.classList.toggle('active', t.id === target));
  }}
  btns.forEach(b => b.addEventListener('click', () => activate(b.dataset.target)));
  // 默认: on v2 (新方案)
  activate('tab-on_v2');
</script>
</body>
</html>"""


def main() -> None:
    # monkey-patch PATIENTS, 只渲染 J66252
    clerk.PATIENTS = [PATIENT_ID]

    print("loading rules + zd + ss + basics ...")
    rules = clerk.load_rules()
    ss = clerk.load_shi_ss_surgeries()
    zd = clerk.load_shi_zd_diagnoses()
    basics = clerk.load_patient_basics()

    tabs_html: list[str] = []
    runs_by_key: dict[str, list[dict]] = {}
    for w in WINDOWS:
        tab_html, runs = render_tab_for_window(w, basics, rules, zd, ss)
        tabs_html.append(tab_html)
        runs_by_key[w["key"]] = runs
        print(f"  window {w['key']}: {len(runs)} runs "
              f"(V={sum(1 for r in runs if r['verdict']=='VIOLATION')}, "
              f"C={sum(1 for r in runs if r['verdict']=='CLEAN')}, "
              f"I={sum(1 for r in runs if r['verdict']=='INCONCLUSIVE')})")

    html = build_html(tabs_html, runs_by_key)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"\nwritten: {OUT_HTML}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
