# -*- coding: utf-8 -*-
"""向 J61556 涉及的 ~20 条规则 yaml 注入 "📌 专家共识触发器" 段.

每条触发器内容是手写的, 基于 wangxin/jiweihui/zhoulihong 实际批注.
脚本只负责安全地 splice 到 prompt_addon block 末尾, 不破坏 yaml 结构.

执行: uv run python scripts/inject_expert_triggers.py
"""
from __future__ import annotations

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "configs" / "rules"

# rule_id -> 要插入的触发器文本 (会被 indent 2 空格嵌入 prompt_addon 末尾)
TRIGGERS: dict[str, str] = {
    # ─── 退化最严重 (V→C) ────────────────────────────────────
    "R153": """
📌 专家共识触发器 (v0.7, 来自 wangxin/jiweihui/zhoulihong 3 位专家 J61556 批注, 加权 13C/0V/0I):

CLEAN 触发器 (命中任一 → CLEAN 不再判 V):
- note_diagnosis 含任何恶性肿瘤 (淋巴瘤/胃肠癌/肺癌/乳癌/甲状腺癌/中枢神经系统肿瘤/转移癌/继发肿瘤等任何癌种) + AFP/甲胎蛋白收费 ≤ 2 次
  → wangxin: "肿瘤病人查肿瘤标志物属于正常诊疗行为, 不予认定"
  → jiweihui: "疑似肿瘤的, 检查一次不被认定为过度"
  → zhoulihong: "建议包括胸腹腔内器官的恶性肿瘤诊断, 如肺/胃癌等, 用以排查肝转移"
- search_lab_results(item_keyword="甲胎"或"AFP") diagnosisOpinion 含 "排查"/"随访"/"鉴别"/"恶性"

**重要**: 不要纠结 "癌种跟 AFP 字面关联强弱" — 中枢神经系统淋巴瘤查 AFP 仍属合理排转移. 这是 wangxin 明确表态.

反例 (仍 V): 完全无肿瘤诊断 + 健康体检 / 反复查 ≥3 次 + 无随访指征.
""",

    "R156": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui 加权 9C/4V/0I):

CLEAN 触发器:
- 男性 + note_diagnosis 含任何恶性肿瘤 + PSA 收费 ≤ 2 次
  → wangxin: "肿瘤病人查肿瘤标志物属于正常诊疗行为, 不予认定"
  → jiweihui: "男性肿瘤标志物可查一次"

反例 (仍 V): 女性 / 无任何肿瘤诊断的男性 + PSA / 反复查 ≥3 次.
""",

    "R203": """
📌 专家共识触发器 (v0.7, wangxin/zhoulihong 加权 9C/4V/0I, wangxin: "胡扯" 强烈反对):

CLEAN 触发器 (强支持):
- 患者有手术记录 (search_notes section="手术记录" 任一命中) + 文书任意位置出现"全麻"/"气管插管"/"麻醉医师"/"麻醉时间"/"麻醉方法"任一关键词
  → zhoulihong: "目前所有涉及全身麻醉的都被检出, 原因是未检索到麻醉记录文书. 可根据【手术记录】及【术后首次病程记录】中"麻醉方法"判断收费真实性. 因这两份文书是术后记录, 一定程度上可反映术中情况"
  → wangxin: "胡扯" (反对仅凭无麻醉记录就判 V)
- search_fees 命中具体麻醉药 (异丙酚/七氟烷/瑞芬太尼等) 或耗材 (气管导管/喉镜片) → CLEAN

INCONCLUSIVE 触发器 (回退仍无):
- 全部回退 section (手术记录/术后首次病程记录/查 keyword="全麻"/"气管插管"等) 0 命中 + 有手术耗材费 → INCONCLUSIVE conf 0.5 + etl_warning. **不要 V**.

