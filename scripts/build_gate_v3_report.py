# -*- coding: utf-8 -*-
"""build_gate_v3_report — gate-v3-fresh 批次自包含验证报告 (HTML).

直接按 batch_tag 取数 (不靠 cutoff 窗), 重点证明: 之前被专家驳回的麻醉/术前/肿瘤/影像
规则在全新病人上 V≈0; 列出残余 V 供专家核对 (应集中在药品/查房症状类, 非本次修复目标).

用法 (62, 全量基础数据 env):
  set -a && source .env && set +a
  export JAVERT_SS_FILE=shi_ss.xls JAVERT_ZD_FILE=shi_zd.xls
  uv run python scripts/build_gate_v3_report.py
输出: output/gate_v3_report.html
"""
from __future__ import annotations

import html
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from javert.config import get_config
from javert.data.clinical_context import build_clinical_context

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "output" / "audit.sqlite"
OUT = ROOT / "output" / "gate_v3_report.html"
TAG = "gate-v3-fresh"
PREV_REJECTED = ["R203", "R205", "R131", "R153", "R154", "R156", "R103", "R105"]
PREV_LABEL = {
    "R203": "麻醉·虚构全麻", "R205": "麻醉·全麻超次", "R131": "术前心脏彩超",
    "R153": "肿瘤标志物AFP", "R154": "肿瘤标志物", "R156": "肿瘤标志物/PSA",
    "R103": "影像虚构", "R105": "影像虚构",
}
VC = {"VIOLATION": "V", "INCONCLUSIVE": "I", "CLEAN": "C"}
COLOR = {"VIOLATION": "#b91c1c", "INCONCLUSIVE": "#b45309", "CLEAN": "#047857"}


def esc(s) -> str:
    return html.escape(str(s or ""))


def main() -> None:
    cfg = get_config()
    con = sqlite3.connect(DB)
    df = pd.read_sql(
        "select patient_id, rule_id, verdict, gate_tag, confidence, reasoning "
        "from audit_runs where batch_tag=?", con, params=[TAG])
    con.close()
    if df.empty:
        print("无 gate-v3-fresh 数据"); return

    pats = sorted(df["patient_id"].unique())
    vc = Counter(df["verdict"])
    gt = Counter(df[df["gate_tag"].fillna("") != ""]["gate_tag"])

    # 核心证明表
    proof_rows = ""
    for rid in PREV_REJECTED:
        r = df[df["rule_id"] == rid]
        if r.empty:
            continue
        c = Counter(r["verdict"])
        v = c.get("VIOLATION", 0)
        mark = '<b style="color:#047857">0 ✓</b>' if v == 0 else f'<b style="color:#b91c1c">{v} ⚠️</b>'
        proof_rows += (f"<tr><td>{rid}</td><td>{PREV_LABEL.get(rid,'')}</td>"
                       f"<td>{mark}</td><td>{c.get('INCONCLUSIVE',0)}</td>"
                       f"<td>{c.get('CLEAN',0)}</td></tr>")

    # 残余 V 明细 (按规则分组)
    vdf = df[df["verdict"] == "VIOLATION"]
    vrule = vdf["rule_id"].value_counts()
    vrows = ""
    for rid, n in vrule.items():
        ex = vdf[vdf["rule_id"] == rid].iloc[0]["reasoning"] or ""
        ex = ex.replace("\n", " ")[:160]
        vrows += (f"<tr><td>{rid}</td><td>{n}</td><td style='font-size:12px;color:#555'>{esc(ex)}</td></tr>")

    # 每患者卡片 (手术麻醉 + 肿瘤诊断 + 裁决分布)
    cards = ""
    for p in pats:
        ctx = build_clinical_context(p, cfg.ss_path, cfg.zd_path)
        pr = df[df["patient_id"] == p]
        pc = Counter(pr["verdict"])
        sgs = "; ".join(
            f"{s.name}[{'全麻' if s.has_general_anesthesia else 'anst'+s.anst_way}·{s.anst_dr}]"
            for s in ctx.surgeries[:5] if s.name)
        tumor = "、".join(ctx.tumor_diagnoses()[:3])
        ptags = Counter(pr[pr["gate_tag"].fillna("") != ""]["gate_tag"])
        tagstr = " ".join(f"<span class='tag'>{esc(k)}×{v}</span>" for k, v in ptags.items())
        badge = (f"<span style='color:{COLOR['VIOLATION']}'>V {pc.get('VIOLATION',0)}</span> · "
                 f"<span style='color:{COLOR['INCONCLUSIVE']}'>I {pc.get('INCONCLUSIVE',0)}</span> · "
                 f"<span style='color:{COLOR['CLEAN']}'>C {pc.get('CLEAN',0)}</span>")
        cards += (
            f"<div class='card'><div class='ph'><b>{p}</b> &nbsp; {badge}</div>"
            f"<div class='meta'>手术麻醉: {esc(sgs) or '—'}</div>"
            f"<div class='meta'>肿瘤诊断: {esc(tumor) or '—'}</div>"
            f"<div class='meta'>闸触发: {tagstr or '—'}</div></div>")

    gtstr = " ".join(f"<span class='tag'>{esc(k)}×{v}</span>" for k, v in gt.items())
    now = time.strftime("%Y-%m-%d %H:%M")
    doc = f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<title>gate-v3 全新病人验证报告</title><style>
