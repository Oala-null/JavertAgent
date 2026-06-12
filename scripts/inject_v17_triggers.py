# -*- coding: utf-8 -*-
"""v1.7 修最后 4 条 (R112 / R131 / R155 / R224). R015 留着 (ambiguity 难修)."""
from pathlib import Path
import yaml
import sys

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "configs" / "rules"

V17: dict[str, str] = {
    "R112": """
⚡ v1.7 STEP 0a (修 v1.6 没对比日期):
**search_fees(keyword="增强") 和 search_fees(keyword="平扫") 返回会带 [yyyy-mm-dd] 日期**.
- 若两者命中 + 同一日期 → 进 STEP 0 报告对比
- **若两者命中 + 不同日期 → 直接 CLEAN conf 0.90**
  → wangxin: "非同一时间" / jiweihui: "不同时间"

不要因 search_examinations 缺 "CT 增强" 报告就 INCONCLUSIVE — 先看 fee 日期.
""",

    "R131": """
⚡ v1.7 STEP 0a (修 v1.6 26 次工具死循环):
**只调一次 search_fees(keyword="心脏彩超") + search_fees(keyword="左心功能") + search_fees(keyword="TDI")**.
- **3 个都 0 命中 → 立刻 CLEAN conf 0.95 (规则不适用, 不再搜)**
- 任一命中 → 进 STEP 0 (检查指征 / experience.md 共识)

⚠️ **禁止** 工具调用超过 8 次. 找不到 fee 命中 = 立刻 CLEAN. 不要搜术前/入院/手术 等关键词去推断 "可能有指征".
""",

    "R155": """
⚡ v1.7 STEP 0a (修 v1.6 27 次工具死循环):
**只调 search_lab_results(item_keyword="IL-6") 和 search_lab_results(item_keyword="细胞因子")**.
- **2 个都 0 命中 → 立刻 CLEAN conf 0.95** (规则不适用, 患者没做这检验)
- 任一命中 + 跨 ≥3 天 → V 0.85
- 命中但跨 ≤2 天 → CLEAN (有指征或单次)

⚠️ **禁止** 反复搜 "感染/炎症/发热/脓毒症" 等症状关键词 (LLM 上 2 轮在这死循环).
""",

    "R224": """
⚡ v1.7 强 INCONCLUSIVE 默认 (修 v1.6 找到 lab 就 CLEAN):
wangxin baseline 是 I (= "未见医嘱单/检验报告单"), 不是 C.
- fee 命中监护项目 + 找到 lab 检验报告 → **仍走 INCONCLUSIVE conf 0.55**
  → wangxin 标准: 即便找到检验报告, "医嘱单" 缺失就应 I (需线下核查)
- 唯一 V 路径: fee 显示按数值分项收费 (如同日血压 + 心率 + 呼吸 3 项分开收) → V
- 唯一 CLEAN 路径: fee 0 命中 (规则不适用)

⚠️ R224 默认 I, 不要因 search_lab_results 拿到检验数据就给 C.
""",
}


def inject(rule_id: str, addition: str) -> bool:
    path = RULES_DIR / f"{rule_id}.yaml"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if "⚡ v1.7" in text:
        print(f"  [SKIP] {rule_id} v1.7 already")
        return False
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    existing = data.get("prompt_addon") or ""
    data["prompt_addon"] = addition.strip() + "\n\n" + existing.rstrip() + "\n"
    path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000),
        encoding="utf-8",
    )
    print(f"  [OK] {rule_id} (+{len(addition)} chars)")
    return True


def main():
    n = 0
    for rid in V17:
        if inject(rid, V17[rid]):
            n += 1
    print(f"\n{n}/{len(V17)} injected")


if __name__ == "__main__":
    main()
