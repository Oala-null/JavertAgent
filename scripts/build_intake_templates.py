#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_intake_templates — 生成「合作医院数据接入标准模板」(docs/schema/).

给对方医院 IT 提前发: 每张表一个 .xlsx, 两个 sheet —— ①字段字典 (列名/必填/类型/含义/示例)
②数据填写 (只有表头, 行空, 直接往里填). 表头取自 schema_manifest + field_alias 的推荐列名,
**填好回传我们直接跑** (上传 /onboarding 即自动认表 + 自动映射 + 连接预检通过).

自检 (always): 对每张表的表头跑 classify_columns 断言归到正确 spoke + 必填全命中;
再合成 费用+文书+诊断+手术 各 2 行 (同一住院号) 跑 run_etl + preflight 断言 🟢 —— 证明"直接跑".

改 schema_manifest / field_alias / 本文件 META 后重跑: uv run python scripts/build_intake_templates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from javert.onboarding.classifier import alias_match, classify_columns, match_fields  # noqa: E402
from javert.onboarding.manifest_loader import load_manifest  # noqa: E402

OUT_DIR = ROOT / "docs" / "schema"

# 每张表的文件名 (序号 + 中文)
FILE_NAMES = {
    "fees": "01_费用明细.xlsx",
    "notes": "02_病历文书.xlsx",
    "diagnoses": "03_诊断.xlsx",
    "surgeries": "04_手术.xlsx",
    "labs": "05_检验.xlsx",
    "examinations": "06_检查.xlsx",
}

# 表级说明 (字段字典 sheet 顶部)
TABLE_NOTES = {
    "fees": "一行一条收费明细 (同一住院号可多行)。退费金额写负数。",
    "notes": "一行一段文书；也可整份文书放一行 (内容里用【主诉】【现病史】等【】标记, 系统自动拆段)。",
    "diagnoses": "病案首页诊断, 一行一个诊断。主诊断那行「主诊断标志」填 1, 其余填 0。",
    "surgeries": "病案首页手术, 一行一个手术。主手术那行「主手术标志」填 1, 其余填 0。",
    "labs": "检验/化验, 一行一个结果项 (一次抽血多个指标 → 多行)。",
    "examinations": "检查/影像, 一行一个检查报告。",
}

