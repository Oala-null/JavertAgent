## Why

m2-rollout (2026-05-14) 完成: M2.yaml 模板装填 + 18 条 B 类规则 ready, 33 条 P0+P1 ready 全集. 组 G 实测 J66252 concurrency 5 跑 30 条 P0 (15 M1 + 15 M2 P0), 6:59 min, V=0 C=28 I=2, M2 子集 avg 58.8s 比 M1 子集 69.5s **快 15.4%**. M2 套填策略验证. m1+m2 形成绿区 (M1+M2 共 33 条 ready, 73 条目标的 45%).

**M3 模板 (口腔串换) 与 17 条 G 类规则当前全 MISSING**:
- `configs/templates/M3.yaml` 是空骨架 (只 template_id/name/description)
- 17 条 G 类规则 (R233-R249) 在 0325 xls 中存在, 但 `configs/rules/` 没有 yaml 骨架
- 设计 doc `docs/templates/模板3_口腔串换.md` 已完整 (339 行 + §4 17 条全部 personalization + §5 速查表 + §7 复用建议)

设计 doc §7 明确: **G 类对甲状腺 pilot 命中率近 0**, 应留 v0.3 切换口腔/综合医院数据集后使用. 但模板装好 + 17 条 ready 是 "装备完毕等数据集切换" 的前置条件, 现在做不阻塞 pilot.

m3-rollout 是 m1/m2-rollout 的姐妹 change, 但多一步: **init 17 条 yaml 骨架** (`init_pilot_rules` from 0325 xls), 然后装 M3.yaml + 装 prompt + ready.

## What Changes

- **M3.yaml 模板装填** (空 → 完整):
  - master_prompt 按 `docs/templates/模板3_口腔串换.md` §2 设计扩写, 含 jinja2 条件块 (dental_dept_check / 自费项目 加分 / 总额过高加分)
  - keywords_template / tools_template / signal_template 与 M1/M2 同形态
  - fields 14-16 个, 覆盖 hijacked_surgery_kw / trivial_dx_kw / supporting_dx_kw / dental_dept_check 等
- **init 17 条 G 类骨架**:
  - 用 `init_pilot_rules(pilot_ids=['R233'..'R249'])` 从 0325 xls 生成 yaml 骨架
  - priority 设 P1 (口腔强信号待数据切换, 不是 P0)
  - 17 条: R233 R234 R235 R236 R237 R238 R239 R240 R241 R242 R243 R244 (G-a 子类), R245 R246 R247 R248 R249 (G-b 子类)
- **17 条 prompt-fit**:
  - 每条创建 `docs/m3_Rxxx_vars.json`, 12 条 G-a 共享 trivial_dx_kw (9 项美容修复诊断), 5 条 G-b 共享 trivial_dx_kw (7 项普通拔牙/牙龈炎/牙髓炎)
  - 每条只换 `hijacked_surgery_kw` (被冒充的大手术名) + `supporting_dx_extra` (该手术专属指征)
  - `derived_from_template: M3`
- R245 是 reference rule (设计 doc §3), 与 R151 类似做语义对照验证 (不强 byte-equal)
- 抽 1-2 条 dry-run J66252 看 trace (预期全 CLEAN, 因甲状腺患者无口腔记录)
- 全 17 条统一 mark ready
- 验收: 跑 `javert audit-patient J66252 --priority P1 --share-tool-cache --concurrency 5`, 记录组 H 到 `docs/sample_audit_patient.md`. 预期 17 条 + R154 R155 R162 = 20 条全 CLEAN (甲状腺患者无口腔规则触发)

不在本期范围:
- ❌ 不动 M4-M6 (各自 rollout, 且设计 doc 尚未写)
- ❌ 不改 base.txt / audit_runs schema / 代码 / drug_indication 表
- ❌ 不为 R260 改归 M3 (设计 doc §7 明确: R260 已正确归 M1)
- ❌ 不在 pilot (甲状腺) 期间期望 V 命中 — 设计 doc §7 已声明命中率近 0

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `rule-templating`: M3 模板从空骨架装满 master_prompt + 14-16 fields + keywords/tools/signal_template, 与 M1/M2 同等可用性, 可被 `javert prompt-fit ... --template M3` 套用到 G 类口腔规则
- `rule-registry`: 17 条 G 类 yaml 从无 (MISSING) → ready: 全部由 init_pilot_rules 生成骨架 + prompt-fit 装填 + 标 priority=P1 + derived_from_template=M3 + status=ready. 全集 yaml 数量 41 → 58 (加 17 条)

## Impact

- **数据变更 (主要)**:
  - `configs/templates/M3.yaml`: 空骨架 → 完整模板 (master_prompt ≥1000 chars, fields 14-16 个)
  - 17 条新 yaml 骨架 (R233-R249) 由 `init_pilot_rules` 创建
  - 17 条新 `docs/m3_Rxxx_vars.json` (operator 手编, 类似 m2_R151_vars.json 体量, 但 G-a/G-b 子类共享字段省时)
  - 17 条 yaml `status: drafting → ready` 推进, priority 设 P1
- **零代码改动**: 全部走现有 `prompt-fit` + `mark` + `init_pilot_rules` API
- **测试**: 既有 157 tests 保持全绿; 不新加单测
- **耗时验收**: 跑一次 `audit-patient J66252 --priority P1 --share-tool-cache --concurrency 5`, 跑 P1 子集 (3 M2 P1 + 17 M3 P1 = 20 条), 预期总耗时 5-8 min, 全部 CLEAN (甲状腺患者无口腔规则触发)
- **文档**: 更新 `CLAUDE.md` 阶段标记 (m3-rollout ✅ + ready:50/58) + `docs/sample_audit_patient.md` 加组 H + `README.md` 路线图标 m3-rollout ✅
