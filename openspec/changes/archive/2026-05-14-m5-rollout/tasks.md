## 1. Phase 1 — M5 模板装填 + init

- [x] 1.1 写 `docs/templates/模板5_虚构医药服务.md` (本期 phase 0 已完成)
- [x] 1.2 init 10 条 H 类骨架: `init_pilot_rules(pilot_ids=['R003','R004','R015','R037','R080','R103','R105','R134','R203','R224'])`
- [x] 1.3 改 priority: 8 条主审 → P0, R003/R004 → P2
- [x] 1.4 R003/R004 写 notes 说明 (跨患者 / 采购数据, 留后续 change)
- [x] 1.5 编辑 `configs/templates/M5.yaml`: 装 master_prompt + 12 fields
- [x] 1.6 跑 `uv run javert template validate M5`, 确认 status=ready

## 2. Phase 2 — 8 条主审 prompt-fit

- [x] 2.1 写 `docs/m5_R134_vars.json` (检验未做但收费, reference 样板)
- [x] 2.2 跑 prompt-fit R134 --dry-run, 看渲染输出, 写盘
- [x] 2.3 写 `docs/m5_R203_vars.json` (全身麻醉未做但收费, dept_check=麻醉科)
- [x] 2.4 跑 prompt-fit R203 写盘
- [x] 2.5 写 `docs/m5_R103_vars.json` (影像未做但收费)
- [x] 2.6 跑 prompt-fit R103 写盘
- [x] 2.7 写 `docs/m5_R105_vars.json` (CT 三维重建未做但收费)
- [x] 2.8 跑 prompt-fit R105 写盘
- [x] 2.9 写 `docs/m5_R015_vars.json` (有创血流动力学监测未做但收费)
- [x] 2.10 跑 prompt-fit R015 写盘
- [x] 2.11 写 `docs/m5_R037_vars.json` (胸腰椎骨折切开复位内固定术未做但收费)
- [x] 2.12 跑 prompt-fit R037 写盘
- [x] 2.13 写 `docs/m5_R080_vars.json` (康复评定打包未做但收费)
- [x] 2.14 跑 prompt-fit R080 写盘
- [x] 2.15 写 `docs/m5_R224_vars.json` (重症监护检验未做但收费)
- [x] 2.16 跑 prompt-fit R224 写盘
- [x] 2.17 跑 `uv run javert dry-run R134 --patient J66252`, 看 trace

## 3. Phase 3 — 验收

- [x] 3.1 批量 mark 8 条 ready
- [x] 3.2 `uv run javert list` 验证: ready:58
- [x] 3.3 跑 `uv run javert audit-patient J66252 --share-tool-cache --concurrency 5`
- [x] 3.4 docs/sample_audit_patient.md 新增 "组 I: m5-rollout 后" 节
- [x] 3.5 单测全绿
- [x] 3.6 更新 CLAUDE.md + README

## 4. 验收

- [x] 4.1 spec scenario "M5 template validation passes" 通过
- [x] 4.2 spec scenario "M5 template can render with vars" 通过
- [x] 4.3 spec scenario "M5 main set is loadable and all ready" 通过
- [x] 4.4 spec scenario "R003/R004 are draft skeletons with explanation" 通过
