## Why

m5-rollout 完成: 四模板 (M1/M2/M3/M5) 共 58 条 ready, V=0 C=57 I=1 (J66252). m6-rollout 是收尾之作, 完成 user goal "迭代到 m6 为止覆盖所有 Y 规则".

M6 (过度诊疗) 处理 violation_type=过度诊疗 8 条 (R218 R221 R222 R310 R311 R312 R225 + 2 已 ready), 加上杂项 (虚假诊断/虚构病情/虚假住院/虚构诊疗/诱导住院 等 14 条).

**M6 与 M2 形态接近** (干预措施 + 适应症), 但加 exclusion_dx 字段处理"非全麻不能收 BIS"等硬证据场景. 设计 doc `docs/templates/模板6_过度诊疗.md` 已写就 (~80 行精简版).

## What Changes

- **M6.yaml 模板装填** (空 → 完整):
  - master_prompt 含 exclusion_dx 分支 (M2 没有的硬证据 VIOLATION)
  - 13 fields (类似 M2 + exclusion_dx_list + notes_evidence_section)
- **init 14 条 M6 候选骨架**:
  - 7 条主审: R218 R221 R222 R225 R310 R311 R312 (priority P0)
  - 7 条特殊: R280 R281 R282 R283 R284 R285 R286 (priority P2, 留 P2 drafting + notes 说明)
- **7 条主审 prompt-fit + ready**
- 验收: 跑 `audit-patient J66252 --share-tool-cache --concurrency 5` (P0 子集 45 条)

不在本期范围:
- ❌ 不动 M4 (超标准收费, 部分需诊疗目录工具, 留 add-catalog-loader 后)
- ❌ 不实装杂项 7 条 (R280-R286, 工具不可见)
- ❌ 不改 base.txt / 代码

## Capabilities

### Modified Capabilities

- `rule-templating`: M6 模板装满, 与 M1-M5 同等可用
- `rule-registry`: 14 条 M6 候选 yaml 从无 → 7 ready + 7 drafting

## Impact

- M6.yaml 空骨架 → 完整 (master_prompt + 13 fields)
- 14 条新 yaml 骨架 (R218 R221 R222 R225 R280-R286 R310 R311 R312)
- 7 条 vars json (R218 R221 R222 R225 R310 R311 R312)
- ready:65/82 (M1 15 + M2 18 + M3 17 + M5 8 + M6 7)
- 零代码改动 / 157 tests 全绿
