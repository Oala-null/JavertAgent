## Why

`add-rule-template-fitter` 把 M1 模板装好并通过 R191 byte-equal round-trip 验证, 但**真正受益的规则数还是 2 条** (R191 原本已写, R045 是 verification 副产物)。剩 13 条 `violation_type=重复收费` 的 yaml 仍是空 `prompt_addon` 骨架, 在 `add-patient-centric-audit` 组 A baseline (J66252 冷启动 41.5 min) 里依旧是慢规则贡献者。

M1 既然能 byte-equal 复刻 R191, 把它套到剩余 13 条只需"逐条写 vars.json + `prompt-fit` 跑一次"。这是一笔**双收益**: prompt 装上后 LLM 反复探索的耗时下来, 同时把 28 条 CLEAN 里潜在的 false-negative 消掉。

## What Changes

- 给 13 条空骨架 M1-类 yaml 装 prompt_addon: **R047 R069 R077 R112 R116 R118 R119 R185 R208 R226 R228 R260 R300**
- 每条创建 `docs/m1_Rxxx_vars.json` (operator 手填 personalization, 16-19 字段)
- 跑 `javert prompt-fit Rxxx --template M1 --vars docs/m1_Rxxx_vars.json` 写回 yaml
- 跑 `javert dry-run Rxxx --patient J66252` (或同病种患者) sanity-check trace
- 已 ready 的 R191 不动; R045 与 13 条新装一起统一 `javert mark ... --status ready`
- 验收: 重跑 `javert audit-patient J66252 --share-tool-cache`, 与组 A baseline (41.5 min, avg 83s/rule) 比, 记录新平均耗时与 verdict 分布到 `docs/sample_audit_patient.md`

不在本期范围:
- ❌ 不动 M1.yaml 模板 (除非反推某条不下来要回头加 field, 那做完后续 `extend-m1-template` change)
- ❌ 不为 M1 设计文档列出的另外 13 条无 yaml 规则 (R018 R054 R105 R115 R117 R205 R227 R292 R295 R296 R303 R304 R305) 新建骨架 — 那是 `add-pending-rules-init` 的事
- ❌ 不动 M2-M6 (各自 rollout)
- ❌ 不改 base.txt / audit_runs schema / 任何代码

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `rule-registry`: 15 条 M1-类规则在本期完成: 13 条骨架写满 `prompt_addon` + `trigger_keywords` + `suggested_tools` + `expected_signal` + `derived_from_template: M1`, 配合 R045 推到 `status: ready`, 与已 ready 的 R191 形成 15 条全量 M1 ready 集

## Impact

- **数据变更 (主要)**:
  - 14 条 rule yaml 字段 (R045 + 13 骨架) 由 prompt-fit 写盘
  - 13 条新 `docs/m1_Rxxx_vars.json` (operator 手编, 类似 m1_r045_vars.json 体量)
  - 14 条 yaml `status: drafting → ready` 推进 (R191 已 ready, 不动)
- **零代码改动**: 全部走现有 `prompt-fit` + `mark` CLI
- **测试**: 既有 134 tests 保持全绿; 不新加单测 (本期是数据填充, 非逻辑变更)
- **耗时验收**: 跑一次 `audit-patient J66252 --share-tool-cache` 看新 baseline; 若 avg/rule 没明显下降, 触发 `m1-rollout-2` 或回退到 prompt-fitter 加字段
- **文档**: 更新 `CLAUDE.md` 阶段标记 + `docs/sample_audit_patient.md` 加 m1-rollout 后实测段
