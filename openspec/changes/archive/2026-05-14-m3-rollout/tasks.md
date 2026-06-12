## 1. Phase 1 — M3 模板装填 + 17 条 G 类骨架 init

- [x] 1.1 init 17 条骨架: `init_pilot_rules(pilot_ids=['R233'..'R249'])` 从 0325 xls 生成 yaml 骨架
- [x] 1.2 改 priority: 17 条全部从默认 P3 → P1
- [x] 1.3 编辑 `configs/templates/M3.yaml`: 装 master_prompt (jinja2, 含 dental_dept_check / self_pay_bonus / total_amount_threshold / special_notes / pilot_caveat 条件块)
- [x] 1.4 装 keywords_template / tools_template / signal_template (与 M1/M2 同形态)
- [x] 1.5 装 fields 清单 14-16 个 (按 design.md D1 字段表)
- [x] 1.6 跑 `uv run javert template validate M3`, 确认 status=ready
- [x] 1.7 跑 `uv run javert template show M3`, 检查渲染输出无 jinja2 语法错

## 2. Phase 2 — G-a 子类 12 条 (R233-R244)

- [x] 2.1 写 `docs/m3_R233_vars.json` (局部组织瓣修复)
- [x] 2.2 跑 prompt-fit R233 写盘
- [x] 2.3 写 `docs/m3_R234_vars.json` (血管瘤淋巴管瘤切除)
- [x] 2.4 跑 prompt-fit R234 写盘
- [x] 2.5 写 `docs/m3_R235_vars.json` (游离骨瓣移植)
- [x] 2.6 跑 prompt-fit R235 写盘
- [x] 2.7 写 `docs/m3_R236_vars.json` (颌骨重建)
- [x] 2.8 跑 prompt-fit R236 写盘
- [x] 2.9 写 `docs/m3_R237_vars.json` (牙周组织瓣移植)
- [x] 2.10 跑 prompt-fit R237 写盘
- [x] 2.11 写 `docs/m3_R238_vars.json` (显微根管治疗)
- [x] 2.12 跑 prompt-fit R238 写盘
- [x] 2.13 写 `docs/m3_R239_vars.json` (根管再治疗)
- [x] 2.14 跑 prompt-fit R239 写盘
- [x] 2.15 写 `docs/m3_R240_vars.json` (根管充填)
- [x] 2.16 跑 prompt-fit R240 写盘
- [x] 2.17 写 `docs/m3_R241_vars.json` (牙髓失活)
- [x] 2.18 跑 prompt-fit R241 写盘
- [x] 2.19 写 `docs/m3_R242_vars.json` (牙髓摘除)
- [x] 2.20 跑 prompt-fit R242 写盘
- [x] 2.21 写 `docs/m3_R243_vars.json` (脱敏治疗)
- [x] 2.22 跑 prompt-fit R243 写盘
- [x] 2.23 写 `docs/m3_R244_vars.json` (牙体粘接修复)
- [x] 2.24 跑 prompt-fit R244 写盘
- [x] 2.25 跑 `uv run javert dry-run R236 --patient J66252`, 看 trace (高额信号抽样)

## 3. Phase 3 — G-b 子类 5 条 (R245-R249)

- [x] 3.1 写 `docs/m3_R245_vars.json` (颌面软组织清创 reference, 设计 doc §3 完整版)
- [x] 3.2 跑 prompt-fit R245 写盘, 看渲染输出语义对照设计 doc §3
- [x] 3.3 写 `docs/m3_R246_vars.json` (根尖搔刮)
- [x] 3.4 跑 prompt-fit R246 写盘
- [x] 3.5 写 `docs/m3_R247_vars.json` (分根术)
- [x] 3.6 跑 prompt-fit R247 写盘
- [x] 3.7 写 `docs/m3_R248_vars.json` (牙龈切除)
- [x] 3.8 跑 prompt-fit R248 写盘
- [x] 3.9 写 `docs/m3_R249_vars.json` (龈瓣整形)
- [x] 3.10 跑 prompt-fit R249 写盘
- [x] 3.11 跑 `uv run javert dry-run R245 --patient J66252`, 看 trace (reference 验证)

## 4. Phase 4 — 验收

- [x] 4.1 批量 mark 17 条 ready: `for r in R233 ... R249; do uv run javert mark $r --status ready; done`
- [x] 4.2 `uv run javert list` 验证: 末行 `ready:50` (15 M1 + 18 M2 + 17 M3)
- [x] 4.3 全 17 条 `derived_from_template=M3` + `priority=P1` 校验通过
- [x] 4.4 跑 `uv run javert audit-patient J66252 --priority P1 --share-tool-cache --concurrency 5` — 记录总耗时 / verdict 分布 / cache hit
- [x] 4.5 docs/sample_audit_patient.md 新增 "组 H: m3-rollout 后" 节, 含逐条耗时表 + 与组 G (33 条 P0+P1) 对比 + 结论
- [x] 4.6 全部单测 `uv run pytest tests/ -v` 仍全绿 (无回归)

## 5. Phase 5 — 文档收尾

- [x] 5.1 更新 `CLAUDE.md` 当前阶段标记: 标 ✅ "m3-rollout (17 条 M3 ready, ready:50/58)"
- [x] 5.2 更新 `README.md` 路线图: m3-rollout ✅ done; m4-rollout 下一候选
- [x] 5.3 `uv run openspec validate m3-rollout --strict` 通过

## 6. 验收

- [x] 6.1 spec scenario "M3 template validation passes" 通过
- [x] 6.2 spec scenario "M3 template can render with vars" 通过
- [x] 6.3 spec scenario "M3 set is loadable and all ready" 通过 (`javert list` 显示 ready:50)
- [x] 6.4 spec scenario "derived_from_template invariant for M3 set" 通过 (全 17 条 derived_from_template=M3, priority=P1)
- [x] 6.5 spec scenario "M3 set passes audit-patient sanity on J66252 (P1)" 通过 — 17 条全部产出 verdict, 全 CLEAN (设计预期)
