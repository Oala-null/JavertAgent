# -*- coding: utf-8 -*-
"""v1.6 修 5 条 wangxin 不一致.

R143: 跨 ≥3 天直接 V, 不要找症状 (LLM 死循环根因)
R105/R015: 关键词太严, 加 fuzzy 别名搜索建议
R112: STEP 0 先对比平扫/增强日期
R205: STEP 0 一手术一全麻 → CLEAN, 不查时长
"""
from pathlib import Path
import yaml
import sys

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "configs" / "rules"

V16: dict[str, str] = {
    "R143": """
🔥 v1.6 STEP 0 (最高优先, 修 v1.5 32 次工具死循环):
**只调一次 search_lab_results(item_keyword="乳酸"或"血气"或规则点名项目)**.
若返回头部 "📊 反复检测统计" 显示该项目跨 ≥3 天 → **VIOLATION conf 0.85**.

**禁止** 反复搜 "休克"/"脓毒症"/"缺氧"/"灌注"/"酸中毒" 等症状关键词 (LLM 之前在这里死循环 32 次).
wangxin 标准: 跨 ≥3 天反复检测就是 V, 不需要病程验证症状.
""",

    "R146": """
🔥 v1.6 STEP 0 (最高优先):
**只调一次 search_lab_results(item_keyword=规则点名项目)**. 跨 ≥3 天 → **V conf 0.85**.
不要找症状关键词. 反复检测即 V.
""",

    "R155": """
🔥 v1.6 STEP 0 (最高优先):
**只调一次 search_lab_results(item_keyword="IL-6"或"细胞因子")**. 跨 ≥3 天 → **V conf 0.85**.
不要搜感染/化疗背景找借口 — 反复检测就是 V.
""",

    "R141": """
🔥 v1.6 STEP 0 (最高优先):
**search_lab_results(item_keyword=规则点名项目)** 看跨天. ≥3 天 → V 0.85. 不要找症状.
""",

    "R160": """
🔥 v1.6 STEP 0 (最高优先):
**search_lab_results(item_keyword=规则点名项目)** 看跨天. ≥3 天 → V 0.85.
""",

    "R161": """
🔥 v1.6 STEP 0 (最高优先):
**search_lab_results** 看跨天. ≥3 天 → V 0.85.
""",

    "R151": """
🔥 v1.6 STEP 0:
**search_lab_results(item_keyword="乙肝病毒"或"HBV DNA"或"DNA定量")**.
若 rpt_itemname 含 "病毒 DNA 定量" + note_diagnosis 无肝炎/肝硬化 → **V** (wangxin 共识).
""",

    "R015": """
🔥 v1.6 STEP 0 (修 v1.5 关键词太严):
**search_fees 必须搜多个别名**, 任一命中即继续 (不要凭一个关键词 0 命中就 CLEAN):
- "有创性血流动力学"  "动脉测压"  "中心静脉压"  "漂浮导管"  "PiCCO"  "Swan-Ganz"  "PA"  "CVP"  "BP 监测"

若任一命中 + 文书无医嘱单 + 无操作记录 + 病程无对应描述 → **INCONCLUSIVE conf 0.50 + etl_warning** (wangxin: "未见医嘱单/检查报告单/病程记录无描述").

唯一 CLEAN 路径: search_fees 全部别名都 0 命中 (规则不适用).
""",

    "R105": """
🔥 v1.6 STEP 0 (修 v1.5 关键词太严):
**search_fees 多别名搜**, 任一命中即继续:
- "三维重建"  "三维"  "3D"  "MIP"  "MPR"  "VR"  "立体"  "重建"  "X线计算机体层(三维)"  "CT 三维"  "冠状面重建"

若任一命中 → **必须**调 search_examinations(check_type="放射") 拉所有放射报告.
- 若任一报告含 "三维"/"重建"/"MIP"/"MPR"/"VR" → CLEAN
- 若所有报告都不含 + 文书也不含 → **INCONCLUSIVE conf 0.50** (wangxin: "需结合 PACS 影像图像判定", 不能凭关键词搜不到就 V)

唯一 CLEAN 路径: search_fees 全部别名 0 命中.
""",

    "R112": """
🔥 v1.6 STEP 0 (修 v1.5 没对比日期):
**步骤强制**:
1. search_fees(keyword="增强") + search_fees(keyword="平扫"), 看 A/B 类都命中
2. **若都命中, 必调 search_examinations(check_type="放射")** 拉所有放射报告
3. **对比 checkDate 字段** 看平扫和增强是否在不同日期:
   - 不同日期 / 不同 checkPosition → **CLEAN** (wangxin/jiweihui: "非同一时间"/"不同时间")
   - 同日同部位 + 报告无时相说明 → V
4. 若 search_examinations 0 命中 → INCONCLUSIVE

不要凭"影像报告不全"就 I — 必须先看 search_examinations 的日期对比.
""",

    "R205": """
🔥 v1.6 STEP 0 (修 v1.5 LLM 卡在找时长):
**最高优先 CLEAN 路径** (wangxin 标准: 1 手术 1 全麻 = CLEAN):
1. search_fees(keyword="全身麻醉") 算 fee 次数 N
2. search_notes(section="手术信息" 或 keyword="手术名称") 算手术次数 M
3. **若 M ≥ N → 直接 CLEAN conf 0.90** (无需查时长)
   → wangxin: "全身麻醉按次收费, 患者做了 1 次手术, 收取 1 次全身麻醉费用, 没有问题"
4. 仅当 N > M (全麻费多于手术次数) 才需要查具体时长 → 此时按麻醉记录走时长判定.

⚠️ 不要因为找不到 HH:MM 精确时长就 INCONCLUSIVE — wangxin 标准不要时长.
""",

    "R224": """
🔥 v1.6 INCONCLUSIVE 强化:
wangxin (I=5) + jiweihui/zhoulihong (V=8) 分歧 → 走中位 I.
- fee 命中监护项目 + 文书无 ICU/重症记录 → **I conf 0.55** (wangxin: "未见医嘱单, 未见检验报告单")
- 仅在 fee 显示"按数值/参数分项收费"(如血压收 1 次 + 心率收 1 次同日) 才 V.

R224 默认走 I, 不要 C 不要 V.
""",
}


def inject(rule_id: str, addition: str) -> bool:
    path = RULES_DIR / f"{rule_id}.yaml"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if "🔥 v1.6" in text:
        print(f"  [SKIP] {rule_id} v1.6 already")
        return False
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    existing = data.get("prompt_addon") or ""
    new = addition.strip() + "\n\n" + existing.rstrip() + "\n"
    data["prompt_addon"] = new
    path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000),
        encoding="utf-8",
    )
    print(f"  [OK] {rule_id} (+{len(addition)} chars)")
    return True


def main():
    n = 0
    for rid in V16:
        if inject(rid, V16[rid]):
            n += 1
    print(f"\n{n}/{len(V16)} injected")


if __name__ == "__main__":
    main()
