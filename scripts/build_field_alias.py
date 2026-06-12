#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_field_alias — 反推国标↔Javert 语义别名词典 → configs/field_alias.yaml.

add-visual-schema-onboarding (设计 D8):
公立医院多按国标 (医保结算清单 / 病案首页字段码) 导出, 上传列名 fuzzy 命中别名
→ /onboarding 自动预填映射. 本脚本从三处反推:
  1. 现有 configs/column_mapping.yaml (szx 真映射: 外部列 → 语义键)
  2. song 真实国标表头 (r_fee / szx_doc / r_basy_zd / r_basy_ss / sy_检验 / 检查)
  3. 内置国标 + 常见中文/英文别名种子 (_SEED)

产物 configs/field_alias.yaml 形如:
    fees:
      amount: [det_item_fee_sumamt, 金额, 总金额, ...]
确定性 (sort), 改完手动重跑.

用法:
    uv run python scripts/build_field_alias.py            # 写 configs/field_alias.yaml
    uv run python scripts/build_field_alias.py --dry-run  # 只打印
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 内置国标 / 中文 / 英文别名种子 (spoke → field_key → [aliases]) ──
# 来源: 医保结算清单字段码 + 病案首页字段码 + HIS 常见中文列名.
_SEED: dict[str, dict[str, list[str]]] = {
    "fees": {
        "patient_id": ["hsp_account_no", "bah", "患者ID", "住院号", "就诊流水号", "mdtrt_id"],
        "item_name": ["medins_list_name", "项目名称", "收费项目", "收费项目名称", "药品名称", "明细项目名称"],
        "amount": ["det_item_fee_sumamt", "金额", "总金额", "明细项目费用总额", "费用金额", "金额（元）"],
        "date": ["fee_ocur_time", "收费日期", "费用发生时间", "记账时间", "开单时间"],
        "category": ["med_chrgitm_type", "medins_chrgitm_type", "费用类别", "收费类别", "医保收费项目类别"],
        "quantity": ["cnt", "数量", "数量（次）"],
        "unit_price": ["pric", "单价", "单价（元）"],
        "spec": ["spec", "规格", "药品规格", "耗材型号"],
        "item_code": ["medins_list_codg", "med_list_codg", "项目编码", "医保编码", "院内编码"],
        "dept_order": ["acord_dept_name", "开单科室", "申请科室"],
        "doctor_order": ["orders_dr_name", "开单医生", "申请医生"],
        "dept_billing": ["bilg_dept_name", "计费科室", "执行科室"],
        "doctor_billing": ["bilg_dr_name", "计费医生", "执行医生"],
        "product_name": ["prodname", "药品通用名", "通用名", "商品名"],
    },
    "notes": {
        "patient_id": ["medcasno", "source_inpat_no", "inpat_id", "住院号", "病案号", "psn_no"],
        "section": ["record_name", "文书名称", "文书类型", "记录名称", "段落名称"],
        "content": ["replace", "内容", "正文", "文书内容", "记录内容"],
        "doc_name": ["doc_name", "文书标题", "记录标题"],
        "time": ["record_date", "文书时间", "记录时间", "书写时间"],
    },
    "diagnoses": {
        "patient_id": ["hsp_account_no", "ba_id", "病案号", "住院号"],
        "main_flag": ["maindiag_flag", "主诊断标志", "是否主诊断"],
        "diag_name": ["inhosp_diag_name", "diag_name", "诊断名称", "出院诊断", "ICD名称", "疾病诊断名称"],
        "diag_code": ["inhosp_diag_code", "diag_code", "诊断编码", "ICD编码", "疾病诊断代码"],
    },
    "surgeries": {
        "patient_id": ["hsp_account_no", "ba_id", "病案号", "住院号"],
        "surgery_name": ["oprn_oprt_name", "opr_name", "手术名称", "手术及操作名称"],
        "main_flag": ["main_oprn_flag", "主手术标志", "是否主手术"],
        "surgery_code": ["oprn_oprt_code", "opr_code", "手术编码", "手术及操作代码", "ICD9编码"],
        "surgery_date": ["oprn_oprt_date", "oprn_oprt_endtime", "手术日期", "手术结束时间"],
        "level": ["oprn_lv_name", "oprn_lv_code", "手术级别"],
        "anesthesia": ["anst_mtd_name", "anst_way", "麻醉方式", "麻醉方法"],
        "surgeon": ["oper_dr_name", "手术医师", "术者"],
        "anesthesiologist": ["anst_dr_name", "麻醉医师"],
    },
    "labs": {
        "patient_id": ["zyh", "住院号", "病案号"],
        "item_name": ["rpt_itemname", "检验项目", "检验项名", "项目名称"],
        "item_code": ["rpt_itemcode", "检验项代码", "英文缩写"],
        "result": ["result", "检验结果", "结果值"],
        "result_unit": ["result_unit", "单位"],
        "result_ref": ["result_ref", "参考范围", "参考值"],
        "result_flag": ["result_flag", "异常标志", "结果标志"],
        "report_dt": ["report_dt", "报告时间", "报告日期"],
        "specimen": ["specimen", "样本", "标本"],
        "inspection": ["inspectionName", "检验类别", "检验大类"],
        "department": ["department", "送检科室", "科室"],
    },
    "examinations": {
        "patient_id": ["zyh", "住院号", "病案号"],
        "check_type": ["checkType", "检查类型", "检查大类"],
        "item_name": ["checkItemName", "检查项目", "检查项名"],
        "conclusion": ["checkConclusion", "检查结论", "诊断意见"],
        "describe": ["checkDescribe", "检查所见", "影像所见"],
        "position": ["checkPosition", "检查部位"],
        "department": ["department", "检查科室", "科室"],
        "check_date": ["checkDate", "检查日期"],
        "report_date": ["reportDate", "报告日期", "报告时间"],
    },
}


