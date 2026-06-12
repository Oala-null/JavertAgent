"""50 病人 router-v2 + ready 全集 审计报告 HTML.

布局: 左侧 sticky patient list (V 数倒序), 右侧详情 (复用 clerk_report 模板).
顶部全局: 50 病人累计 V/I/C + 高置信 V 数 + 平均耗时 + 质量评估摘要.

数据源: audit_runs 表, 按 cutoff 时间窗取 (本次 batch 跑后).

跑法:
  uv run python scripts/build_50_html.py \\
      --cutoff-start "2026-05-20 13:00:00" \\
      --cutoff-end   "2026-05-21 03:00:00"

输出:
  output/router_v2_50patients.html
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import click

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import build_clerk_report as clerk  # noqa: E402
from quality_eval import LABEL_DISPLAY, evaluate_batch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "output" / "audit.sqlite"
PATIENTS_FILE = ROOT / "data" / "router_test_50patients.txt"
OUT_HTML = ROOT / "output" / "router_v2_50patients.html"


def load_patients_list() -> list[str]:
    return [
        line.strip()
        for line in PATIENTS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_runs_in_window(patient_id: str, t_start: str, t_end: str) -> list[dict]:
    """取窗内 patient 的 audit_runs, 按 rule_id 取最新."""
    con = sqlite3.connect(DB)
    cur = con.execute("""
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
    out: list[dict] = []
    for r in cur.fetchall():
        rid_, rule_id, pid, verdict, conf, reasoning, ev_json, dur, created = r
        out.append({
            "run_id": rid_,
            "rule_id": rule_id,
            "patient_id": pid,
            "verdict": verdict,
            "confidence": conf,
            "reasoning": reasoning or "",
            "evidence": json.loads(ev_json) if ev_json else [],
            "evidence_json": ev_json,
            "duration_ms": dur,
            "created_at": created,
        })
    con.close()
    out.sort(key=lambda r: (
        0 if r["verdict"] == "VIOLATION" else (1 if r["verdict"] == "INCONCLUSIVE" else 2),
        -float(r["confidence"] or 0),
        r["rule_id"],
    ))
    return out


def _v_count(runs: list[dict], verdict: str) -> int:
    return sum(1 for r in runs if r["verdict"] == verdict)


def short_dx(zd_entry: dict) -> str:
    if zd_entry.get("main"):
        return zd_entry["main"][0]["name"][:16]
    return "—"


def build_summary_table(
    patients_data: list[dict],
) -> tuple[str, str, dict]:
    """汇总: 全局 stats + 50 行 patient 总表 + quality counts."""
    total_v = sum(_v_count(p["runs"], "VIOLATION") for p in patients_data)
    total_i = sum(_v_count(p["runs"], "INCONCLUSIVE") for p in patients_data)
    total_c = sum(_v_count(p["runs"], "CLEAN") for p in patients_data)
    total_runs = total_v + total_i + total_c
    total_llm_ms = sum(
        (r["duration_ms"] or 0) for p in patients_data for r in p["runs"]
    )

    # quality 评估
    all_rows: list[dict] = []
    for p in patients_data:
        for r in p["runs"]:
            all_rows.append({
                **r, "patient_id": p["patient_id"],
            })
    q = evaluate_batch(all_rows)
    q_counts = q["counts"]

    # 高置信 V 数 = high_conf 中 verdict=VIOLATION
    high_conf_v = sum(
        1 for row in q["rows"]
        if row["quality_label"] == "high_conf" and row["verdict"] == "VIOLATION"
    )
    questionable_v = sum(
        1 for row in q["rows"]
        if row["quality_label"] == "questionable" and row["verdict"] == "VIOLATION"
    )

    global_stats_html = f"""
      <div class="global-stats">
        <div class="gs">病人数 <span class="v">{len(patients_data)}</span></div>
        <div class="gs">总裁决 <span class="v">{total_runs}</span></div>
        <div class="gs">违规 V <span class="v" style="color:#fca5a5">{total_v}</span></div>
        <div class="gs">高置信 V <span class="v" style="color:#fef2f2">{high_conf_v}</span></div>
        <div class="gs">可疑 V <span class="v" style="color:#fed7aa">{questionable_v}</span></div>
        <div class="gs">存疑 I <span class="v" style="color:#fed7aa">{total_i}</span></div>
        <div class="gs">合规 C <span class="v" style="color:#86efac">{total_c}</span></div>
        <div class="gs">LLM 总耗时 <span class="v">{total_llm_ms/60000:.1f} min</span></div>
      </div>
    """

    return global_stats_html, "", q_counts


