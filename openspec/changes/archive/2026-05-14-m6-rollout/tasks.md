## 1. Phase 1 — M6 模板 + init

- [x] 1.1 写 `docs/templates/模板6_过度诊疗.md` (本期 phase 0 完成)
- [x] 1.2 编辑 `configs/templates/M6.yaml` 装满 (本期 phase 0 完成)
- [x] 1.3 跑 `javert template validate M6` 通过 (本期 phase 0 完成)
- [x] 1.4 init 14 条骨架 (7 主审 + 7 特殊)
- [x] 1.5 改 priority: 主审 P0, 特殊 P2
- [x] 1.6 写 7 条特殊 (R280-R286) 的 notes 说明限制

## 2. Phase 2 — 7 条主审 prompt-fit

- [x] 2.1 写 7 条 vars json (R218 R221 R222 R225 R310 R311 R312)
- [x] 2.2 批量 prompt-fit 写盘
- [x] 2.3 验证: 每条 prompt_addon >= 500c, trigger_keywords >= 5

## 3. Phase 3 — 验收

- [x] 3.1 mark 7 条主审 ready
- [x] 3.2 `javert list` 验证 ready:65
- [x] 3.3 跑 `javert audit-patient J66252 --share-tool-cache --concurrency 5` 组 J
- [x] 3.4 docs/sample_audit_patient.md 加组 J + 五模板 (M1-M3 + M5 + M6) 终极汇总
- [x] 3.5 单测全绿
- [x] 3.6 更新 CLAUDE.md + README (m6 ✅; m4 待 add-catalog-loader)

## 4. 验收

- [x] 4.1 spec "M6 template validation passes" 通过
- [x] 4.2 spec "M6 exclusion_dx hard-evidence branch" 通过
- [x] 4.3 spec "M6 main set is loadable and all ready" 通过
- [x] 4.4 spec "M6 jumbled set has explanatory notes" 通过
