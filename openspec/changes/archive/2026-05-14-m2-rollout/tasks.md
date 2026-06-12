## 1. Phase 1 — M2 模板装填 + R151 reference

- [x] 1.1 编辑 `configs/templates/M2.yaml`: 装 master_prompt (jinja2, 含 drug_check / single_count / special_notes / pilot_caveat 条件块)
- [x] 1.2 装 keywords_template / tools_template / signal_template (与 M1 同形态)
- [x] 1.3 装 fields 清单 14-16 个 (按 design.md D1 字段表)
- [x] 1.4 跑 `uv run javert template validate M2`, 确认 status=ready
- [x] 1.5 跑 `uv run javert template show M2`, 检查渲染输出无 jinja2 语法错
- [x] 1.6 写 `docs/m2_R151_vars.json` (按设计 doc §3 内容填 16 字段)
- [x] 1.7 跑 `uv run javert prompt-fit R151 --template M2 --vars docs/m2_R151_vars.json --dry-run --output -`, 看渲染输出
- [x] 1.8 语义对照: 渲染出的 prompt 是否覆盖设计 doc §3 的 6 步审计 + 8 项诊断指征 + min_count=2 + 单次 INCONCLUSIVE
- [x] 1.9 跑 `uv run javert prompt-fit R151 --template M2 --vars docs/m2_R151_vars.json` 写盘, 验证 R151.yaml 含 prompt_addon + derived_from_template=M2

## 2. Phase 2 — 临床检验 11 条 (R151 已 W1 完成, 余 10 条)

- [x] 2.1 写 `docs/m2_R141_vars.json` (肌红蛋白, 心肌损伤/横纹肌溶解指征)
- [x] 2.2 跑 prompt-fit R141 写盘
- [x] 2.3 写 `docs/m2_R143_vars.json` (血浆乳酸, 休克/脓毒症指征)
- [x] 2.4 跑 prompt-fit R143 写盘
- [x] 2.5 写 `docs/m2_R146_vars.json` (BNP/NT-proBNP, 心衰/呼吸困难指征)
- [x] 2.6 跑 prompt-fit R146 写盘
- [x] 2.7 写 `docs/m2_R153_vars.json` (AFP, 肝癌/睾丸癌/孕妇指征)
- [x] 2.8 跑 prompt-fit R153 写盘
- [x] 2.9 写 `docs/m2_R154_vars.json` (CEA/CA199, 肿瘤指征; pilot_caveat 标髓样癌有指征)
- [x] 2.10 跑 prompt-fit R154 写盘
- [x] 2.11 写 `docs/m2_R155_vars.json` (IL-6/TNF, 风湿/感染指征)
- [x] 2.12 跑 prompt-fit R155 写盘
- [x] 2.13 写 `docs/m2_R156_vars.json` (PSA, 前列腺指征; special_notes 标女性绝对违规)
- [x] 2.14 跑 prompt-fit R156 写盘
- [x] 2.15 写 `docs/m2_R160_vars.json` (PCT, 脓毒症/严重感染指征)
- [x] 2.16 跑 prompt-fit R160 写盘
- [x] 2.17 写 `docs/m2_R161_vars.json` (cTnI, 心梗/ACS 指征)
- [x] 2.18 跑 prompt-fit R161 写盘
- [x] 2.19 写 `docs/m2_R162_vars.json` (肺炎支原体抗体等, 不典型肺炎/结核指征)
- [x] 2.20 跑 prompt-fit R162 写盘
- [x] 2.21 跑 `uv run javert dry-run R141 --patient J66252`, 看 trace (强信号 M2 抽样)

## 3. Phase 3 — 医学影像 4 + 麻醉 2 + 精神 1

- [x] 3.1 写 `docs/m2_R109_vars.json` (CT 心电/呼吸门控加收, 心律失常指征)
- [x] 3.2 跑 prompt-fit R109 写盘
- [x] 3.3 写 `docs/m2_R129_vars.json` (妇科/泌尿系彩超)
- [x] 3.4 跑 prompt-fit R129 写盘
- [x] 3.5 写 `docs/m2_R130_vars.json` (血管彩超)
- [x] 3.6 跑 prompt-fit R130 写盘
- [x] 3.7 写 `docs/m2_R131_vars.json` (心脏彩超系列)
- [x] 3.8 跑 prompt-fit R131 写盘
- [x] 3.9 写 `docs/m2_R219_vars.json` (麻醉中肌松监测, drug_check=true + 肌松药 7 种)
- [x] 3.10 跑 prompt-fit R219 写盘
- [x] 3.11 写 `docs/m2_R220_vars.json` (控制性降压, 神经外科/血管外科指征)
- [x] 3.12 跑 prompt-fit R220 写盘
- [x] 3.13 写 `docs/m2_R313_vars.json` (抗精神病药物治疗监测, drug_check=true + 抗精神病药 12 种)
- [x] 3.14 跑 prompt-fit R313 写盘
- [x] 3.15 跑 `uv run javert dry-run R219 --patient J66252`, 看 trace (drug_check 变体抽样)

## 4. Phase 4 — 验收

- [x] 4.1 批量 mark 18 条 ready: `for r in R109 R129 R130 R131 R141 R143 R146 R151 R153 R154 R155 R156 R160 R161 R162 R219 R220 R313; do uv run javert mark $r --status ready; done`
- [x] 4.2 `uv run javert list` 验证: 末行 `ready:33` (15 M1 + 18 M2)
- [x] 4.3 全 18 条 `derived_from_template=M2` 校验通过 (脚本断言或人工 spot check)
- [x] 4.4 跑 `uv run javert audit-patient J66252 --share-tool-cache --concurrency 5` — 记录总耗时 / verdict 分布 / cache hit
- [x] 4.5 docs/sample_audit_patient.md 新增 "组 G: m2-rollout 后" 节, 含逐条耗时表 + 与组 E (15 条 M1) 对比 + 结论
- [x] 4.6 全部单测 `uv run pytest tests/ -v` 仍全绿 (无回归)

## 5. Phase 5 — 文档收尾

- [x] 5.1 更新 `CLAUDE.md` 当前阶段标记: 标 ✅ "m2-rollout (18 条 M2 ready, ready:33/41)"
- [x] 5.2 更新 `README.md` 路线图: m2-rollout ✅ done; m3-rollout 下一候选
- [x] 5.3 `uv run openspec validate m2-rollout --strict` 通过

## 6. 验收

- [x] 6.1 spec scenario "M2 template validation passes" 通过
- [x] 6.2 spec scenario "M2 template can render with vars" 通过
- [x] 6.3 spec scenario "M2 set is loadable and all ready" 通过 (`javert list` 显示 ready:33)
- [x] 6.4 spec scenario "derived_from_template invariant for M2 set" 通过 (全 18 条 derived_from_template=M2, min prompt_addon=500)
- [x] 6.5 spec scenario "M2 set passes audit-patient sanity" 通过 — 18 条全部产出 verdict, 总耗时合理
