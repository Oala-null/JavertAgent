"""扫描当前 Javert rule YAML 并构建 rule_mapping.json.

产出:
  data/router/javert_rules_index.json   # 实时 yaml 元数据 (含 rule kind / criteria ref)
  configs/rule_mapping.json             # 11 Java rule × N Javert yaml 多对多 mapping

Mapping 策略:
  1) 主映射 — derived_from_template 决定大方向
       M1 (重复收费)  →  RULE19  (字典 2329 条 "A 与 B 不能同时收费")
       M2 (过度检查)  →  RULE17 + RULE7   (超频次 + 限定适应症)
       M3 (口腔串换)  →  无 (Java RULE21 valid=0, javert-only)
       M4 (超标准收费) →  RULE5 + RULE3 + RULE4  (限医院级别 / 性别 / 儿童)
       M5 (虚构服务)  →  RULE36 + RULE2  (非医保目录 + 限就医方式)
       M6 (过度诊疗)  →  RULE35 + RULE17 + RULE7  (支付疗程 + 超频次 + 限定诊断)
       M7 (身份串换)  →  无 (Java RULE21 valid=0, javert-only)
  2) trigger_keywords 二次确认 — 若关键词命中 14372 字典任意 entry, 视为强信号

跑法:
  uv run python scripts/build_rule_mapping.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from javert.audit.rule_loader import load_rule

ROOT = Path(__file__).resolve().parents[1]
YAML_DIR = ROOT / "configs" / "rules"
DICT_PATH = ROOT / "data" / "router" / "violation_dict.json"
ACTIVE_RULES_PATH = ROOT / "data" / "router" / "active_java_rules.json"
OUT_INDEX_PATH = ROOT / "data" / "router" / "javert_rules_index.json"
OUT_MAPPING_PATH = ROOT / "configs" / "rule_mapping.json"

# 主映射: template → list[java_rule_no] (按相关度从主到辅排序)
TEMPLATE_TO_JAVA: dict[str, list[str]] = {
    "M1": ["RULE19"],
    "M2": ["RULE17", "RULE7"],
    "M3": [],
    "M4": ["RULE5", "RULE3", "RULE4"],
    "M5": ["RULE36", "RULE2"],
    "M6": ["RULE35", "RULE17", "RULE7"],
    "M7": [],
}

# Java rule 中文名 (来自规则未处理.xls)
JAVA_RULE_NAMES = {
    "RULE2":  "限定就医方式",
    "RULE3":  "限性别用药",
    "RULE4":  "限定儿童",
    "RULE5":  "限定医院类型级别",
    "RULE7":  "违反限定适应症(条件)用药",
    "RULE9":  "中药饮片审核",
    "RULE15": "医用材料与项目不符",
    "RULE17": "超限定频次",
    "RULE19": "重复收费",
    "RULE35": "超限定支付疗程",
    "RULE36": "非基本医疗保险目录",
}


def _validate_trigger_codes(rule_id: str, raw_codes) -> list[str]:
    """trigger_codes 每项去空白后 len≥2 且非空, 否则报错 (防短码前缀泛滥误召回)."""
    codes = raw_codes or []
    clean: list[str] = []
    for c in codes:
        s = str(c).strip()
        if len(s) < 2:
            raise ValueError(
                f"{rule_id}: trigger_codes 含过短/空白项 {c!r} (需 len≥2 防前缀泛滥)"
            )
        clean.append(s)
    return clean


def scan_javert_yamls() -> list[dict]:
    """扫所有 yaml, 抽 router 用得到的字段."""
    out: list[dict] = []
    seen: set[str] = set()
    for path in sorted(YAML_DIR.glob("*.yaml")):
        rule = load_rule(path)
        rid = rule.rule_id
        if rid in seen:
            raise ValueError(f"重复 rule_id: {rid}")
        seen.add(rid)
        record = {
            "rule_id": rid,
            "yaml_path": f"configs/rules/{path.name}",
            "domain": rule.domain,
            "violation_type": rule.violation_type,
            "status": rule.status,
            "priority": rule.priority,
            "rule_kind": rule.rule_kind,
            "clinical_criteria_ref": rule.clinical_criteria_ref,
            "derived_from_template": rule.derived_from_template,
            "trigger_keywords": list(rule.trigger_keywords),
            "trigger_codes": _validate_trigger_codes(rid, rule.trigger_codes),
            "question": rule.question[:200],
        }
        out.append(record)
    return out


def keyword_hits_in_dict(keywords: list[str], dict_keyword_index: dict[str, list[int]],
                          entries: list[dict], java_rule_filter: list[str]) -> dict[str, int]:
    """统计 yaml trigger_keywords 在 14372 字典里命中多少条目, 按 java rule 拆."""
    hits: dict[str, set[int]] = defaultdict(set)
    for kw in keywords:
        if not kw:
            continue
        for dict_kw, idx_list in dict_keyword_index.items():
            # 双向 substring 命中 (yaml keyword 是字典 item 的子串, 或反之)
            if kw in dict_kw or dict_kw in kw:
                for idx in idx_list:
                    rn = entries[idx]["java_rule_no"]
                    if rn in java_rule_filter:
                        hits[rn].add(idx)
    return {rn: len(s) for rn, s in hits.items()}


def build_mapping(yamls: list[dict], violation_dict: dict) -> dict:
    entries = violation_dict["entries"]
    keyword_index = violation_dict["keyword_index"]

    # 反向构建: java_rule_no → [javert_rule_ids]
    java_to_javert: dict[str, list[str]] = defaultdict(list)
    javert_to_java: dict[str, list[str]] = {}
    javert_only: list[str] = []
    template_summary: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for y in yamls:
        rid = y["rule_id"]
        if y.get("rule_kind", "audit") != "audit":
            continue
        tpl = y["derived_from_template"]
        candidates = TEMPLATE_TO_JAVA.get(tpl, [])

        if not candidates:
            # Case B: Javert-only (M3 口腔串换 / M7 项目身份串换 / 无 template)
            javert_only.append(rid)
            javert_to_java[rid] = []
            template_summary[tpl or "_no_template"]["javert_only"] += 1
            continue

        # 用 trigger_keywords 在字典里 cross-check, 记录 hit 数供调试; 但**所有 candidates 都纳入**
        # (一条 M4 yaml 在 RULE3/RULE4/RULE5 任何一个 java rule 触发时都该被 LLM 审, multi-route)
        kw_hits = keyword_hits_in_dict(y["trigger_keywords"], keyword_index, entries, candidates)
        for cand in candidates:
            java_to_javert[cand].append(rid)
        javert_to_java[rid] = candidates
        # template_summary 仍按 "强信号" java rule 归类 (用于人类阅读统计)
        chosen = candidates[0] if not kw_hits else max(candidates, key=lambda r: (kw_hits.get(r, 0), -candidates.index(r)))
        template_summary[tpl][chosen] += 1

    # 确保 11 个 java rule key 都存在 (即便没 yaml 对应)
    out = {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "total_javert_rules": len(yamls),
            "mapped_audit_rules": sum(
                1 for item in yamls if item.get("rule_kind", "audit") == "audit"
            ),
            "excluded_non_audit_rules": sum(
                1 for item in yamls if item.get("rule_kind", "audit") != "audit"
            ),
            "overlapping_rules": sum(len(v) for v in java_to_javert.values()),
            "javert_only_rules": len(javert_only),
            "by_template": {t: dict(d) for t, d in template_summary.items()},
        },
        "case_A_overlapping": {
            rn: {
                "java_rule_name": JAVA_RULE_NAMES.get(rn, "?"),
                "javert_rule_ids": sorted(java_to_javert.get(rn, [])),
                "javert_count": len(java_to_javert.get(rn, [])),
                "routing": "deterministic — javert rules execute ONLY if this java rule triggers",
            }
            for rn in ["RULE2", "RULE3", "RULE4", "RULE5", "RULE7", "RULE9",
                      "RULE15", "RULE17", "RULE19", "RULE35", "RULE36"]
        },
        "case_B_javert_only": {
            "comment": "These yaml rules have no Java engine counterpart. They run via high-level context pruning (domain/diagnosis/dept), NOT the deterministic 14372-dict lookup.",
            "rules": sorted(javert_only),
            "count": len(javert_only),
        },
        "reverse_index_javert_to_java": dict(sorted(javert_to_java.items())),
    }
    return out


def main() -> None:
    yamls = scan_javert_yamls()
    OUT_INDEX_PATH.write_text(
        json.dumps({"count": len(yamls), "rules": yamls}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[1/2] javert_rules_index.json  →  {len(yamls)} yaml")

    # status / priority 分布
    by_status: dict[str, int] = defaultdict(int)
    by_priority: dict[str, int] = defaultdict(int)
    by_template: dict[str, int] = defaultdict(int)
    for y in yamls:
        by_status[y["status"] or "_blank"] += 1
        by_priority[y["priority"] or "_blank"] += 1
        by_template[y["derived_from_template"] or "_blank"] += 1
    print(f"        status: {dict(by_status)}")
    print(f"        priority: {dict(by_priority)}")
    print(f"        template: {dict(by_template)}")

    violation_dict = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    mapping = build_mapping(yamls, violation_dict)
    OUT_MAPPING_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[2/2] configs/rule_mapping.json  →  {mapping['summary']['overlapping_rules']} overlapping + {mapping['summary']['javert_only_rules']} javert-only")
    print(f"        分布按 java rule:")
    for rn, info in mapping["case_A_overlapping"].items():
        print(f"          {rn} ({info['java_rule_name']}): {info['javert_count']} 条 javert rules")


if __name__ == "__main__":
    main()
