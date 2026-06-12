# -*- coding: utf-8 -*-
"""v1.5 — 修正 v1.4 退化, 加 STEP 0 V 优先触发器到 R143/R155, 加强 R105/R015/R224 I 触发器.

针对 v1.4 J61556 一致率退化到 63%, R146/R143/R155 V→C 全错。
关键: LLM 用 experience.md 共识 2/3 当通行证, 跳过了 V 触发器验证.
"""
from pathlib import Path
import yaml
import sys

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "configs" / "rules"

V15_ADDITIONS: dict[str, str] = {
    "R143": """
🚨 v1.5 STEP 0 (V 硬优先, 命中即 V 不进任何 CLEAN 判断):
**先调 search_lab_results(item_keyword="乳酸"或"动脉血乳酸")**, 看返回头部 "📊 反复检测统计".
- 该项目跨 ≥3 天 → 直接 **VIOLATION conf 0.85+** (wangxin 共识 V=13 满分, 任何 experience 共识都不能豁免)
- 跨 ≤2 天 → 进入 CLEAN 触发器判定

⚠️ 不要因为患者有"肿瘤"或"ICU"诊断就直接 CLEAN. 即便患者在 ICU, 反复 ≥3 天检测乳酸 + 病程无 "休克/灌注/酸中毒" 字面描述 → 仍是过度检查 (wangxin: "无指征反复复查").
""",

    "R146": """
🚨 v1.5 STEP 0 (V 硬优先):
**先调 search_lab_results(item_keyword=本规则点名项目, 如"BNP"或"心肌酶"等)** 看头部 "📊 反复检测统计".
- 跨 ≥3 天 → 直接 **VIOLATION** (wangxin 共识 V=13)
- 跨 ≤2 天 → 进入 CLEAN 触发器

⚠️ 不要因为"淋巴瘤累及心血管"(共识 2) 或"重大手术术前评估"(共识 3) 就给 C. 反复检测 ≥3 天是独立 V 触发器, 优先级高于 experience.md 任何共识.
""",

    "R155": """
🚨 v1.5 STEP 0 (V 硬优先):
**先调 search_lab_results(item_keyword="IL-6"或"细胞因子"或"TNF"或"白介素")** 看头部 "📊 反复检测统计".
- 该规则点名项目跨 ≥3 天 → **VIOLATION** (wangxin: "连续检测 4 天, 病程记录无相关记录")
- 跨 ≤2 天 → 进入 CLEAN 触发器判定

⚠️ 即便患者在化疗/ICU/免疫治疗背景下, 同项目反复 ≥3 天测仍是过度. 共识 2/3 不豁免 V.
""",

    "R141": """
🚨 v1.5 STEP 0 (V 硬优先, R141/R143/R146/R160/R161 同模式):
**先调 search_lab_results(item_keyword=规则点名项目)** 看 "📊 反复检测统计".
- 跨 ≥3 天 → **V** (无论患者诊断如何, wangxin 共识 V=13)
""",

    "R160": """
🚨 v1.5 STEP 0 (V 硬优先):
**先调 search_lab_results(item_keyword=...)** 看 "📊 反复检测统计".
- 跨 ≥3 天 + 病程记录无对应症状描述 → **V** (wangxin: "连续检测 4 天, 病程记录无相关记录")
""",

    "R161": """
🚨 v1.5 STEP 0 (V 硬优先):
**先调 search_lab_results 看跨天统计**. 跨 ≥3 天 → **V**.
""",

    "R151": """
🚨 v1.5 STEP 0 (V 强信号):
**先 search_lab_results(item_keyword="乙肝病毒"或"HBV DNA")** 看返回是否真做了 + diagnosisOpinion.
- rpt_itemname 含 "乙肝病毒 DNA 定量" + note_diagnosis 无 "乙肝/肝炎/肝硬化" → **V** (wangxin: "无相关病史不应检查")
- 普通乙肝 5 项 / HBV 抗体 → 是术前传染指标, CLEAN
""",

    # ─── INCONCLUSIVE 强化 (wangxin "未见医嘱单/检查报告单 → I") ────
    "R015": """
🚨 v1.5 INCONCLUSIVE 硬规则 (wangxin 共识 I=5 + jiweihui/zhoulihong V=8 → 加权分歧, 走中位 I):
- fee 命中 + 主 section 0 命中 + search_examinations 也无对应报告 → **INCONCLUSIVE conf 0.50 + etl_warning**
- 不要凭 "找不到 = CLEAN" — wangxin 明确: "未见医嘱单/检查报告单/病程记录无相关描述" 应 I 不是 C 不是 V
""",

    "R105": """
🚨 v1.5 INCONCLUSIVE 硬规则 (wangxin I 优先):
- fee 命中具体影像项目 (如"三维重建") + search_examinations 拉到 N 条放射报告但 keyword 搜不到具体执行描述 → **INCONCLUSIVE conf 0.50 + etl_warning**
- wangxin 明确: "未见检查报告单, 且需要结合 PACS 系统影像图像进行判定" — 不能凭 keyword 搜不到就 V
- 要 V 必须 search_examinations 完全 0 命中 + search_notes 完全 0 提及该项目 + 多次反复收费 + 完全无相关手术/检查痕迹
""",

    "R103": """
🚨 v1.5 INCONCLUSIVE 硬规则:
- fee 命中 + search_examinations 拉到部分报告但条数 < fee 笔数 → **INCONCLUSIVE conf 0.50**
- 不要因为 "差额无证据" 就 V — 走 I (wangxin 标准)
""",

    "R224": """
🚨 v1.5 INCONCLUSIVE 硬规则 (wangxin I 优先):
- 监护类费用收 N 次 + 文书无 ICU/重症记录 + 病程无生命体征危机 → **INCONCLUSIVE conf 0.50**
- wangxin: "未见医嘱单, 未见检验报告单" → 应 I 不是 C
- jiweihui V 模式: 监测数值分解收取 (血压/心率/呼吸分开收) — 仅在能在 fee 看到分项才 V

⚠️ R224 默认走 INCONCLUSIVE, 除非看到明确"分解收费"模式才 V.
""",
}


def inject(rule_id: str, additions: str) -> bool:
    yaml_path = RULES_DIR / f"{rule_id}.yaml"
    if not yaml_path.exists():
        print(f"  [SKIP] {rule_id} not found", file=sys.stderr)
        return False
    text = yaml_path.read_text(encoding="utf-8")
    if "🚨 v1.5" in text:
        print(f"  [SKIP] {rule_id} v1.5 already injected")
        return False
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        print(f"  [ERR] {rule_id}: {e}", file=sys.stderr)
        return False
    if not isinstance(data, dict):
        return False
    existing = data.get("prompt_addon") or ""
    # 把 v1.5 additions 塞在 prompt_addon 最前面 (优先级最高)
    new_addon = additions.strip() + "\n\n" + existing.rstrip() + "\n"
    data["prompt_addon"] = new_addon
    new_text = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)
    yaml_path.write_text(new_text, encoding="utf-8")
    print(f"  [OK] {rule_id} ({len(additions)} chars prepended)")
    return True


def main():
    n_ok = 0
    for rid in V15_ADDITIONS:
        if inject(rid, V15_ADDITIONS[rid]):
            n_ok += 1
    print(f"\n{n_ok}/{len(V15_ADDITIONS)} injected")


if __name__ == "__main__":
    main()