def build_patient_list_sidebar(patients_data: list[dict]) -> str:
    """左侧 patient 列表, 默认按 V 数倒序."""
    rows = []
    for p in patients_data:
        runs = p["runs"]
        v = _v_count(runs, "VIOLATION")
        i = _v_count(runs, "INCONCLUSIVE")
        c = _v_count(runs, "CLEAN")
        dx = clerk.esc(short_dx(p["zd_entry"]))
        pid = clerk.esc(p["patient_id"])
        fees = p["basics_entry"]["fees_count"]
        sum_amt = p["basics_entry"]["fees_sum"]
        # high-conf V 数
        hc_v = sum(
            1 for r in p.get("quality_rows", [])
            if r.get("quality_label") == "high_conf" and r.get("verdict") == "VIOLATION"
        )
        rows.append({
            "v": v, "i": i, "c": c, "hc_v": hc_v,
            "pid": pid, "dx": dx, "fees": fees, "sum_amt": sum_amt,
        })

    # 按 V 倒序 + 高置信 V 倒序 + 总裁决倒序
    rows.sort(key=lambda r: (-r["v"], -r["hc_v"], -r["fees"], r["pid"]))

    html = "<div class='sidebar-search'><input type='text' id='sidebar-filter' placeholder='搜 pid 或诊断 …' /></div>"
    html += "<div class='sidebar-list'>"
    for r in rows:
        v_badge = (
            f"<span class='pl-v' style='color:#dc2626'>V {r['v']}</span>"
            if r["v"] > 0 else "<span class='pl-v' style='color:#9ca3af'>V 0</span>"
        )
        hc_marker = (
            f" <span class='pl-hc' title='高置信 V'>★ {r['hc_v']}</span>"
            if r["hc_v"] > 0 else ""
        )
        html += (
            f"<div class='pl-row' data-target='tab-{r['pid']}' data-search='{r['pid']} {r['dx']}'>"
            f"<div class='pl-head'>"
            f"<span class='pl-pid'>{r['pid']}</span>"
            f"{v_badge}"
            f"<span class='pl-i'>I {r['i']}</span>"
            f"<span class='pl-c'>C {r['c']}</span>"
            f"{hc_marker}"
            f"</div>"
            f"<div class='pl-dx'>{r['dx']}</div>"
            f"<div class='pl-meta'>{r['fees']} fees · ¥{r['sum_amt']:,.0f}</div>"
            f"</div>"
        )
    html += "</div>"
    return html


def build_patient_detail(p: dict, rules: dict, zd: dict, ss: dict) -> str:
    """复用 clerk.render_patient_tab 渲染单 patient 详情."""
    raw = clerk.render_patient_tab(
        p["patient_id"], p["basics_entry"], p["runs"], rules,
        {p["patient_id"]: p["zd_entry"]}, {p["patient_id"]: p["ss_entry"]},
    )
    # 加 quality 标记到每个 rule card 顶部 — 通过 rule_id mapping inject
    # (简单方式: 在最后用一个 inline JS data + style 加颜色边框)
    # 这里偷懒, 不改 raw, 让用户从 rule_card 自身的 verdict 判断
    return raw


def build_html(
    patients_data: list[dict], rules: dict, zd: dict, ss: dict,
    cutoff_start: str, cutoff_end: str,
) -> str:
    global_stats, _, q_counts = build_summary_table(patients_data)
    sidebar = build_patient_list_sidebar(patients_data)

    # patient detail tabs (默认全部隐藏, 点击 sidebar 后激活)
    tabs_html = ""
    for p in patients_data:
        tabs_html += build_patient_detail(p, rules, zd, ss)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    q_parts = []
    for k, disp in LABEL_DISPLAY.items():
        bg = disp["bg"]
        color = disp["color"]
        label = disp["label"]
        n = q_counts.get(k, 0)
        q_parts.append(
            f"<div class='q-card' style='background:{bg};border-color:{color}'>"
            f"<div class='q-num' style='color:{color}'>{n}</div>"
            f"<div class='q-lab'>{label}</div></div>"
        )
    q_summary_rows = "".join(q_parts)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>50 病人 router v2 审计报告 · ready 全集</title>