反例 (V): 无任何手术 + 无任何麻醉药费 + 仅有全麻费 (极罕见, 真"虚构服务").
""",

    "R205": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui 加权 5C/4V/0I):

CLEAN 触发器:
- 患者本次住院 search_notes(section="手术记录") 命中 N 次手术 + search_fees 全麻费次数 ≤ N → **CLEAN**
  → wangxin: "全身麻醉按次收费, 患者做了 1 次手术, 收取 1 次全身麻醉费用, 没有问题"
- 麻醉记录 section 缺失 → 走 R203 共识 6 (ETL 缺失) 路线, INCONCLUSIVE 不 V

反例 (V): 全麻费次数 > 手术次数 + 明确时长拆分不合规 (≥2 小时 1 次但记录显示 1 小时).
""",

    "R220": """
📌 专家共识触发器 (v0.7, wangxin 加权 5C/4V/0I):

CLEAN 触发器:
- search_notes(section="手术记录") 或 keyword="切除"/"开颅"/"颞叶"/"脑膜"/"颅底"/"脑干"/"脊柱"/"血管外科"/"大型骨科" 任一命中 → **CLEAN**
  → wangxin: "违规认定不符合客观逻辑和诊疗常规"
- 控制性降压是颅内手术/大血管手术/出血风险大手术的麻醉常规, 不需 LLM 二次判断必要性

反例 (V): 浅表小手术 (甲状腺浅切 / 皮肤切除 / 痣切除) + 收控制性降压 / 无手术 + 收控制性降压.
""",

    "R279": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui/zhoulihong 加权 9C/4V/0I):

CLEAN 触发器:
- note_diagnosis 含淋巴瘤/白血病/多发转移/中枢神经系统肿瘤 → 多器官评估 (内分泌/性腺/生长激素等) 合理 → **CLEAN**
  → wangxin: "淋巴瘤有可能累及内分泌系统, 有检查的必要性"
  → jiweihui: "入院常规都查, 建议改为提醒"
- 单项目 ≤ 2 次 → CLEAN

反例 (V): 局部良性疾病 + 套餐 ≥3 类全打包 + 完全无对应指征.
""",

    "R278": """
📌 专家共识触发器 (v0.7, wangxin/zhoulihong 加权 5C/8I/0V):

CLEAN 触发器:
- note_diagnosis 含淋巴瘤/中枢神经系统肿瘤/颅内占位/垂体占位/肾上腺占位/全身性血液肿瘤 → 内分泌套餐合理 → **CLEAN**
  → wangxin: "淋巴瘤有可能累及内分泌系统, 有检查的必要性"
  → zhoulihong: "弥漫大B细胞淋巴瘤(中枢、肾上腺、胃部(待定))及颅内占位性病变, 查肾上腺及垂体激素类检查有鉴别诊断的合理性, 但缺少相关临床表现支撑, 建议定为疑似, 需线下核查"
- 4 类内分泌项目命中 ≤ 2 类 → CLEAN (单项可能合理)

INCONCLUSIVE 触发器:
- 4 类命中 ≥ 3 类 (套餐式打包) + 系统性肿瘤诊断模糊 → INCONCLUSIVE (zhoulihong 共识)

反例 (V): 4 类全命中 + 完全局部良性疾病 + 无任何脏器累及证据.
""",

    "R131": """
📌 专家共识触发器 (v0.7, wangxin/zhoulihong 加权 5C/4I/4V, wangxin C 优先):

CLEAN 触发器:
- note_diagnosis 含恶性肿瘤 (尤其淋巴瘤/系统性) → wangxin: "淋巴瘤有可能累及心血管系统, 且手术病人术前需要排除心肺异常, 故不予认定"
- search_notes(section="手术记录" 或 keyword="手术") 命中四级手术/大型手术 + 全身麻醉 → 术前心评常规 → **CLEAN**
  → zhoulihong: "幕上深部病变切除术, 属于四级手术, 且全麻, 术前行心脏彩超检查评估心脏情况有一定合理性"
- search_examinations(check_type="超声"或"心超") 命中心脏超声报告 (报告日期在手术日前) → CLEAN

INCONCLUSIVE 触发器:
- 单项心脏彩超 + 患者高龄 (≥60) + 任何手术 → CLEAN
- 心脏彩超 + 左心功能 + TDI 三项打包 + 完全无心脏症状/手术指征 → INCONCLUSIVE

反例 (V): 完全无手术 + 完全无肿瘤 + 体检/普通入院 + 心彩超 + 左心 + TDI 三项打包.
""",

    "R291": """
📌 专家共识触发器 (v0.7, wangxin 加权 5C/0V/0I):

CLEAN 触发器:
- search_fees 无精神科监护类项目 (封闭式精神病专科病区监护/精神科特殊监护) → **CLEAN**
  → wangxin: "无精神科监护的收费"
- 若该规则在该患者不适用 (fee 无精神监护项目) → CLEAN

