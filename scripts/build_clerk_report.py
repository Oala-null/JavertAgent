"""病案资料员视角 10-tab HTML 汇总报告 v2.

修复:
1. 使用 csv.reader 正确解析费用 CSV (含引号内逗号)
2. Top 10 收费项目用 medins_list_name (真实费用名)
3. 病人基本信息扩充: 入/出院日期 + 住院天数 + 主治 + 科室 + 主诉 + 现病史 + 拟施手术 + 病理诊断
4. 按医保项目类别 (medins_chrgitm_type) 出费用结构图
"""

from __future__ import annotations
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import xlrd
import yaml

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "output" / "audit.sqlite"
NOTES_CSV = ROOT / "data" / "case_notes.csv"
FEES_CSV = ROOT / "data" / "shi_fee.csv"
ZD_XLS = ROOT / "data" / "shi_zd.xls"  # 病案首页主诊断 ground truth
SS_XLS = ROOT / "data" / "shi_ss.xls"  # 病案首页手术 ground truth (ICD-9)
RULES_DIR = ROOT / "configs" / "rules"
OUT_HTML = ROOT / "output" / "clerk_report_v0_4.html"

PATIENTS = [
    "J66252", "J18906", "J13365", "K33745", "J24278",
    "J19333", "J90508", "J61556", "J40485", "K03341",
]

# Fee CSV columns (0-indexed)
COL_BAH = 0
COL_TIME = 2
COL_CNT = 3
COL_PRIC = 4
COL_AMT = 5
COL_ITEM_NAME = 16  # medins_list_name (真实名)
COL_MED_CHRGITM_TYPE = 17  # med_chrgitm_type (数字大类码)
COL_PRODNAME = 18  # 药品/耗材规格名
COL_SPEC = 19  # 规格
COL_DEPT_CODE = 21
COL_DEPT_NAME = 22
COL_DR_NAME = 24
COL_MEDINS_CHRGITM_TYPE = 31  # medins_chrgitm_type (中文标签: 化验/西药/手术/麻醉/...)