# (header, 含义说明, 示例) per spoke.field —— header 必须能被 field_alias 自动映射 (脚本断言)
META: dict[str, dict[str, tuple[str, str, str]]] = {
    "fees": {
        "patient_id": ("住院号", "患者住院号/病案号。★必须与其它所有表用同一个值★ — 这是把费用/文书/诊断/手术关联到同一患者的唯一钥匙", "211454284"),
        "item_name": ("收费项目名称", "本行收费的项目/药品/耗材名称 (按收费明细原样)", "盐酸利多卡因注射液"),
        "amount": ("金额", "本行费用总额 (元); 退费写负数", "1580.00"),
        "date": ("收费日期", "该笔费用发生/记账日期。推荐 ISO: 2026-01-05", "2026-01-05"),
        "category": ("费用类别", "西药/中成药/检查/化验/治疗/手术/护理/材料/其他", "西药费"),
        "quantity": ("数量", "数量/次数", "2"),
        "unit_price": ("单价", "单价 (元)", "790.00"),
        "spec": ("规格", "药品规格/耗材型号", "5ml:0.1g"),
        "item_code": ("项目编码", "医保或院内项目编码", "XJ01ABC123"),
        "dept_order": ("开单科室", "申请/开单科室", "普外科"),
        "doctor_order": ("开单医生", "申请/开单医生", "张医生"),
        "dept_billing": ("计费科室", "执行/计费科室", "药剂科"),
        "doctor_billing": ("计费医生", "执行/计费医生", "李医生"),
        "product_name": ("药品通用名", "药品通用名 (区别于收费项目名/商品名)", "利多卡因"),
    },
    "notes": {
        "patient_id": ("住院号", "同上, ★与其它表保持一致★", "211454284"),
        "section": ("文书名称", "文书/段落名称, 如 主诉/现病史/入院诊断/手术记录/病程记录", "入院记录"),
        "content": ("内容", "文书正文。整份文书可放一行 (用【主诉】【现病史】等【】标记, 系统自动拆段)", "患者因「颈部肿物 3 月」入院, 查体…"),
        "doc_name": ("文书标题", "所属文书标题 (如 入院记录/手术记录), 可留空", "入院记录"),
        "time": ("文书时间", "文书书写/记录时间。推荐 ISO", "2026-01-03 09:30:00"),
    },
    "diagnoses": {
        "patient_id": ("住院号", "同上, ★与其它表保持一致★", "211454284"),
        "main_flag": ("主诊断标志", "1 = 主诊断, 0 = 其它诊断", "1"),
        "diag_name": ("诊断名称", "ICD-10 中文诊断名称", "甲状腺恶性肿瘤"),
        "diag_code": ("诊断编码", "ICD-10 编码", "C73.x00"),
    },
    "surgeries": {
        "patient_id": ("住院号", "同上, ★与其它表保持一致★", "211454284"),
        "surgery_name": ("手术名称", "手术/操作名称", "甲状腺全切除术"),
        "main_flag": ("主手术标志", "1 = 主手术, 0 = 其它", "1"),
        "surgery_code": ("手术编码", "ICD-9-CM3 编码, 可留空", "06.4x00"),
        "surgery_date": ("手术日期", "手术日期/结束时间。推荐 ISO", "2026-01-04"),
        "level": ("手术级别", "一/二/三/四级", "三级"),
        "anesthesia": ("麻醉方式", "麻醉方法 (全麻/局麻/椎管内…)", "全身麻醉"),
        "surgeon": ("手术医师", "术者", "王医生"),
        "anesthesiologist": ("麻醉医师", "麻醉医师签名", "赵医生"),
    },
    "labs": {
        "patient_id": ("住院号", "同上, ★与其它表保持一致★", "211454284"),
        "item_name": ("检验项目", "检验项目名 (一行一个结果项)", "游离甲状腺素 FT4"),
        "item_code": ("检验项代码", "项目英文缩写/代码, 可留空", "FT4"),
        "result": ("检验结果", "结果值", "12.6"),
        "result_unit": ("单位", "结果单位", "pmol/L"),
        "result_ref": ("参考范围", "参考区间", "9.0-19.0"),
        "result_flag": ("异常标志", "异常标志 (H 高/L 低/↑↓/阳性…), 可留空", "H"),
        "report_dt": ("报告时间", "报告时间。推荐 ISO", "2026-01-03 14:00:00"),
        "specimen": ("样本", "标本类型 (血清/病理…)", "血清"),
        "inspection": ("检验类别", "检验大类 (生化/免疫/血液…)", "免疫"),
        "department": ("送检科室", "送检/申请科室", "普外科"),
    },
    "examinations": {
        "patient_id": ("住院号", "同上, ★与其它表保持一致★", "211454284"),
        "check_type": ("检查类型", "检查大类 (CT/MRI/超声/X 线…), 可留空", "超声"),
        "item_name": ("检查项目", "检查项目名 (一行一个报告)", "甲状腺及颈部淋巴结超声"),
        "conclusion": ("检查结论", "检查结论/诊断意见", "甲状腺右叶实性结节 TI-RADS 4c"),
        "describe": ("检查所见", "检查所见/影像描述", "右叶见 1.2cm 低回声结节, 边界不清…"),
        "position": ("检查部位", "检查部位", "甲状腺"),
        "department": ("检查科室", "检查科室", "超声科"),
        "check_date": ("检查日期", "检查日期。推荐 ISO", "2026-01-02"),
        "report_date": ("报告日期", "报告日期。推荐 ISO", "2026-01-02"),
    },
}

NUMERIC_FIELDS = {"amount", "quantity", "unit_price"}
FLAG_FIELDS = {"main_flag"}