def _reverse_column_mapping(mapping_path: Path) -> dict[str, dict[str, list[str]]]:
    """从 column_mapping.yaml 反推: spoke → field_key → [外部列名]."""
    if not mapping_path.exists():
        return {}
    with open(mapping_path, encoding="utf-8") as f:
        mapping = yaml.safe_load(f) or {}
    # column_mapping 用 fees/notes/diagnoses/surgeries 作节名 = spoke key
    out: dict[str, dict[str, list[str]]] = {}
    for spoke_key in ("fees", "notes", "diagnoses", "surgeries", "labs", "examinations"):
        section = mapping.get(spoke_key)
        if not isinstance(section, dict):
            continue
        cols = section.get("columns", {})
        if not isinstance(cols, dict):
            continue
        for field_key, ext_name in cols.items():
            if ext_name:
                out.setdefault(spoke_key, {}).setdefault(field_key, []).append(str(ext_name))
    return out


def build_alias() -> dict[str, dict[str, list[str]]]:
    """合并 _SEED + column_mapping 反推, 去重排序."""
    result: dict[str, dict[str, list[str]]] = {}

    # 1. seed
    for spoke, fields in _SEED.items():
        for fk, aliases in fields.items():
            result.setdefault(spoke, {}).setdefault(fk, [])
            result[spoke][fk].extend(aliases)

    # 2. 真映射反推 (优先级高 → 放最前, 去重时保序)
    reversed_map = _reverse_column_mapping(PROJECT_ROOT / "configs" / "column_mapping.yaml")
    for spoke, fields in reversed_map.items():
        for fk, aliases in fields.items():
            result.setdefault(spoke, {}).setdefault(fk, [])
            # 真映射放最前
            result[spoke][fk] = aliases + result[spoke][fk]

    # 3. 去重 (保序) + 内层排序友好: 真映射保前序, 其余按字典序稳定
    cleaned: dict[str, dict[str, list[str]]] = {}
    for spoke in sorted(result):
        cleaned[spoke] = {}
        for fk in sorted(result[spoke]):
            seen: set[str] = set()
            deduped: list[str] = []
            for a in result[spoke][fk]:
                key = a.strip()
                low = key.lower()
                if not key or low in seen:
                    continue
                seen.add(low)
                deduped.append(key)
            cleaned[spoke][fk] = deduped
    return cleaned


def main() -> None:
    parser = argparse.ArgumentParser(description="反推国标↔Javert 语义别名词典")
    parser.add_argument("--output", default="configs/field_alias.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    alias = build_alias()
    header = (
        "# ============================================================\n"
        "# Javert 国标↔语义别名种子 (field_alias)\n"
        "# ============================================================\n"
        "# add-visual-schema-onboarding (设计 D8): 上传列名 fuzzy 命中 → /onboarding 自动预填.\n"
        "# 由 scripts/build_field_alias.py 反推生成 (_SEED + column_mapping.yaml), 改完重跑.\n"
        "# 结构: spoke → field_key → [别名...] (左侧真映射别名优先).\n"
        "# ============================================================\n"
    )
    body = yaml.safe_dump(alias, allow_unicode=True, sort_keys=False, default_flow_style=False)
    text = header + body

    n_fields = sum(len(v) for v in alias.values())
    n_aliases = sum(len(a) for v in alias.values() for a in v.values())
    if args.dry_run:
        print(text)
        print(f"# {len(alias)} spoke / {n_fields} 字段 / {n_aliases} 别名 (dry-run)")
        return
    out_path = PROJECT_ROOT / args.output
    out_path.write_text(text, encoding="utf-8")
    print(f"✓ 写入 {out_path}: {len(alias)} spoke / {n_fields} 字段 / {n_aliases} 别名")


if __name__ == "__main__":
    main()
