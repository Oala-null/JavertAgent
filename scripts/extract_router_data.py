"""把规则引擎代码 xls 提取成 RuleRouter 用的 JSON.

产出三个文件:
  data/router/violation_dict.json   # 14372 行 + keyword index, 按 18 大类分组, 已附 pruning_hints
  data/router/active_java_rules.json # 11 条 valid=1 规则的配置 + 对应 dict 大类 + 触发字段
  data/router/pruning_rules.json     # 高层 prune (诊断/科室/性别/年龄/医院级别/就医方式) 规则字典

数据来源:
  规则引擎代码/规则未处理.xls       (36 条规则配置, 11 条 valid=1)
  规则引擎代码/违规规则明细.xls     (14372 条违规条目, 含 18 大类)

跑法:
  uv run python scripts/extract_router_data.py
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import xlrd

ROOT = Path(__file__).resolve().parents[1]
SRC_RULES_XLS = ROOT / "规则引擎代码" / "规则未处理.xls"
SRC_DETAIL_XLS = ROOT / "规则引擎代码" / "违规规则明细.xls"
OUT_DIR = ROOT / "data" / "router"

# 18 大类 → Java rule_no 对应关系 (基于 ImsRuleCatch + RuleItem 语义 + xls 行数验证)
CATEGORY_TO_JAVA_RULE: dict[str, str] = {
    "药品限就医方式":         "RULE2",
    "医疗服务项目限就医方式":  "RULE2",
    "药品限性别使用":         "RULE3",
    "医疗服务项目区分性别使用": "RULE3",
    "药品限儿童使用":         "RULE4",
    "医疗服务项目限儿童使用":  "RULE4",
    "药品限定医院类型级别":    "RULE5",
    "医疗服务项目限定医院类型级别": "RULE5",
    "药品限定诊断":           "RULE7",
    "中药饮片审核":           "RULE9",
    "药品不匹配":            "RULE15",
    "医疗服务项目限定频次":    "RULE17",
    "药品重复收费":           "RULE19",
    "医疗服务项目重复收费":    "RULE19",
    "药品限定支付疗程":        "RULE35",
    "医疗服务项目限定支付疗程":  "RULE35",
    "药品医保不支付":          "RULE36",
    "医疗服务项目医保不支付":   "RULE36",
}

# 11 个 active Java 规则的元信息 (rule_no, name, remark 从 xls 取; 字段提示从 RuleItem 代码语义抽)
ACTIVE_RULE_META: dict[str, dict] = {
    "RULE2":  {"triggers": ["item_name_match", "visit_type"]},          # 限就医方式 (门/住)
    "RULE3":  {"triggers": ["item_name_match", "patient_gender"]},      # 限性别
    "RULE4":  {"triggers": ["item_name_match", "patient_age"]},         # 限儿童 / 年龄阈值
    "RULE5":  {"triggers": ["item_name_match", "hospital_level"]},      # 限医院级别 (二/三级)
    "RULE7":  {"triggers": ["item_name_match", "diagnosis_in_set"]},    # 限定诊断
    "RULE9":  {"triggers": ["item_name_match", "single_herb_only"]},    # 单味饮片
    "RULE15": {"triggers": ["item_name_match", "paired_item_missing"]}, # 耗材与项目不符
    "RULE17": {"triggers": ["item_name_match", "frequency_window"]},    # 超限定频次
    "RULE19": {"triggers": ["item_a_match", "item_b_match"]},           # A 与 B 同时收费 = 重复
    "RULE35": {"triggers": ["item_name_match", "cumulative_days"]},     # 超限定支付疗程
    "RULE36": {"triggers": ["item_name_match", "inscp_amt_gt_zero"]},   # 非基本医保目录
}

BRACKET = re.compile(r"【([^】]+)】")

# warn_msg 文本里的 pruning hints (用 regex 抽; 缺失就不限制)
GENDER_PAT_F = re.compile(r"限女性|女性使用|妇女|月经|妊娠|孕")
GENDER_PAT_M = re.compile(r"限男性|男性使用")
VISIT_IPT_PAT = re.compile(r"限住院|住院使用|住院[^门]|限\s*入院")
VISIT_OPT_PAT = re.compile(r"限门|限急诊|限门急诊|门诊使用|门急诊使用")
AGE_PAT = re.compile(r"限\s*(\d+)\s*岁|(\d+)\s*岁\s*及以下|(\d+)\s*岁\s*以下|限\s*儿童|限\s*新生儿|未成年")
HOSP_PAT = re.compile(r"限\s*(一|二|三)\s*级")
HOSP_NUM = {"一": 1, "二": 2, "三": 3}
DIAG_REQ_PAT = re.compile(r"限有?\s*[^】]{0,30}诊断|需有.*诊断|有.*诊断.*用|有.*诊断.*药")


def extract_pruning_hints(warn_msg: str, category: str) -> dict:
    """从 warn_msg 文本抽出可用的 prefilter 提示. 缺则不限制."""
    hints: dict = {}

    # 1) 性别
    if "性别" in category or GENDER_PAT_F.search(warn_msg):
        if GENDER_PAT_F.search(warn_msg):
            hints["require_gender"] = "F"
        elif GENDER_PAT_M.search(warn_msg):
            hints["require_gender"] = "M"

    # 2) 就医方式 (仅当类别本身关心 visit_type 时)
    if "就医方式" in category:
        if VISIT_IPT_PAT.search(warn_msg):
            hints["require_visit_type"] = "ipt"
        elif VISIT_OPT_PAT.search(warn_msg):
            hints["require_visit_type"] = "opt"

    # 3) 年龄
    if "儿童" in category or AGE_PAT.search(warn_msg):
        m = AGE_PAT.search(warn_msg)
        if m:
            digits = next((g for g in m.groups() if g and g.isdigit()), None)
            if digits:
                hints["max_age"] = int(digits)
            elif "儿童" in warn_msg or "新生儿" in warn_msg:
                hints["max_age"] = 18
        elif "儿童" in category:
            hints["max_age"] = 18

    # 4) 医院级别
    if "医院" in category or "医院类型级别" in category:
        m = HOSP_PAT.search(warn_msg)
        if m:
            hints["min_hospital_level"] = HOSP_NUM[m.group(1)]

    # 5) 诊断要求 (主要用于 RULE7 / RULE36)
    if "限定诊断" in category or DIAG_REQ_PAT.search(warn_msg):
        hints["requires_diagnosis_context"] = True

    return hints


def load_active_rules() -> dict[str, dict]:
    """从 规则未处理.xls 读 36 条, filter valid=1 的 11 条."""
    wb = xlrd.open_workbook(str(SRC_RULES_XLS))
    sh = wb.sheet_by_index(0)
    rules: dict[str, dict] = {}
    for r in range(1, sh.nrows):
        rule_no, name, remark, valid = [str(sh.cell_value(r, c)).strip() for c in range(4)]
        if valid != "1":
            continue
        rules[rule_no] = {
            "rule_no": rule_no,
            "rule_name": name,
            "rule_remark": remark,
            "valid": valid,
            "triggers": ACTIVE_RULE_META.get(rule_no, {}).get("triggers", []),
            "covered_categories": [
                cat for cat, rn in CATEGORY_TO_JAVA_RULE.items() if rn == rule_no
            ],
        }
    return rules


def load_violation_dict() -> tuple[list[dict], dict[str, list[int]]]:
    """从 违规规则明细.xls 读 14372 行, 抽 item_name list + 附 pruning_hints."""
    wb = xlrd.open_workbook(str(SRC_DETAIL_XLS))
    sh = wb.sheet_by_index(0)

    entries: list[dict] = []
    keyword_index: dict[str, list[int]] = defaultdict(list)

    for r in range(1, sh.nrows):
        cat = str(sh.cell_value(r, 0)).strip()
        msg = str(sh.cell_value(r, 1)).strip()
        items = BRACKET.findall(msg)
        if not items:
            items = []  # 极少数无 brackets 的条目 (e.g. CT增强和平扫不能同时收费)
        # 多 bracket 会出现重复 (eg "【X】... 【X】...") 取 unique
        items_uniq = list(dict.fromkeys(items))

        java_rule = CATEGORY_TO_JAVA_RULE.get(cat, "UNKNOWN")
        idx = len(entries)
        entry = {
            "idx": idx,
            "category": cat,
            "java_rule_no": java_rule,
            "items": items_uniq,
            "warn_msg": msg,
            "pruning_hints": extract_pruning_hints(msg, cat),
        }
        entries.append(entry)

        # 倒排索引: item_name → 该 entry 的 idx
        for it in items_uniq:
            keyword_index[it].append(idx)

    return entries, dict(keyword_index)


def build_pruning_rules(rules: dict[str, dict]) -> dict:
    """高层 pruning: java rule 对哪些场景豁免 (来自 RuleItem 代码 + RuleItemBase.checkRuleValid)."""
    # 这些 fields 来自 RuleItemBase.checkRuleValid + 11 个 RuleItem 实现
    # 现阶段 14372 xls 没有结构化 start_date/end_date/checkflag_opt 等字段
    # 仅记录通用语义供 RuleRouter 决策使用
    return {
        "global": {
            "comment": "RuleItemBase.checkRuleValid 通用 prune 字段, 在条目级 entry.pruning_hints 体现",
            "fields_known": [
                "valid",              # 该条 14372 是否生效 (默认都生效, 已在 xls 落地的为 valid=1)
                "start_date/end_date",  # xls 未导出, 假设全年有效
                "checkflag_opt",      # xls 未导出, 仅 RULE2/RULE17 类靠 warn_msg 文本推断
                "checkflag_ipt",      # 同上
                "limit_opt_dept/limit_ipt_dept",  # ImsRuleCatch 注释掉, 默认不限
                "limit_med_mdtrt_type",  # 同上, 注释掉
            ],
        },
        "by_java_rule": {
            "RULE2": {"dim": "visit_type",      "patient_field": "visit_type"},
            "RULE3": {"dim": "patient_gender",  "patient_field": "gender"},
            "RULE4": {"dim": "patient_age",     "patient_field": "age"},
            "RULE5": {"dim": "hospital_level",  "patient_field": "hospital_level"},
            "RULE7": {"dim": "diagnosis",       "patient_field": "diagnoses",
                       "note": "限定诊断: 若 entry 标 requires_diagnosis_context, 仅当患者诊断与 warn_msg 描述域有重叠时才触发"},
            "RULE9": {"dim": "drug_form",       "patient_field": "drug_forms",
                       "note": "中药饮片: 仅当患者用过单味中药饮片才有意义"},
            "RULE15":{"dim": "paired_items",    "patient_field": "items",
                       "note": "材料-项目配对: 仅当 A 出现但 B 缺失才触发"},
            "RULE17":{"dim": "frequency",       "patient_field": "items",
                       "note": "频次: 至少有 1 个目标项即潜在触发, 具体频次靠 LLM"},
            "RULE19":{"dim": "co_occurrence_AB","patient_field": "items",
                       "note": "重复收费: 必须 A 和 B 同时出现才触发"},
            "RULE35":{"dim": "duration",        "patient_field": "items",
                       "note": "支付疗程: 仅当患者用过该项目, 具体天数靠 LLM"},
            "RULE36":{"dim": "inscp_amt",       "patient_field": "items",
                       "note": "非医保目录: 仅当对应 item 有医保入账金额 (inscp_scp_amt > 0)"},
        },
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1) active rules
    rules = load_active_rules()
    out1 = OUT_DIR / "active_java_rules.json"
    out1.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[1/3] active_java_rules.json  →  {len(rules)} 条 (RULE2/3/4/5/7/9/15/17/19/35/36)")

    # 2) violation dict
    entries, keyword_index = load_violation_dict()
    by_rule_count: dict[str, int] = defaultdict(int)
    for e in entries:
        by_rule_count[e["java_rule_no"]] += 1
    out2 = OUT_DIR / "violation_dict.json"
    payload = {
        "categories_to_java_rule": CATEGORY_TO_JAVA_RULE,
        "n_entries": len(entries),
        "n_keywords": len(keyword_index),
        "by_rule_count": dict(by_rule_count),
        "entries": entries,
        "keyword_index": keyword_index,
    }
    out2.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[2/3] violation_dict.json     →  {len(entries)} entries, {len(keyword_index)} unique keywords")
    for rn, cnt in sorted(by_rule_count.items()):
        print(f"        {rn}: {cnt}")

    # 3) pruning rules
    out3 = OUT_DIR / "pruning_rules.json"
    out3.write_text(json.dumps(build_pruning_rules(rules), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/3] pruning_rules.json      →  by_java_rule for {len(rules)} rules")


if __name__ == "__main__":
    main()
