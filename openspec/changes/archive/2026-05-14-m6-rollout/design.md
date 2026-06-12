## Context

m5-rollout (2026-05-14) 完成后, 58 条 ready, M1/M2/M3/M5 四模板可用. M6 是闭环最后一块, 处理 violation_type=过度诊疗 主审 7 条 + 杂项 7 条 (留 P2). 设计 doc `docs/templates/模板6_过度诊疗.md` 已就位.

## Goals / Non-Goals

**Goals:**
- M6.yaml 模板装满 (13 fields, master_prompt ~700 chars)
- 14 条 M6 候选 init (7 主审 + 7 特殊)
- 7 条主审 ready (P0); 7 条特殊 drafting (P2 + notes 说明)
- 验收: 45 条 P0 (15 M1 + 15 M2 P0 + 8 M5 + 7 M6) 跑组 J, 期望某些 M6 触发 V 或 I (J66252 是择期全麻甲状腺手术, R218/R221 / R222 / R225 / R310 / R311 / R312 可能命中)

**Non-Goals:**
- ❌ 不动 M4
- ❌ 不实装 R280-R286

## Decisions

### D1. M6 与 M2 模板独立?

**选择独立 derived_from_template=M6**, 即使形态相近, 因为:
- M6 加了 `exclusion_dx_list` 硬证据字段 (M2 没有)
- 标识清晰利于审计报告分类

### D2. 7 条特殊 (R280-R286) 处理

**选 (b) P2 drafting + notes**, 与 m5-rollout R003/R004 一致策略.

### D3. priority 设置

- 7 条主审 (R218 R221 R222 R225 R310 R311 R312): **P0**
- 7 条特殊 (R280 R281 R282 R283 R284 R285 R286): **P2**

### D4. 验收门: 跑组 J

`audit-patient J66252 --share-tool-cache --concurrency 5`. 期望 45 条 P0 (15 M1 + 15 M2 + 8 M5 + 7 M6) 总耗时 ~11 min. 关注:
- R218/R221: J66252 是全麻手术, 应 CLEAN (有指征)
- R222: 看是否有"巨大甲状腺肿"描述, 可能 CLEAN 或 I
- R225/R310/R311/R312: 非精神/重症患者, 应 CLEAN (规则不适用)

## Migration Plan

1. 装 M6.yaml + init 14 条 + 改 priority
2. 7 条 vars + prompt-fit + ready
3. 跑组 J + 写文档 + 归档
