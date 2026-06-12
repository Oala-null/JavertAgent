## Why

m6-rollout 完成后 ready:65/82, 五模板 (M1/M2/M3/M5/M6) 闭环. M4 (超标准收费) 是最后一块, 之前因为缺诊疗目录被跳过, 现在<b>截至 2026-04-01 全量诊疗目录 (9794 条)</b> 已就位 (`截至20260401诊疗项目.xls`), 可以装.

12 条 E 类 (violation_type=超标准收费):
- 已 init drafting: R193 R196 R200 (3 条)
- MISSING: R020 R063 R074 R165 R212 R250 R291 R292 R293 (9 条)

**核心思路**: 不写新工具, 把诊疗目录条款<b>直接嵌入 vars catalog_basis 字段</b>, LLM 看 prompt 就能比对实际 fee 与目录单位/加成规则.

## What Changes

- **写 M4 设计 doc** `docs/templates/模板4_超标准收费.md` (已完成, phase 0)
- **装 M4.yaml** (空 → 完整, master_prompt + 14 fields + 量化计算分支)
- **init 9 条 MISSING** R020/R063/R074/R165/R212/R250/R291/R292/R293, R193/R196/R200 已 init
- **12 条 vars json**, 每条嵌入诊疗目录条款 (项目名 + 单位 + 价格 + 加成规则原文)
- **12 条 prompt-fit + ready** (priority 全 P0)
- 跑组 K audit-patient J66252

不在本期范围:
- ❌ 不写 service_catalog 工具 (vars 嵌入足够)
- ❌ 不引入完整 9794 条目录数据 (按需嵌入到对应 vars)

## Capabilities

### Modified Capabilities

- `rule-templating`: M4 模板装满 (master_prompt 含量化计算分支, catalog 条款嵌入字段)
- `rule-registry`: 12 条 E 类 ready, 全集 yaml 82 → 91

## Impact

- ready:65 → **77/91** (M1 15 + M2 18 + M3 17 + M5 8 + M6 7 + **M4 12**)
- 17 条 P2 drafting 中, R193/R196/R200 转 ready
- 覆盖 0325 表 ~59% 违规情形
- 零代码改动, 157 tests 全绿