body{{font-family:-apple-system,'PingFang SC',sans-serif;margin:24px;color:#1f2937;background:#f8fafc}}
h1{{color:#1e40af}} .sub{{color:#64748b;font-size:13px;margin-bottom:18px}}
table{{border-collapse:collapse;background:#fff;margin:8px 0 20px}} td,th{{border:1px solid #e2e8f0;padding:6px 12px;font-size:14px}}
th{{background:#1e40af;color:#fff}} .card{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin:8px 0}}
.ph{{font-size:15px;margin-bottom:6px}} .meta{{font-size:13px;color:#475569;margin:2px 0}}
.tag{{background:#dbeafe;color:#1e40af;border-radius:4px;padding:1px 7px;font-size:12px;margin-right:4px}}
.big{{font-size:22px;font-weight:700}} .ok{{color:#047857}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}</style></head><body>
<h1>gate-v3 确定性闸 — 全新病人验证报告</h1>
<div class=sub>生成 {now} · batch_tag={TAG} · {len(pats)} 全新病人 · {len(df)} 裁决 · 麻醉/术前/肿瘤/缺文书确定性闸</div>
<p class=big>总裁决 V=<span style='color:{COLOR['VIOLATION']}'>{vc.get('VIOLATION',0)}</span>
 I=<span style='color:{COLOR['INCONCLUSIVE']}'>{vc.get('INCONCLUSIVE',0)}</span>
 C=<span style='color:{COLOR['CLEAN']}'>{vc.get('CLEAN',0)}</span></p>
<p>闸触发分布: {gtstr or '(LLM 多自判对, 闸少触发)'}</p>

<h2>① 核心证明 — 之前被专家驳回的规则在全新病人上的 V 数 (目标 0)</h2>
<table><tr><th>规则</th><th>类型</th><th>V (新病人)</th><th>I</th><th>C</th></tr>{proof_rows}</table>

<h2>② 残余 V (按规则) — 应集中在药品/查房症状类, 非本次修复目标</h2>
<table><tr><th>规则</th><th>V 数</th><th>裁决理由摘要 (首条)</th></tr>{vrows or '<tr><td colspan=3>无 V</td></tr>'}</table>

<h2>③ 每患者明细 (手术麻醉 ground truth + 肿瘤诊断 + 闸触发)</h2>
<div class=grid>{cards}</div>
</body></html>"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"写出 {OUT} ({len(pats)} 患者, {len(df)} 裁决, V={vc.get('VIOLATION',0)})")


if __name__ == "__main__":
    main()