<style>
  * {{ box-sizing: border-box }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; background: #f3f4f6; color: #1f2937; font-size: 14px }}
  header {{ background: linear-gradient(135deg, #6d28d9, #3b82f6);
           color: white; padding: 18px 24px }}
  header h1 {{ margin: 0; font-size: 20px }}
  header .sub {{ opacity: 0.92; margin-top: 4px; font-size: 13px }}
  .global-stats {{ display: flex; gap: 12px; margin-top: 14px; flex-wrap: wrap }}
  .gs {{ background: rgba(255,255,255,0.15); padding: 8px 14px; border-radius: 6px }}
  .gs .v {{ font-weight: bold; font-size: 16px }}

  /* Quality assessment bar */
  .q-bar {{ background: white; padding: 12px 20px; display: flex; gap: 10px;
            border-bottom: 1px solid #e5e7eb; overflow-x: auto }}
  .q-card {{ padding: 10px 16px; border: 2px solid; border-radius: 6px;
              text-align: center; flex: 1; min-width: 100px }}
  .q-num {{ font-size: 22px; font-weight: bold }}
  .q-lab {{ font-size: 11px; color: #6b7280; margin-top: 2px }}

  /* Layout: sidebar + content */
  .body-layout {{ display: flex; min-height: calc(100vh - 200px) }}
  .sidebar {{ width: 280px; flex-shrink: 0; background: white;
               border-right: 1px solid #e5e7eb; overflow-y: auto;
               position: sticky; top: 0; height: 100vh }}
  .sidebar-search {{ padding: 10px; border-bottom: 1px solid #e5e7eb;
                      background: #f9fafb }}
  .sidebar-search input {{ width: 100%; padding: 6px 10px; border: 1px solid #d1d5db;
                            border-radius: 4px; font-size: 13px }}
  .sidebar-list {{ padding: 4px 0 }}
  .pl-row {{ padding: 8px 12px; border-bottom: 1px solid #f3f4f6;
              cursor: pointer; transition: background 0.1s }}
  .pl-row:hover {{ background: #f9fafb }}
  .pl-row.active {{ background: #ede9fe; border-left: 4px solid #6d28d9 }}
  .pl-head {{ display: flex; gap: 6px; align-items: center; font-size: 12px }}
  .pl-pid {{ font-family: monospace; font-weight: bold; font-size: 13px; color: #1f2937 }}
  .pl-v {{ font-weight: bold }}
  .pl-i {{ color: #d97706 }}
  .pl-c {{ color: #6b7280 }}
  .pl-hc {{ color: #f59e0b; font-weight: bold; margin-left: auto }}
  .pl-dx {{ font-size: 11px; color: #6b7280; margin: 2px 0 }}
  .pl-meta {{ font-size: 11px; color: #9ca3af }}
  .content {{ flex: 1; padding: 20px 24px; overflow-x: hidden }}
  .placeholder {{ text-align: center; color: #9ca3af; margin-top: 80px;
                   font-size: 14px }}

  /* Patient tab (复用 clerk_report 样式) */
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
  <h1>50 病人 router v2 审计报告 · ready 全集</h1>
  <div class="sub">生成 {now} · 时间窗 {cutoff_start} → {cutoff_end} · 单闸 + 弹性 keyword router</div>
  {global_stats}
</header>

<div class="q-bar">{q_summary_rows}</div>

<div class="body-layout">
  <aside class="sidebar">{sidebar}</aside>
  <main class="content">
    <div class="placeholder" id="placeholder">← 左侧选病人查看详情</div>
    {tabs_html}
  </main>
</div>

<script>
  const rows = document.querySelectorAll('.pl-row');
  const tabs = document.querySelectorAll('.patient-tab');
  const placeholder = document.getElementById('placeholder');

  function activate(target) {{
    rows.forEach(r => r.classList.toggle('active', r.dataset.target === target));
    let any = false;
    tabs.forEach(t => {{
      const on = t.id === target;
      t.classList.toggle('active', on);
      if (on) any = true;
    }});
    placeholder.style.display = any ? 'none' : 'block';
  }}
  rows.forEach(r => r.addEventListener('click', () => activate(r.dataset.target)));

  // sidebar filter
  const filter = document.getElementById('sidebar-filter');
  filter.addEventListener('input', () => {{
    const q = filter.value.trim().toLowerCase();
    rows.forEach(r => {{
      const s = r.dataset.search.toLowerCase();
      r.style.display = q === '' || s.includes(q) ? '' : 'none';
    }});
  }});

  // 默认: 第一个 (V 最多的)
  if (rows.length > 0) {{
    activate(rows[0].dataset.target);
  }}
</script>
</body>
</html>"""


@click.command()
@click.option("--cutoff-start", required=True, help='本次 batch 跑前的 max(created_at), e.g. "2026-05-20 13:00:00"')
@click.option("--cutoff-end",   required=True, help='本次 batch 跑后任一未来值, e.g. "2099-01-01 00:00:00"')
def main(cutoff_start, cutoff_end):
    pids = load_patients_list()
    print(f"loading {len(pids)} patients ...")

    # monkey-patch PATIENTS for clerk.load_*
    clerk.PATIENTS = pids

    rules = clerk.load_rules()
    ss_all = clerk.load_shi_ss_surgeries()
    zd_all = clerk.load_shi_zd_diagnoses()
    basics_all = clerk.load_patient_basics()

    patients_data: list[dict] = []
    n_ok = 0
    for pid in pids:
        runs = load_runs_in_window(pid, cutoff_start, cutoff_end)
        # quality 评估
        q = evaluate_batch(runs)
        rows_q = q["rows"]
        patients_data.append({
            "patient_id": pid,
            "runs": runs,
            "quality_rows": rows_q,
            "basics_entry": basics_all.get(pid, {
                "diagnoses": {}, "notes_total": 0, "fees_count": 0,
                "fees_sum": 0.0, "departments": {}, "doctors": {},
                "notes_stages": {}, "fee_items": {}, "drugs_materials": {},
                "fee_categories": {}, "admit_date": "?", "discharge_date": "?",
                "los_days": 0, "fields": {},
            }),
            "zd_entry": zd_all.get(pid, {"main": [], "others": []}),
            "ss_entry": ss_all.get(pid, []),
        })
        if runs:
            n_ok += 1

    print(f"  loaded data for {n_ok}/{len(pids)} patients with runs in window")

    html = build_html(patients_data, rules, zd_all, ss_all, cutoff_start, cutoff_end)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"\nwritten: {OUT_HTML}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
