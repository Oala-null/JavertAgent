## Why

m1-rollout (2026-05-14) 完成后 15 条 M1 规则全 ready, 组 E 实测 J66252 暖启动 18.6 min, V=0 C=15 I=0, avg 74.5s/rule, 全部高置信 CLEAN (≥0.95). M1 模板套填策略验证可行。

**M2 模板 (过度检查) 目前是空骨架** (`configs/templates/M2.yaml` 仅含 template_id/name/description, master_prompt/keywords/tools/signal_template 全空, fields=[]). 而 28 条 B 类规则 (`violation_type=过度检查`) 中, 18 条已有 yaml 骨架但 `prompt_addon` 全空, 在 audit-patient 时仍然是慢规则候选 — `add-patient-centric-audit` 组 A baseline 中骨架规则 LLM 反复探索, 拖累整体耗时。

本期 = m1-rollout 的姐妹 change, 一并完成两件事:
1. 把 M2.yaml 模板从空填满 (master_prompt + fields)
2. 把 18 条已有骨架装 prompt_addon, 推到 ready

R151 (非肝炎多次乙肝丙肝抗体) 是 `docs/templates/模板2_过度检查.md` §3 设计的 reference 规则, 由本期写 vars.json + prompt-fit 后用作 M2 模板的 round-trip 验证 (类似 R191 之于 M1, 但 R151 yaml 是空骨架, 不做 byte-equal — 改用"渲染 prompt ≥ 设计 doc §3 文案"的语义对照).

## What Changes

- **M2.yaml 模板装填** (空 → 完整):
  - master_prompt 按 `docs/templates/模板2_过度检查.md` §2 设计扩写, 含 jinja2 条件块 (drug_check / single_count INCONCLUSIVE / special_notes / pilot_caveat)
  - keywords_template / tools_template / signal_template 与 M1 同形态 (list-iter)
  - fields 14-16 个, 覆盖 exam_concept_desc / catalog_basis / exam_kw_primary_list / indication_dx_list / min_count / drug_check + 可选条件字段
- **18 条 B 类骨架装 prompt_addon**:
  - R141 R143 R146 R151 R153 R154 R155 R156 R160 R161 R162 (临床检验 11 条)
  - R109 R129 R130 R131 (医学影像 4 条; R108/R132 MISSING 留给后续)
  - R219 R220 (麻醉 2 条; R218/R221/R222 MISSING)
  - R313 (精神 1 条; R311/R312 MISSING)
- 每条创建 `docs/m2_Rxxx_vars.json` (按设计 doc §4 填充表)
- 跑 `javert prompt-fit Rxxx --template M2 --vars docs/m2_Rxxx_vars.json` 写回 yaml + 加 `derived_from_template: M2`
- 抽 2-3 条代表性规则 (R151, R141, R219) dry-run J66252 看 trace
- 全 18 条统一 `javert mark Rxxx --status ready`
- 验收: 跑 `javert audit-patient J66252 --share-tool-cache --concurrency 5`, 与组 E (15 条 18.6 min) 对照, 写组 G 到 `docs/sample_audit_patient.md`

不在本期范围:
- ❌ 不新建 10 条 MISSING 骨架 (R108 R132 R218 R221 R222 R225 R278 R279 R311 R312) — 后续 `add-pending-rules-b-class` change
- ❌ 不动 M3-M6
- ❌ 不改 base.txt / audit_runs schema / 任何代码 / drug_indication 表
- ❌ 不为 R225/R311/R312 (对甲状腺 pilot 几乎零命中) 强行写 prompt — 这些规则 MISSING + 设计 doc 建议 abandoned, 由后续 change 处理

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `rule-templating`: M2 模板从空骨架装满 master_prompt + 14-16 fields + keywords/tools/signal_template, 与 M1 同等可用性, 可被 `javert prompt-fit ... --template M2` 套用到 B 类规则
- `rule-registry`: 18 条 B 类骨架完成: 全部装 `prompt_addon` + `trigger_keywords` + `suggested_tools` + `expected_signal` + `derived_from_template: M2`, status 推到 ready, 形成 18 条 M2 ready 集 (与 15 条 M1 ready 集并存 → ready:33 / 41)

## Impact

- **模板变更**:
  - `configs/templates/M2.yaml`: 空骨架 → 完整模板 (master_prompt ≥1000 chars, fields 14-16 个)
- **数据变更 (主要)**:
  - 18 条 rule yaml 字段由 prompt-fit 写盘
  - 18 条新 `docs/m2_Rxxx_vars.json` (operator 手编, 类似 m1_r191_vars.json 体量)
  - 18 条 yaml `status: drafting → ready` 推进
- **零代码改动**: 全部走现有 `prompt-fit` + `mark` CLI
- **测试**: 既有 tests 保持全绿; 不新加单测 (本期是数据填充 + 模板填充, 非逻辑变更)
- **耗时验收**: 跑一次 `audit-patient J66252 --share-tool-cache --concurrency 5`, 与组 E baseline 对照. 期望: 33 条 ready 集 (M1+M2) 全部高置信 CLEAN/部分 VIOLATION (B 类对肿瘤患者 R141/R143/R146 等检验项目可能触发 V), 总耗时控制在 8-12 min 内
- **文档**: 更新 `CLAUDE.md` 阶段标记 + `docs/sample_audit_patient.md` 加组 G + 路线图标 m2-rollout ✅