反例 (V): 普通病房收"封闭式精神病专科病区监护费" + 医院 hospital_config 标注无该科室.
""",

    # ─── INCONCLUSIVE 倾向 (wangxin "未见医嘱单/检查报告单" → I) ────
    "R015": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui/zhoulihong 加权 8V/5I/0C, wangxin I 优先):

INCONCLUSIVE 触发器 (wangxin 标准):
- fee 命中具体项目 + 文书无医嘱单 + 无检查报告单 + 病程记录无相关描述 → **INCONCLUSIVE conf 0.5 + etl_warning**
  → wangxin: "未见医嘱单, 未见检查报告单, 病程记录无相关描述"

V 触发器 (强证据):
- 多次反复收费 + 完全无任何手术/检查痕迹 + 无任何相关诊断 → V (jiweihui 标准: "提供不必要医药服务")

不要 V 仅基于"找不到记录" — wangxin 明确 I 倾向.
""",

    "R103": """
📌 专家共识触发器 (v0.7, wangxin I, jiweihui/zhoulihong V, 加权 8V/5I/0C):

INCONCLUSIVE 触发器 (wangxin 优先):
- search_fees 命中影像项目 N 次 + search_examinations 拉到的影像报告数 < N + 文书无明确报告号/阅片记录 → **INCONCLUSIVE conf 0.5 + etl_warning**
  → wangxin: "未见医嘱单, 未见检查报告单, 病程记录无相关描述"

V 触发器 (反例):
- search_examinations 完全 0 命中 + search_notes 影像段也 0 + 仅有具体影像费 → 强 V 信号

**优先用 search_examinations 工具 — 比 search_notes 影像段更权威**.
""",

    "R105": """
📌 专家共识触发器 (v0.7, wangxin I 优先):

INCONCLUSIVE 触发器:
- 同 R103 — fee 命中 + 部分报告缺失 → INCONCLUSIVE 不 V
  → wangxin: "未见检查报告单, 且需要结合PACS系统影像图像进行判定"

要 V 必须 search_examinations 完全 0 + search_notes 影像段 0 + 多次反复收费.
""",

    "R224": """
📌 专家共识触发器 (v0.7, wangxin I, jiweihui/zhoulihong V, 加权 8V/5I/0C):

INCONCLUSIVE 触发器 (wangxin):
- 监护类费用收 N 次 + 文书无 ICU/重症记录 + 病程无生命体征危机描述 → **INCONCLUSIVE conf 0.5**
  → wangxin: "未见医嘱单, 未见检验报告单"

V 触发器 (jiweihui):
- 监测项目按数值/参数分解收费 (如收血压 + 收心率 + 收呼吸 = 3 次, 而非 1 次"生命体征监测") → V
  → jiweihui: "监测数值分解收取"
""",

    "R034": """
📌 专家共识触发器 (v0.7, wangxin C, jiweihui I, 加权 5C/4I/0V):

INCONCLUSIVE 触发器 (统一立场):
- 耗材费 命中 + 文书无 "植入"/"装入"/"使用 [耗材名]" → **INCONCLUSIVE conf 0.5 + 标注"需线下核查条码"**
  → wangxin: "需要查看耗材粘贴条码"
  → jiweihui: "建议条形码"

不要 V 除非反复多次 + 完全无任何手术记录.
""",

    "R026": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui 加权 9I/0V/0C, 规则措辞需改):

INCONCLUSIVE 触发器:
- 心电监测与动态心电图或遥测心电监护任意 2 项同收 → **INCONCLUSIVE conf 0.5 + 标注"规则措辞模糊, 建议线下复核"**
  → jiweihui: "心电监测与动态心电图或遥测心电监护不能同时收费 — 规则措辞需改"
""",

    "R112": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui C, zhoulihong I, 加权 9C/4I/0V):

CLEAN 触发器:
- search_examinations(check_type="放射" 或 "磁共振") 显示 A 类(增强)+B 类(平扫) 在不同日期/不同部位/不同时相 → **CLEAN**
  → wangxin: "非同一时间"
  → jiweihui: "不同时间"

INCONCLUSIVE 触发器:
- search_examinations 影像报告完全 0 命中 / 同日同部位但报告时相描述模糊 → INCONCLUSIVE conf 0.5
  → zhoulihong: "影像报告文书缺失 (ETL_GAP), 无法证明平扫与增强的部位/时相差异. 无法证明的应该定为疑似行为, 建议线下核查"

V 触发器: 同日同部位 + 报告无任何时相/部位差异说明 + 多次反复打包.
""",

    # ─── 高一致 V 模式 (防止 v1.3 退化为 C) ────────────────
    "R141": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui/zhoulihong 加权 13V/0/0):

V 触发器 (强一致 V — 保持判 V):
- 同检查 ≥3 天连续收费 + search_notes(keyword="胸痛"/"胸闷"/"气短") 0 命中 → **V**
  → wangxin: "无指征反复复查, 属于过度检查"
  → zhoulihong: "建议增加日常查房记录的检索"