def load_rules() -> dict[str, dict]:
    rules = {}
    for f in sorted(RULES_DIR.glob("R*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            rid = data.get("rule_id") or f.stem
            rules[rid] = {
                "rule_id": rid,
                "title": data.get("title", ""),
                "domain": data.get("domain", ""),
                "violation_type": data.get("violation_type", ""),
                "priority": data.get("priority", ""),
                "derived_from_template": data.get("derived_from_template", ""),
            }
        except Exception as e:
            print(f"WARN load {f.name}: {e}", file=sys.stderr)
    return rules


def load_shi_ss_surgeries() -> dict[str, list[dict]]:
    """从 shi_ss.xls 病案首页手术表读取真实手术列表 (ICD-9 ground truth)."""
    out: dict[str, list[dict]] = {pid: [] for pid in PATIENTS}
    if not SS_XLS.exists():
        print("WARN: shi_ss.xls 不存在, 跳过手术 ground truth", file=sys.stderr)
        return out
    wb = xlrd.open_workbook(SS_XLS)
    s = wb.sheet_by_index(0)
    # col 列定义:
    # 1=oprn_oprt_name, 2=oprn_oprt_code (临床版 ICD9-CM3 上海版)
    # 3=hi_oprn_oprt_name, 4=hi_oprn_oprt_code (医保版)
    # 6=oprn_lv_code, 7=oprn_lv_name (手术等级)
    # 12=anst_mtd_name (麻醉方式)
    # 14=anst_dr_name (麻醉医师)
    # 15=oprn_oper_part (手术部位)
    # 20=main_oprn_flag
    # 28=oper_dr_name (主刀)
    # 32=ba_id (即 H...-{pid})
    # 33=oprn_oprt_begntime (手术日期)
    for r in range(1, s.nrows):
        ba_id = str(s.cell_value(r, 32))
        target = None
        for pid in PATIENTS:
            if f"-{pid}" in ba_id:
                target = pid
                break
        if target is None:
            continue
        try:
            main = int(s.cell_value(r, 20)) if s.cell_value(r, 20) != "" else 0
        except (ValueError, TypeError):
            main = 0
        name = (s.cell_value(r, 1) or "").strip()
        code = (s.cell_value(r, 2) or "").strip()
        hi_name = (s.cell_value(r, 3) or "").strip()
        hi_code = (s.cell_value(r, 4) or "").strip()
        lv = (s.cell_value(r, 7) or "").strip()
        anst = (s.cell_value(r, 12) or "").strip()
        anst_dr = (s.cell_value(r, 14) or "").strip()
        part = (s.cell_value(r, 15) or "").strip()
        oper_dr = (s.cell_value(r, 28) or "").strip()
        date = s.cell_value(r, 33)
        if isinstance(date, float):
            try:
                from xlrd.xldate import xldate_as_datetime
                date = xldate_as_datetime(date, wb.datemode).strftime("%Y-%m-%d")
            except Exception:
                date = str(date)
        else:
            date = str(date)[:10]
        out[target].append({
            "main": main,
            "name": name,
            "code": code,
            "hi_name": hi_name,
            "hi_code": hi_code,
            "level": lv,
            "anesthesia": anst,
            "anst_dr": anst_dr,
            "part": part,
            "oper_dr": oper_dr,
            "date": date,
        })
    for pid in PATIENTS:
        out[pid].sort(key=lambda x: (0 if x["main"] == 1 else 1, x["date"]))
    return out


def load_shi_zd_diagnoses() -> dict[str, dict]:
    """从 shi_zd.xls 病案首页诊断表读取真实主诊断 + 其他诊断 (ground truth)."""
    out: dict[str, dict] = {pid: {"main": [], "others": []} for pid in PATIENTS}
    if not ZD_XLS.exists():
        print(f"WARN: shi_zd.xls 不存在, 跳过 ground truth 注入", file=sys.stderr)
        return out
    wb = xlrd.open_workbook(ZD_XLS)
    s = wb.sheet_by_index(0)
    # 列定义: col[1]=maindiag_flag col[3]=ipt_medcas_hmpg_sn (诊断次序)
    # col[4]=inhosp_diag_name col[5]=inhosp_diag_code col[20]=ba_id
    for r in range(1, s.nrows):
        ba_id = str(s.cell_value(r, 20))
        target = None
        for pid in PATIENTS:
            if f"-{pid}" in ba_id:
                target = pid
                break
        if target is None:
            continue
        try:
            main_flag = int(s.cell_value(r, 1)) if s.cell_value(r, 1) != "" else 0
        except (ValueError, TypeError):
            main_flag = 0
        try:
            order = int(s.cell_value(r, 3))
        except (ValueError, TypeError):
            order = 999
        name = (s.cell_value(r, 4) or "").strip()
        code = (s.cell_value(r, 5) or "").strip()
        if not name:
            continue
        entry = {"name": name, "code": code, "order": order}
        if main_flag == 1:
            out[target]["main"].append(entry)
        else:
            out[target]["others"].append(entry)
    for pid in PATIENTS:
        out[pid]["others"].sort(key=lambda x: x["order"])
    return out


def load_patient_basics() -> dict[str, dict]:
    out: dict[str, dict] = {pid: {
        "diagnoses": defaultdict(list),
        "notes_total": 0,
        "fees_count": 0,
        "fees_sum": 0.0,
        "departments": defaultdict(int),
        "doctors": defaultdict(int),
        "notes_stages": defaultdict(int),
        "fee_items": defaultdict(lambda: {"cnt": 0, "sum": 0.0}),
        "drugs_materials": defaultdict(lambda: {"cnt": 0, "sum": 0.0, "spec": ""}),
        "fee_categories": defaultdict(lambda: {"cnt": 0, "sum": 0.0}),
        "dates": [],
        "fields": {},
    } for pid in PATIENTS}

    KEY_NOTES = {
        "主诉": "chief_complaint",
        "现病史": "history",
        "既往史": "past_history",
        "性别": "gender",
        "年龄": "age",
        "婚姻": "marital",
        "拟施手术名称": "planned_surgery",
        "拟定手术名称": "planned_surgery2",
        "手术名称": "actual_surgery",
        "术后诊断": "post_dx",
        "病理诊断": "pathology",
        "麻醉方法": "anesthesia_method",
        "麻醉分级（ASA分级）": "asa",
        "主刀医师术前查看患者相关情况": "preop_remark",
    }

    with open(NOTES_CSV, encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            pid = (r.get("住院号") or r.get("﻿住院号") or "").strip()
            if pid not in out:
                continue
            out[pid]["notes_total"] += 1
            sub = r.get("子阶段", "")
            stage = r.get("阶段", "")
            out[pid]["notes_stages"][stage] += 1
            content = (r.get("内容", "") or "").strip()
            if sub in ("出院诊断", "入院诊断", "临床诊断", "主要诊断", "其他诊断", "术后诊断"):
                out[pid]["diagnoses"][sub].append(content[:400])
            if sub in KEY_NOTES:
                fk = KEY_NOTES[sub]
                if fk not in out[pid]["fields"] or len(content) > len(out[pid]["fields"].get(fk, "")):
                    out[pid]["fields"][fk] = content[:800]

    with open(FEES_CSV, encoding="utf-8") as f:
        rdr = csv.reader(f)
        next(rdr)
        for row in rdr:
            if not row or len(row) <= COL_AMT:
                continue
            bah = row[COL_BAH]
            target = None
            for pid in PATIENTS:
                if f"-{pid}" in bah:
                    target = pid
                    break
            if target is None:
                continue

            try:
                amt = float(row[COL_AMT])
                cnt = float(row[COL_CNT])
                pric = float(row[COL_PRIC])
            except (ValueError, IndexError):
                amt = cnt = pric = 0.0

            out[target]["fees_count"] += 1
            out[target]["fees_sum"] += amt

            try:
                d = datetime.strptime(row[COL_TIME].split()[0], "%d/%m/%Y")
                out[target]["dates"].append(d)
            except (ValueError, IndexError):
                pass

            if len(row) > COL_DEPT_NAME:
                dn = row[COL_DEPT_NAME].strip()
                if dn:
                    out[target]["departments"][dn] += 1
            if len(row) > COL_DR_NAME:
                dr = row[COL_DR_NAME].strip()
                if dr:
                    out[target]["doctors"][dr] += 1

            item_name = ""
            if len(row) > COL_ITEM_NAME:
                item_name = row[COL_ITEM_NAME].strip()
            prod_name = ""
            if len(row) > COL_PRODNAME:
                prod_name = row[COL_PRODNAME].strip()
            spec = ""
            if len(row) > COL_SPEC:
                spec = row[COL_SPEC].strip()

            display = item_name or prod_name or "(未命名)"

            if prod_name and prod_name != item_name:
                key = (prod_name, spec)
                out[target]["drugs_materials"][key]["cnt"] += cnt
                out[target]["drugs_materials"][key]["sum"] += amt
                out[target]["drugs_materials"][key]["spec"] = spec

            spec_tag = spec if spec else "-"
            item_key = (display, spec_tag)
            cur = out[target]["fee_items"][item_key]
            cur["cnt"] += cnt
            cur["sum"] += amt
            cur["unit_prices"] = cur.get("unit_prices", set())
            cur["unit_prices"].add(round(pric, 2))

            cat_label = "未分类"
            if len(row) > COL_MEDINS_CHRGITM_TYPE:
                v = row[COL_MEDINS_CHRGITM_TYPE].strip()
                if v:
                    cat_label = v
            out[target]["fee_categories"][cat_label]["cnt"] += 1
            out[target]["fee_categories"][cat_label]["sum"] += amt

    for pid, info in out.items():
        ds = info["dates"]
        if ds:
            info["admit_date"] = min(ds).strftime("%Y-%m-%d")
            info["discharge_date"] = max(ds).strftime("%Y-%m-%d")
            info["los_days"] = (max(ds) - min(ds)).days + 1
        else:
            info["admit_date"] = info["discharge_date"] = "?"
            info["los_days"] = 0
        del info["dates"]
    return out


def load_audit_runs() -> dict[str, list[dict]]:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""
        SELECT run_id, rule_id, patient_id, verdict, confidence,
               reasoning, evidence_json, duration_ms, created_at
        FROM audit_runs
        WHERE patient_id IN ({})
        ORDER BY created_at ASC
    """.format(",".join(f"'{p}'" for p in PATIENTS)))

    latest: dict[tuple[str, str], dict] = {}
    for row in cur.fetchall():
        run_id, rid, pid, verdict, conf, reasoning, ev_json, dur, created = row
        key = (pid, rid)
        latest[key] = {
            "run_id": run_id,
            "rule_id": rid,
            "patient_id": pid,
            "verdict": verdict,
            "confidence": conf,
            "reasoning": reasoning or "",
            "evidence": json.loads(ev_json) if ev_json else [],
            "duration_ms": dur,
            "created_at": created,
        }
    con.close()

    out: dict[str, list[dict]] = {pid: [] for pid in PATIENTS}
    for (pid, _), run in latest.items():
        out[pid].append(run)
    for pid in out:
        out[pid].sort(key=lambda r: (
            0 if r["verdict"] == "VIOLATION" else (1 if r["verdict"] == "INCONCLUSIVE" else 2),
            -float(r["confidence"] or 0),
            r["rule_id"],
        ))
    return out


def esc(s) -> str:
    if s is None:
        return ""
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


VERDICT_COLOR = {"VIOLATION": "#dc2626", "INCONCLUSIVE": "#d97706", "CLEAN": "#16a34a"}
VERDICT_LABEL = {"VIOLATION": "违规 V", "INCONCLUSIVE": "存疑 I", "CLEAN": "合规 C"}


def render_patient_tab(pid: str, b: dict, runs: list[dict], rules: dict, zd: dict, ss: dict) -> str:
    zd_entry = zd.get(pid, {"main": [], "others": []})
    ss_entry = ss.get(pid, [])
    diags = b.get("diagnoses", {})

    # 真实主诊断: shi_zd.xls (病案首页 ground truth) 优先; 病历文书 fallback
    if zd_entry["main"]:
        m = zd_entry["main"][0]
        primary = f"{m['name']} ({m['code']})"
        primary_source = "病案首页 shi_zd"
    else:
        primary = (diags.get("出院诊断") or diags.get("主要诊断") or diags.get("入院诊断")
                   or diags.get("临床诊断") or ["(无)"])[0]
        primary_source = "病历文书 (无首页 ground truth)"

    if zd_entry["others"]:
        other_dx = [f"{o['name']} ({o['code']})" for o in zd_entry["others"]]
    else:
        other_dx = diags.get("其他诊断", [])
    post_dx = diags.get("术后诊断", [])

    f = b.get("fields", {})
    chief = f.get("chief_complaint", "")
    hx = f.get("history", "")
    past = f.get("past_history", "")
    # surg: ground truth from shi_ss.xls (优先), notes fallback
    surg_notes_text = f.get("actual_surgery") or f.get("planned_surgery") or f.get("planned_surgery2") or ""
    path = f.get("pathology", "")
    asa = f.get("asa", "")
    anesthesia = f.get("anesthesia_method", "")
    gender = f.get("gender", "")
    age = f.get("age", "")

    counts = {"VIOLATION": 0, "CLEAN": 0, "INCONCLUSIVE": 0}
    for r in runs:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    total_runs = len(runs)

    top_depts = sorted(b["departments"].items(), key=lambda kv: -kv[1])[:5]
    top_drs = sorted(b["doctors"].items(), key=lambda kv: -kv[1])[:5]
    top_stages = sorted(b["notes_stages"].items(), key=lambda kv: -kv[1])[:8]
    top_items = sorted(b["fee_items"].items(), key=lambda kv: -kv[1]["sum"])[:15]
    top_drugs = sorted(b["drugs_materials"].items(), key=lambda kv: -kv[1]["sum"])[:8]
    fee_cats = sorted(b["fee_categories"].items(), key=lambda kv: -kv[1]["sum"])

    violations = [r for r in runs if r["verdict"] == "VIOLATION"]
    inconclusives = [r for r in runs if r["verdict"] == "INCONCLUSIVE"]
    cleans = [r for r in runs if r["verdict"] == "CLEAN"]

    def rule_card(run: dict) -> str:
        rid = run["rule_id"]
        meta = rules.get(rid, {})
        verdict = run["verdict"]
        color = VERDICT_COLOR[verdict]
        ev_html = "<ol class='ev'>"
        for ev in run["evidence"][:8]:
            src = esc(ev.get("source", "?"))
            loc = esc(ev.get("locator", ""))
            txt = esc(ev.get("text", ""))[:500]
            ev_html += f"<li><b>[{src}]</b> <span class='loc'>{loc}</span><br>{txt}</li>"
        ev_html += "</ol>"
        if not run["evidence"]:
            ev_html = "<i class='muted'>(无 evidence_json)</i>"
        title = esc(meta.get("title", ""))[:100]
        return f"""
        <div class="rule-card" style="border-left-color:{color}">
          <div class="rule-head">
            <span class="verdict-badge" style="background:{color}">
              {VERDICT_LABEL[verdict]} · conf={run['confidence']:.2f}
            </span>
            <span class="rule-id"><b>{rid}</b></span>
            <span class="rule-title">{title}</span>
            <span class="rule-meta">{esc(meta.get('domain',''))} · {esc(meta.get('violation_type',''))[:14]} · {esc(meta.get('priority',''))} · 模板{esc(meta.get('derived_from_template') or '-')}</span>
          </div>
          <div class="reasoning"><b>判定理由:</b> {esc(run['reasoning'])[:1000]}</div>
          <details><summary>查看证据 ({len(run['evidence'])} 条)</summary>{ev_html}</details>
        </div>
        """

    # Build basic-info block
    other_dx_html = ""
    if other_dx:
        other_dx_html = "<ul class='dx-list'>" + "".join(
            f"<li>{esc(d)}</li>" for d in other_dx[:10]) + "</ul>"
    post_dx_html = ""
    if post_dx:
        post_dx_html = "<ul class='dx-list'>" + "".join(
            f"<li>{esc(d)}</li>" for d in post_dx[:5]) + "</ul>"

    dept_str = "、".join(f"{esc(c)}({n})" for c, n in top_depts) or "(无)"
    dr_str = "、".join(f"{esc(c)}({n})" for c, n in top_drs) or "(无)"

    basics_html = f"""
    <table class="meta-table">
      <colgroup><col style="width:14%"><col style="width:36%"><col style="width:14%"><col style="width:36%"></colgroup>
      <tr><th>住院号</th><td><b>{esc(pid)}</b></td>
          <th>性别·年龄</th><td>{esc(gender) or '—'} · {esc(age) or '—'}</td></tr>
      <tr><th>入院日期</th><td>{esc(b['admit_date'])}</td>
          <th>出院日期</th><td>{esc(b['discharge_date'])}</td></tr>
      <tr><th>住院天数</th><td>{b['los_days']} 天</td>
          <th>文书 / 费用条数</th><td>{b['notes_total']} 条文书 · {b['fees_count']} 条费用</td></tr>
      <tr><th>费用合计</th><td><b style='color:#dc2626'>¥{b['fees_sum']:,.2f}</b></td>
          <th>麻醉 / ASA</th><td>{esc(anesthesia) or '—'} / ASA {esc(asa) or '—'}</td></tr>
      <tr><th>收治科室</th><td colspan="3">{dept_str}</td></tr>
      <tr><th>经治医师</th><td colspan="3">{dr_str}</td></tr>
      <tr><th>主诊断</th><td colspan="3"><b>{esc(primary)}</b> <span class='muted'>· 来源: {esc(primary_source)}</span></td></tr>
    </table>
    """

    if other_dx_html:
        basics_html += f"<div class='subblock'><b>其他诊断 (Top {min(len(other_dx),10)})</b>{other_dx_html}</div>"
    if post_dx_html:
        basics_html += f"<div class='subblock'><b>术后诊断</b>{post_dx_html}</div>"

    chief_html = ""
    if chief:
        chief_html = f"<div class='subblock'><b>主诉</b><div class='note-box'>{esc(chief)[:300]}</div></div>"
    hx_html = ""
    if hx:
        hx_html = f"<div class='subblock'><b>现病史</b><div class='note-box'>{esc(hx)[:600]}</div></div>"
    surg_html = ""
    if ss_entry:
        rows = ""
        for o in ss_entry:
            tag = "主" if o["main"] == 1 else "次"
            tag_color = "#dc2626" if o["main"] == 1 else "#6b7280"
            extra = []
            if o.get("level"): extra.append(esc(o["level"]))
            if o.get("anesthesia"): extra.append(f"麻醉:{esc(o['anesthesia'])}")
            if o.get("oper_dr"): extra.append(f"主刀:{esc(o['oper_dr'])}")
            if o.get("anst_dr"): extra.append(f"麻醉医师:{esc(o['anst_dr'])}")
            meta = " · ".join(extra)
            rows += (
                f"<tr><td><span style='color:{tag_color};font-weight:bold'>{tag}</span></td>"
                f"<td>{esc(o['name'])}</td>"
                f"<td><code>{esc(o['code'])}</code></td>"
                f"<td><code>{esc(o['hi_code'])}</code></td>"
                f"<td>{esc(o['date'])}</td>"
                f"<td>{meta}</td></tr>"
            )
        surg_html = f"""
        <div class='subblock'><b>手术列表 <span class='muted'>· 来源: 病案首页 shi_ss (ICD-9-CM3)</span></b>
          <table class='data-table'>
            <thead><tr><th>主/次</th><th>手术名称 (临床版)</th><th>ICD-9 临床版</th><th>ICD-9 医保版</th><th>日期</th><th>等级/麻醉/术者</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        """
    elif surg_notes_text:
        surg_html = (f"<div class='subblock'><b>手术名称 <span class='muted'>· 来源: 病历文书 (无 shi_ss ground truth)</span></b>"
                     f"<div class='note-box'>{esc(surg_notes_text)[:300]}</div></div>")
    path_html = ""
    if path:
        path_html = f"<div class='subblock'><b>病理诊断</b><div class='note-box'>{esc(path)[:300]}</div></div>"
    past_html = ""
    if past:
        past_html = f"<div class='subblock'><b>既往史</b><div class='note-box'>{esc(past)[:300]}</div></div>"

    # Fee items table (key=(name,spec) to避免不同规格被错误聚合)
    fee_rows = ""
    for i, ((name, spec), v) in enumerate(top_items):
        prices = sorted(v.get("unit_prices", {0}))
        if len(prices) == 1:
            price_html = f"¥{prices[0]:,.2f}"
        else:
            price_html = f"¥{prices[0]:,.2f}-{prices[-1]:,.2f}"
        spec_display = "" if spec in ("", "-") else f"<div class='spec-tag'>规格: {esc(spec)[:30]}</div>"
        fee_rows += (
            f"<tr><td>{i+1}</td>"
            f"<td>{esc(name)[:55]}{spec_display}</td>"
            f"<td class='num'>{v['cnt']:.1f}</td>"
            f"<td class='num'>{price_html}</td>"
            f"<td class='num'><b>¥{v['sum']:,.2f}</b></td>"
            f"<td class='num'>{v['sum']/b['fees_sum']*100:.1f}%</td></tr>"
        )
    fees_block = f"""
    <details class="subblock" open>
      <summary><b>费用项目 Top 15 (按金额倒序; 同名不同规格分行)</b></summary>
      <table class="data-table">
        <thead><tr><th>#</th><th>费用项目名 (medins_list_name)</th><th>次数</th><th>单价</th><th>小计</th><th>占比</th></tr></thead>
        <tbody>{fee_rows}</tbody>
      </table>
    </details>
    """

    drugs_html = ""
    if top_drugs:
        drug_rows = "".join(
            f"<tr><td>{i+1}</td><td>{esc(name)[:50]}</td><td>{esc(spec)[:30]}</td>"
            f"<td class='num'>{v['cnt']:.1f}</td><td class='num'>¥{v['sum']:,.2f}</td></tr>"
            for i, ((name, spec), v) in enumerate(top_drugs)
        )
        drugs_html = f"""
        <details class="subblock">
          <summary><b>药品/耗材明细 Top 8 (prodname)</b></summary>
          <table class="data-table">
            <thead><tr><th>#</th><th>药品/耗材名</th><th>规格</th><th>数量</th><th>金额</th></tr></thead>
            <tbody>{drug_rows}</tbody>
          </table>
        </details>
        """

    # Fee categories bar chart
    cat_max_sum = max((v["sum"] for _, v in fee_cats), default=1)
    cat_rows = ""
    for label, v in fee_cats:
        pct = v["sum"] / b["fees_sum"] * 100 if b["fees_sum"] else 0
        bar_pct = v["sum"] / cat_max_sum * 100 if cat_max_sum else 0
        cat_rows += f"""
        <tr>
          <td>{esc(label)}</td>
          <td class='num'>{v['cnt']}</td>
          <td class='num'>¥{v['sum']:,.2f}</td>
          <td class='num'>{pct:.1f}%</td>
          <td><div class='bar'><div class='bar-fill' style='width:{bar_pct}%'></div></div></td>
        </tr>
        """
    cat_block = f"""
    <details class="subblock" open>
      <summary><b>按医保项目类别拆分 (medins_chrgitm_type)</b></summary>
      <table class="data-table">
        <thead><tr><th>类别</th><th>条数</th><th>金额</th><th>占比</th><th></th></tr></thead>
        <tbody>{cat_rows}</tbody>
      </table>
    </details>
    """

    notes_block = f"""
    <details class="subblock">
      <summary><b>文书阶段分布 Top 8</b></summary>
      <table class="data-table">
        <thead><tr><th>阶段</th><th>条数</th></tr></thead>
        <tbody>{''.join(f'<tr><td>{esc(s)}</td><td class=num>{n}</td></tr>' for s,n in top_stages)}</tbody>
      </table>
    </details>
    """

    v_count = counts["VIOLATION"]
    i_count = counts["INCONCLUSIVE"]
    c_count = counts["CLEAN"]

    audit_summary = f"""
    <div class="audit-summary">
      <div class="stat-card" style="background:#fef2f2;border-color:#dc2626">
        <div class="stat-num" style="color:#dc2626">{v_count}</div>
        <div class="stat-lab">违规 VIOLATION</div>
      </div>
      <div class="stat-card" style="background:#fffbeb;border-color:#d97706">
        <div class="stat-num" style="color:#d97706">{i_count}</div>
        <div class="stat-lab">存疑 INCONCLUSIVE</div>
      </div>
      <div class="stat-card" style="background:#f0fdf4;border-color:#16a34a">
        <div class="stat-num" style="color:#16a34a">{c_count}</div>
        <div class="stat-lab">合规 CLEAN</div>
      </div>
      <div class="stat-card" style="background:#f3f4f6;border-color:#6b7280">
        <div class="stat-num" style="color:#374151">{total_runs}</div>
        <div class="stat-lab">规则总数</div>
      </div>
    </div>
    """

    v_html = "".join(rule_card(r) for r in violations) or "<p class='muted'>无违规命中.</p>"
    i_html = "".join(rule_card(r) for r in inconclusives) or "<p class='muted'>无存疑.</p>"
    clean_pills = []
    for r in cleans:
        rid = r["rule_id"]
        title = esc(rules.get(rid, {}).get("title", ""))
        clean_pills.append(f"<span class='clean-pill' title='{title}'>{esc(rid)}</span>")
    clean_rule_list = "".join(clean_pills) or "(无)"

    return f"""
    <section class="patient-tab" id="tab-{esc(pid)}">
      <h2>病案 {esc(pid)}
        <span class="badge-v">V {v_count}</span>
        <span class="badge-i">I {i_count}</span>
        <span class="badge-c">C {c_count}</span>
      </h2>

      <div class="block">
        <h3>① 病案基本信息</h3>
        {basics_html}
        {chief_html}{hx_html}{past_html}{surg_html}{path_html}
      </div>

      <div class="block">
        <h3>② 费用结构</h3>
        {cat_block}
        {fees_block}
        {drugs_html}
      </div>

      <div class="block">
        <h3>③ 文书构成</h3>
        {notes_block}
      </div>

      <div class="block">
        <h3>④ 审计裁决汇总</h3>
        {audit_summary}
      </div>

      <div class="block">
        <h3>⑤ 违规规则明细 ({v_count} 条)</h3>
        <p class='muted'>verdict=VIOLATION + confidence ≥ 0.70 视为可上报. 按 confidence 倒序.</p>
        {v_html}
      </div>

      <div class="block">
        <h3>⑥ 存疑规则明细 ({i_count} 条)</h3>
        <p class='muted'>verdict=INCONCLUSIVE 表示证据不足以定论, 建议人工复核或补充数据.</p>
        {i_html}
      </div>

      <div class="block">
        <h3>⑦ 合规规则 ({c_count} 条)</h3>
        <details>
          <summary>展开查看合规规则 ID 列表 (鼠标悬停看标题)</summary>
          <p style='margin-top:10px;line-height:2'>{clean_rule_list}</p>
        </details>
      </div>
    </section>
    """


def build_html(rules: dict, basics: dict, audits: dict, zd: dict, ss: dict) -> str:
    def short_dx(pid: str) -> str:
        z = zd.get(pid, {}).get("main", [])
        if z:
            return z[0]["name"][:14]
        d = (basics[pid]['diagnoses'].get('出院诊断') or basics[pid]['diagnoses'].get('入院诊断') or ['—'])[0]
        return d[:14]

    nav = "".join(
        f"<button class='tab-btn' data-target='tab-{pid}'>"
        f"<div class='nav-pid'>{pid}</div>"
        f"<div class='nav-dx'>{esc(short_dx(pid))}</div>"
        f"<div class='nav-meta'>"
        f"<span class='nv'>V {sum(1 for r in audits[pid] if r['verdict']=='VIOLATION')}</span>"
        f"<span class='ni'>I {sum(1 for r in audits[pid] if r['verdict']=='INCONCLUSIVE')}</span>"
        f"<span class='nc'>C {sum(1 for r in audits[pid] if r['verdict']=='CLEAN')}</span>"
        f"</div></button>"
        for pid in PATIENTS
    )

    tabs = "".join(render_patient_tab(pid, basics[pid], audits[pid], rules, zd, ss) for pid in PATIENTS)

    total_v = sum(sum(1 for r in audits[p] if r["verdict"] == "VIOLATION") for p in PATIENTS)
    total_i = sum(sum(1 for r in audits[p] if r["verdict"] == "INCONCLUSIVE") for p in PATIENTS)
    total_c = sum(sum(1 for r in audits[p] if r["verdict"] == "CLEAN") for p in PATIENTS)
    total_runs = total_v + total_i + total_c
    total_fees = sum(basics[p]["fees_sum"] for p in PATIENTS)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Javert 病案资料员合规审计 · 10 病人 v0.4</title>
<style>
  * {{ box-sizing: border-box }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; background: #f3f4f6; color: #1f2937; font-size: 14px }}
  header {{ background: linear-gradient(135deg, #1e3a8a, #3b82f6);
           color: white; padding: 18px 24px }}
  header h1 {{ margin: 0; font-size: 20px }}
  header .sub {{ opacity: 0.92; margin-top: 4px; font-size: 13px }}
  .global-stats {{ display: flex; gap: 12px; margin-top: 14px; flex-wrap: wrap }}
  .gs {{ background: rgba(255,255,255,0.15); padding: 8px 14px; border-radius: 6px }}
  .gs .v {{ font-weight: bold; font-size: 16px }}
  nav.tabs {{ background: white; padding: 12px; display: flex; gap: 6px;
             overflow-x: auto; border-bottom: 1px solid #e5e7eb;
             position: sticky; top: 0; z-index: 10 }}
  .tab-btn {{ padding: 10px 12px; border: 1px solid #d1d5db; background: white;
             cursor: pointer; border-radius: 6px; min-width: 110px;
             text-align: center; white-space: nowrap }}
  .tab-btn:hover {{ background: #f9fafb; border-color: #93c5fd }}
  .tab-btn.active {{ background: #1e3a8a; color: white; border-color: #1e3a8a }}
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
  h2 {{ margin-top: 0; border-bottom: 2px solid #1e3a8a; padding-bottom: 8px }}
  h2 .badge-v, h2 .badge-i, h2 .badge-c {{ font-size: 12px; padding: 3px 9px;
        margin-left: 6px; border-radius: 12px; color: white; font-weight: normal }}
  .badge-v {{ background: #dc2626 }}
  .badge-i {{ background: #d97706 }}
  .badge-c {{ background: #16a34a }}
  h3 {{ margin-top: 26px; color: #1e3a8a; border-left: 4px solid #1e3a8a;
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
  details summary {{ cursor: pointer; margin-top: 6px; color: #1e3a8a }}
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
  .bar-fill {{ height: 100%; background: linear-gradient(90deg,#3b82f6,#1e3a8a) }}
</style>
</head>
<body>
<header>
  <h1>Javert 医保合规审计 · 病案资料员视角 · 10 病人汇总 v0.4</h1>
  <div class="sub">生成: {now} · 模板: M1-M7 全装载 (含 m7-rollout 新规则)
      · 每病人审计 111 条 ready 规则 · 共 1110 裁决</div>
  <div class="global-stats">
    <div class="gs">总裁决 <span class="v">{total_runs}</span></div>
    <div class="gs">违规 V <span class="v" style="color:#fca5a5">{total_v}</span></div>
    <div class="gs">存疑 I <span class="v" style="color:#fed7aa">{total_i}</span></div>
    <div class="gs">合规 C <span class="v" style="color:#86efac">{total_c}</span></div>
    <div class="gs">10 病人费用合计 <span class="v">¥{total_fees:,.0f}</span></div>
  </div>
</header>
<nav class="tabs">{nav}</nav>
<main>{tabs}</main>
<script>
  const buttons = document.querySelectorAll('.tab-btn');
  const tabs = document.querySelectorAll('.patient-tab');
  buttons.forEach(b => b.addEventListener('click', () => {{
    buttons.forEach(x => x.classList.remove('active'));
    tabs.forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById(b.dataset.target).classList.add('active');
    window.scrollTo({{top: 0, behavior: 'smooth'}});
  }}));
  if (buttons.length) buttons[0].click();
</script>
</body>
</html>
"""


def main():
    print("loading rules...", file=sys.stderr)
    rules = load_rules()
    print(f"  {len(rules)} rules", file=sys.stderr)

    print("loading shi_zd (病案首页诊断 ground truth)...", file=sys.stderr)
    zd = load_shi_zd_diagnoses()
    print("loading shi_ss (病案首页手术 ground truth)...", file=sys.stderr)
    ss = load_shi_ss_surgeries()
    for pid in PATIENTS:
        sl = ss[pid]
        main_op = next((o for o in sl if o["main"] == 1), None)
        print(f"  {pid}: 手术{len(sl)}条 主={main_op['name']+'('+main_op['code']+')' if main_op else '(无)'}",
              file=sys.stderr)
    for pid in PATIENTS:
        main = zd[pid]["main"]
        print(f"  {pid}: 主诊={main[0]['name']+'('+main[0]['code']+')' if main else '(无)'}"
              f" 其他={len(zd[pid]['others'])}条", file=sys.stderr)

    print("loading patient basics (fee + notes)...", file=sys.stderr)
    basics = load_patient_basics()
    for pid in PATIENTS:
        b = basics[pid]
        print(f"  {pid}: {b['admit_date']}→{b['discharge_date']} ({b['los_days']}d) "
              f"notes={b['notes_total']} fees={b['fees_count']}/¥{b['fees_sum']:,.0f} "
              f"top_dept={list(b['departments'].keys())[:1]}", file=sys.stderr)

    print("loading audit runs...", file=sys.stderr)
    audits = load_audit_runs()
    for pid in PATIENTS:
        v = sum(1 for r in audits[pid] if r["verdict"] == "VIOLATION")
        i = sum(1 for r in audits[pid] if r["verdict"] == "INCONCLUSIVE")
        c = sum(1 for r in audits[pid] if r["verdict"] == "CLEAN")
        print(f"  {pid}: total={len(audits[pid])} V={v} I={i} C={c}", file=sys.stderr)

    print(f"rendering HTML -> {OUT_HTML}", file=sys.stderr)
    OUT_HTML.write_text(build_html(rules, basics, audits, zd, ss), encoding="utf-8")
    print(f"DONE. size={OUT_HTML.stat().st_size:,} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
