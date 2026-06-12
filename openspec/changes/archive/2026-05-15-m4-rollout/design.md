## Context

诊疗目录就位 (`截至20260401诊疗项目.xls` 9794 条, 含项目名/内涵/计价单位/价格/备注加成规则). M4 是第 5 模板 (最后一块), 解锁 12 条 E 类超标准收费规则.

M4 与其他模板的核心差异: **需要量化计算**. LLM 不只是判 "命中/不命中", 还要按目录规则计算应收次数 vs 实收次数. 例如:
- R020: 3 支血管介入应收 1 + 2 × 20% = 1.4 次价格, 收 3 次全价 → 超 1.6 次
- R074: 4 小时血液透析应收 1 次, 收 4 次 → 超 3 次
- R200: 1 疗程 10 次定位应收 1 个疗程, 收 10 次 → 超 9 次

## Goals / Non-Goals

**Goals:**
- M4 模板装满, 含量化计算引导 (LLM 按 catalog_unit + addon_rule 算应收 vs 实收)
- 12 条 E 类 ready, 每条 vars 嵌入诊疗目录精确条款
- ready 65 → 77 (覆盖 0325 表 ~59%)

**Non-Goals:**
- ❌ 不写 service_catalog 工具 (按需嵌入足够)
- ❌ 不引入完整 9794 条目录 (后续若 add-catalog-loader 才需要)

## Decisions

### D1. 诊疗目录如何输入 prompt

**选 (b) vars catalog_basis 字段嵌入**, 不引新工具:
- (a) 写 service_catalog(name) 工具 → 需新代码, 12 条规则各 1-3 个查询, 慢
- (b) **vars catalog_basis 字符串嵌入** → 一次性把项目/单位/价格/加成规则写进 prompt, LLM 直接看到
- (c) 引入完整目录 RAG → 过度工程

选 b 是因为: 12 条 E 类的目录条款都是<b>固定文本</b>, 不会随患者变化, 嵌入 vars 就是最简洁的方案.

### D2. 字段集

M4 字段 14 个:

| 字段 | 类型 | required | 说明 |
|------|------|---------|------|
| service_name | str | true | 项目名 (与目录精确匹配) |
| service_kw_list | list[str] | true | search_fees 检索词 |
| catalog_unit | str | true | 目录计价单位 (次/疗程/例/小时/牙/根管/支血管/...) |
| catalog_price | str | false (default "") | 目录基础价 (字符串便于多档表达) |
| addon_rule_text | str | true | 目录加成/加收规则原文 |
| violation_pattern | str | true | 典型违规模式 (按次→按时长 / 按例→按蜡块 等) |
| actual_event_kw_list | list[str] | true | 从文书提取实际事件量化关键词 |
| notes_section | str | false (default "") | 文书 section (手术记录/治疗记录) |
| expected_calc_hint | str | true | 应收计算提示 (告诉 LLM 怎么算) |
| pilot_caveat | str | false (default "") | pilot 适用性 |
| special_notes | list[str] | false (default []) | 特殊注意 |
| aux_keywords | list[str] | true | trigger_keywords |
| aux_tools | list[str] | true | suggested_tools |
| aux_signal | str | true | expected_signal |

### D3. R212 特殊处理 (麻醉恢复室)

R212 不是"按计价单位多收", 是"无该科室仍收费". 它跟其他 11 条 M4 形态不同, 但可以套 M4 模板:
- service_name: 麻醉后复苏监护 (PACU)
- catalog_unit: -
- addon_rule_text: 须在麻醉恢复室内监测; 医院须设 PACU
- violation_pattern: 无 PACU 但收监护费
- pilot_caveat: J66252 fee 有 "麻醉后复苏监护(PACU) ¥300", 需医院级 PACU 配置确认

LLM 跑 R212 时, 若 fee 命中, 应建议 "需医院配置确认", 输出 INCONCLUSIVE (等同事提供医院 PACU 配置后再判).

### D4. R291 特殊处理 (精神科监护内涵)

R291 不是单纯量化, 是"内涵不符" (4 维度: 适用对象/监护内容/病区环境/人员资质). LLM 难全面审, 当前能审的:
- 患者诊断是否含急性发作期指征 (note_diagnosis)
- 文书是否有班次/监护人员描述

设 pilot_caveat: "当前 4 工具可审诊断指征; 病区环境/人员资质需医院级核实 (留 INCONCLUSIVE)".

### D5. 验收

跑 `audit-patient J66252 --share-tool-cache --concurrency 5` 组 K. 期望:
- 45 + 7 = 52 条 P0 (M4 12 条全 P0)
- M4 子集: R212 可能 INCONCLUSIVE (PACU 配置未知), 其余多数 CLEAN (J66252 fee 无 PTCA/HIFU/血液净化等)
- 总耗时 ~12 min

## Risks

**[R1] LLM 量化计算可能出错** → R020 算 3 支血管应收次数, LLM 可能算错. Mitigation: expected_calc_hint 给具体例子, 让 LLM 套.

**[R2] 目录条款嵌入 vars 太长** → master_prompt 渲染后 > 1500 chars. Mitigation: 接受, 现 prompt avg 600-900c, M4 加到 1500c 仍可控.

**[R3] R212 R291 量化模式不符** → M4 模板可能套不下. Mitigation: pilot_caveat 兜底 + 个别情况下接受 INCONCLUSIVE.

## Migration Plan

1. 装 M4.yaml + init 9 条骨架 + 改 R193/R196/R200 priority P0
2. 12 条 vars json (批量脚本生成 + 个别手调)
3. prompt-fit + ready
4. 跑组 K + 写文档 + 归档