不要因肿瘤诊断就改 C — 这条专家明确反复检查无意义为 V.
""",

    "R143": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui/zhoulihong 加权 10V/1I/0):

V 触发器:
- 同 R141 模式 — 反复复查 + 病程无症状 → V
  → wangxin: "无指征反复复查, 属于过度检查"
""",

    "R146": """
📌 专家共识触发器 (v0.7, 13V/0I/1C — 几乎全 V):

V 触发器:
- 同 R141 — 反复复查模式 → V
  → wangxin: "无指征反复复查, 属于过度检查"
- search_notes(keyword="胸闷"/"气短"/"喘"/"憋"/"下肢水肿") 全 0 命中 → V 强信号
""",

    "R160": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui/zhoulihong 加权 13V/0/0):

V 触发器:
- 连续 ≥3 天检测 + 病程记录无相关症状 → V
  → wangxin: "连续检测 4 天, 病程记录无相关记录, 无法体现合理性"
  → zhoulihong: "建议增加日常查房记录的检索"

不要因术后就 CLEAN — 必须有具体监护指征 (休克/感染/重症).
""",

    "R161": """
📌 专家共识触发器 (v0.7, wangxin/zhoulihong 加权 10V/0/0):

V 触发器: 同 R160 — 反复检测 + 无相应症状 → V
  → wangxin: "无指征反复复查, 属于过度检查"
""",

    "R155": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui/zhoulihong 加权 10V/2I/1C):

V 触发器:
- 同检查 ≥3 天连续收费 + 病程记录无感染/炎症/术后监测描述 → **V**
  → wangxin: "连续检测 4 天, 病程记录无相关记录, 无法体现合理性"
- 4 类细胞因子 (IL-6 + IL-2 + IFN + TNF) 打包 → V

CLEAN 触发器:
- 1-2 次单项 + 感染/自身免疫/肿瘤靶向治疗指征 → CLEAN
- search_lab_results(item_keyword="IL-6"或"细胞因子") diagnosisOpinion 含"感染"/"风湿"/"靶向治疗" → CLEAN
""",

    "R151": """
📌 专家共识触发器 (v0.7, wangxin/jiweihui 加权 9V/0/0):

V 触发器:
- 乙肝病毒 DNA / 丙肝病毒 RNA / 巨细胞病毒 DNA 等病毒载量定量检测 + 无任何肝炎/感染病史 → **V**
  → wangxin: "手术病人可以检查传染指标, 但乙肝病毒 DNA 测定不是常规检查项目, 无相关病史不应检查"

CLEAN 触发器:
- 乙肝表面抗原/丙肝抗体/HIV/梅毒 等抗体筛查 (术前传染指标) → CLEAN (是常规)
- 乙肝病毒 DNA + note_diagnosis 含 "乙肝"/"病毒性肝炎"/"肝硬化"/"肝炎活动" → CLEAN
""",
}


def inject(rule_id: str, trigger_text: str) -> bool:
    yaml_path = RULES_DIR / f"{rule_id}.yaml"
    if not yaml_path.exists():
        print(f"  [SKIP] {rule_id} yaml 不存在", file=sys.stderr)
        return False
    text = yaml_path.read_text(encoding="utf-8")
    if "📌 专家共识触发器" in text:
        print(f"  [SKIP] {rule_id} 已有触发器 (不重复注入)")
        return False
    # 找 prompt_addon block 末尾 — 它后面通常是 `trigger_keywords:` 或其他顶级 key
    # 简单做法: 解析 yaml, 修改 prompt_addon, 重新 dump
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        print(f"  [ERR] {rule_id} yaml parse fail: {e}", file=sys.stderr)
        return False
    if not isinstance(data, dict):
        return False
    existing = data.get("prompt_addon") or ""
    new_addon = existing.rstrip() + "\n\n" + trigger_text.strip() + "\n"
    data["prompt_addon"] = new_addon
    # 保留其他字段顺序 — 用 sort_keys=False
    new_text = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)
    yaml_path.write_text(new_text, encoding="utf-8")
    print(f"  [OK]   {rule_id} 注入 ({len(trigger_text)} chars)")
    return True


def main():
    n_ok = 0
    for rid in sorted(TRIGGERS):
        if inject(rid, TRIGGERS[rid]):
            n_ok += 1
    print(f"\n注入完成: {n_ok}/{len(TRIGGERS)}")


if __name__ == "__main__":
    main()