def _dtype(spoke, field) -> str:
    if field.is_date:
        return "日期/时间"
    if field.key in FLAG_FIELDS:
        return "0/1 标志"
    if field.key in NUMERIC_FIELDS:
        return "数字"
    if field.key == "result":
        return "数字/文本"
    return "文本"


def _load_alias() -> dict:
    with open(ROOT / "configs" / "field_alias.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ────────────── xlsx 生成 ──────────────

def _styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    thin = Side(style="thin", color="D5DBE3")
    return {
        "hdr_font": Font(bold=True, color="FFFFFF", size=11),
        "hdr_fill": PatternFill("solid", fgColor="1E40AF"),
        "req_font": Font(bold=True, color="B91C1C"),
        "opt_font": Font(color="64748B"),
        "title_font": Font(bold=True, size=13, color="1E40AF"),
        "note_font": Font(color="334155", size=10),
        "hl_font": Font(color="B45309", size=10),
        "wrap": Alignment(wrap_text=True, vertical="top"),
        "center": Alignment(horizontal="center", vertical="center"),
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
    }


def build_workbook(spoke, alias, st) -> "openpyxl.Workbook":
    import openpyxl
    wb = openpyxl.Workbook()

    # ── sheet ① 字段字典 ──
    d = wb.active
    d.title = "①字段说明"
    d["A1"] = f"{spoke.name}  —  字段说明"
    d["A1"].font = st["title_font"]
    d.merge_cells("A1:F1")
    d["A2"] = TABLE_NOTES.get(spoke.key, "")
    d["A2"].font = st["note_font"]
    d.merge_cells("A2:F2")
    d["A3"] = "★ 跨表关键: 「住院号」在所有表用同一个值 (关联同一患者的钥匙)。日期推荐 ISO 格式 2026-01-05, 避免 05/01/2026 这种日/月歧义。"
    d["A3"].font = st["hl_font"]
    d.merge_cells("A3:F3")

    hdr = ["列名 (请按此填到「②数据填写」表头)", "必填", "数据类型", "含义说明", "示例", "也可用的列名 (若你 HIS 已有)"]
    d.append([])  # row4 空
    d.append(hdr)  # row5
    hr = d.max_row
    for c in range(1, len(hdr) + 1):
        cell = d.cell(hr, c)
        cell.font = st["hdr_font"]
        cell.fill = st["hdr_fill"]
        cell.alignment = st["center"]
        cell.border = st["border"]

    for field in spoke.fields:
        header, desc, example = META[spoke.key][field.key]
        others = [a for a in alias.get(spoke.key, {}).get(field.key, []) if a != header][:4]
        row = [header, "必填" if field.required else "可选", _dtype(spoke, field),
               desc, example, " / ".join(others)]
        d.append(row)
        r = d.max_row
        d.cell(r, 2).font = st["req_font"] if field.required else st["opt_font"]
        d.cell(r, 2).alignment = st["center"]
        for c in range(1, 7):
            d.cell(r, c).border = st["border"]
            d.cell(r, c).alignment = st["wrap"]

    for col, w in zip("ABCDEF", (26, 8, 12, 46, 22, 30)):
        d.column_dimensions[col].width = w
    d.freeze_panes = f"A{hr + 1}"

    # ── sheet ② 数据填写 (只有表头, 行空) ──
    s = wb.create_sheet("②数据填写")
    headers = [META[spoke.key][f.key][0] for f in spoke.fields]
    s.append(headers)
    for c, field in enumerate(spoke.fields, start=1):
        cell = s.cell(1, c)
        cell.font = st["hdr_font"]
        cell.fill = st["hdr_fill"]
        cell.alignment = st["center"]
        cell.border = st["border"]
        # 必填列宽一点
        s.column_dimensions[cell.column_letter].width = max(14, len(headers[c - 1]) * 2 + 4)
    s.append([])  # 第一行数据空 (留给对方填)
    s.freeze_panes = "A2"
    return wb


# ────────────── 自检: 证明"直接跑" ──────────────

def self_check(manifest, alias) -> bool:
    ok = True
    print("\n── 自检 1: 每张表的表头能否自动认表 + 必填全命中 ──")
    for key in FILE_NAMES:
        spoke = manifest.spoke(key)
        headers = [META[key][f.key][0] for f in spoke.fields]
        # 每个 header 必须能被自身字段别名映射 (防 META 漂移)
        for f in spoke.fields:
            h = META[key][f.key][0]
            if alias_match(alias.get(key, {}).get(f.key, []), [h]) != h:
                print(f"  ✗ {key}.{f.key} 表头「{h}」无法被 field_alias 自动映射"); ok = False
        res = classify_columns(headers, manifest, alias)
        req = spoke.required_keys
        matched = match_fields(spoke, headers, alias)
        miss = [r for r in req if r not in matched]
        flag = "✓" if (res.spoke == key and not miss) else "✗"
        if res.spoke != key or miss:
            ok = False
        print(f"  {flag} {key:13} 表头 {len(headers)} 列 → 认出「{res.spoke}」必填 {len(req)-len(miss)}/{len(req)}"
              + (f"  缺: {miss}" if miss else ""))

    print("\n── 自检 2: 合成 费用+文书+诊断+手术 各 2 行 (同住院号) → ETL + 连接预检 ──")
    from javert.onboarding.etl_engine import run_etl
    from javert.onboarding.join_preflight import preflight_keys
    pids = ["211454284", "211486870"]
    mapping = {"hospital_code": "demo", "normalize_dates": True}
    tmp = OUT_DIR / "_selfcheck"
    tmp.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    rows = {
        "fees": [{"住院号": p, "收费项目名称": "血常规", "金额": "35.00", "收费日期": "2026-01-03",
                  "费用类别": "化验费"} for p in pids],
        "notes": [{"住院号": p, "文书名称": "入院记录", "内容": "颈部肿物 3 月"} for p in pids],
        "diagnoses": [{"住院号": p, "主诊断标志": "1", "诊断名称": "甲状腺恶性肿瘤",
                       "诊断编码": "C73.x00"} for p in pids],
        "surgeries": [{"住院号": p, "手术名称": "甲状腺全切除术", "主手术标志": "1"} for p in pids],
    }
    for key, data in rows.items():
        f = tmp / f"{key}.csv"
        pd.DataFrame(data).to_csv(f, index=False, encoding="utf-8")
        cols = match_fields(manifest.spoke(key), list(data[0].keys()), alias)
        mapping[key] = {"file": str(f.relative_to(ROOT)), "key_mode":
                        ("bridge" if manifest.spoke(key).via_bridge else "synth"), "columns": cols}
    result = run_etl(mapping, manifest)
    if not result.ok:
        print("  ✗ ETL 失败:", result.fatal_errors); ok = False
    else:
        meta = {s.key: manifest.spoke(s.key) for s in result.spokes}
        pf = preflight_keys(result.spokes, meta)
        flag = "✓" if pf and pf.verdict in ("green", "yellow") else "✗"
        if not (pf and pf.verdict in ("green", "yellow")):
            ok = False
        print(f"  {flag} 4 表载入: " + ", ".join(f"{s.name} {len(s.df)}行/{s.patient_count}患者"
              for s in result.spokes))
        if pf:
            print(f"  {flag} 连接预检 {pf.emoji} 覆盖率 {pf.coverage*100:.0f}% ({pf.label}) — 证明填好可直接跑")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return ok


def main() -> int:
    manifest = load_manifest()
    alias = _load_alias()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    st = _styles()
    print(f"生成接入模板 → {OUT_DIR.relative_to(ROOT)}/")
    for key, fname in FILE_NAMES.items():
        wb = build_workbook(manifest.spoke(key), alias, st)
        wb.save(OUT_DIR / fname)
        print(f"  ✓ {fname}")
    if not self_check(manifest, alias):
        print("\n❌ 自检未通过 — 模板表头与 schema 不一致, 请检查 META")
        return 1
    print("\n✅ 全部生成 + 自检通过 (填好回传可直接跑)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
