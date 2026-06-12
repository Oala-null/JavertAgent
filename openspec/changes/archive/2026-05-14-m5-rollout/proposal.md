## Why

m3-rollout (2026-05-14) 完成: M3.yaml 模板 + 17 条 G 类规则 ready. **ready:50/58 (15 M1 + 18 M2 + 17 M3)**, 三大模板可用. 组 H 实测 J66252 P1 子集 4:40 min, V=0 C=20 I=0, M3 全 CLEAN 符合设计预期.

继续向 m6 目标推进. M5 (虚构医药服务) 是下一目标:

- `configs/templates/M5.yaml` 是空骨架
- 10 条 H 类规则在 0325 xls 中 (R003 R004 R015 R037 R080 R103 R105 R134 R203 R224), **全部 MISSING** (无 yaml 骨架)
- 设计 doc `docs/templates/模板5_虚构医药服务.md` 已在本期 phase 0 写就 (140 行精简版)

核心模式: **fee 命中 + 文书无对应执行记录** = 虚构骗保. 8 条主审 (R015 R037 R080 R103 R105 R134 R203 R224) 用当前 4 工具可审, R003 (套模板病历跨患者比对) + R004 (药品申请 vs 采购数据) **超出工具能力**, 留 P2 drafting + abandoned 备注.

m5-rollout 是 m3-rollout 的姐妹 change: init 缺失骨架 + 装 M5.yaml + prompt-fit + ready.

## What Changes

- **M5.yaml 模板装填** (空 → 完整):
  - master_prompt 按 `docs/templates/模板5_虚构医药服务.md` §2 设计扩写
  - keywords_template / tools_template / signal_template 与 M1-M3 同形态
  - fields ~12 个: service_desc / service_kw / catalog_basis / execution_evidence_kw / notes_section_hints / supporting_dx_kw / dept_check / special_notes / pilot_caveat / aux_*
- **init 10 条 H 类骨架**:
  - 用 `init_pilot_rules(pilot_ids=['R003','R004','R015','R037','R080','R103','R105','R134','R203','R224'])`
  - priority: 8 条主审 P0 (高命中候选); R003/R004 P2 (跨患者/采购数据不可见)
- **8 条 prompt-fit**:
  - R015 R037 R080 R103 R105 R134 R203 R224 → M5 模板装填, derived_from_template=M5
  - R003 R004 → 不装 prompt, 保持 drafting + notes 说明 (留后续 change)
- 抽 1-2 条 dry-run J66252 (R134 / R203 强信号)
- 8 条 mark ready
- 验收: 跑 `javert audit-patient J66252 --priority P0 --share-tool-cache --concurrency 5`, 记录组 I

不在本期范围:
- ❌ 不动 M4/M6 (各自 rollout, 设计 doc 待写)
- ❌ 不实装 R003/R004 (跨患者 / 采购侧, 留 P2)
- ❌ 不改 base.txt / 代码 / drug_indication 表
- ❌ 不补 10 条 B 类 MISSING (留 `add-pending-rules-b-class`)

## Capabilities

### Modified Capabilities

- `rule-templating`: M5 模板从空骨架装满 master_prompt + 12 fields + keywords/tools/signal_template, 与 M1-M3 同等可用性
- `rule-registry`: 10 条 H 类 yaml 从无 → ready (8 条) + drafting (2 条 R003/R004 特殊). 全集 yaml 数量 58 → 68

## Impact

- **数据变更**:
  - `configs/templates/M5.yaml`: 空骨架 → 完整模板
  - 10 条新 yaml 骨架 (R003 R004 R015 R037 R080 R103 R105 R134 R203 R224) 由 `init_pilot_rules` 创建
  - 8 条新 `docs/m5_Rxxx_vars.json`
  - 8 条 yaml `status: drafting → ready` (R015 R037 R080 R103 R105 R134 R203 R224)
  - 2 条保 drafting (R003 R004) + notes 说明
- **零代码改动**: 全部走现有 API
- **测试**: 既有 157 tests 保持全绿
- **耗时验收**: 跑 P0 子集 (15 M1 + 15 M2 P0 + 8 M5 P0 = 38 条), 预期 ~9 min concurrency 5. 期望 M5 子集出现部分 V (J66252 fee 项与文书不完全匹配的情况, M5 触发能力)
- **文档**: 更新 CLAUDE.md (m5 ✅ + ready:58/68) + sample_audit_patient.md 加组 I + README 路线图标 m5 ✅
