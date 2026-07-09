# Tasks: recover-deterministic-recall

> 前置: `add-fn-regression-library` 已落 (FN-003/004/005 为本 change 验收判据)

## 1. 证据层保真 (最小风险, 先落)

- [x] 1.1 退费净额逻辑提为共享 helper (patient_overview / precheck / search_fees 三处统一口径), `search_fees` 输出净额行 + 「含 N 次退费已抵消」注记, 完全充退项不输出 → 单测: 2收1退净1次 / 全充退不出现
- [x] 1.2 费用数量小数保真: 排查 csv/hub 两链格式化点修正 → 单测 + 211440399 肘关节截骨术 0.75 行人工核对
- [x] 1.3 R063 prompt_addon 50%→75% (附现行条款依据), 重建 router index → dry-run 211440399 R063 确认 expected 按 75% 算

## 2. 单次闸场景化

- [x] 2.0 核对 panel 初始集: R155 已实证套餐形态 (211419211 11项/日); R151/R132 逐条核 yaml 违规形态 + 抽存量 gate 行看费用分布, **确为"多项目单日打包"形态才入集**, 不实证不入 (误入集的代价是该规则闸降级目标变 I, 放大 I 量)
- [x] 2.1 `configs/verdict_gate.yaml` 加 `panel_rules` 节 (2.0 核定集, min_distinct_items 初值 3 + panel_downgrade_to: INCONCLUSIVE); `verdict_gate.py` ②闸: panel 规则按同日不同项目名数计数 (费用不可得 fail-open 原口径), 触发降 I → 单测: 11项单日 V 保留 / 2项触发降 I / 非 panel 逐字不变 / fail-open
- [ ] 2.2 FN 回归: FN-005 不退化 (gate 改动不误伤 R225)  ⏸ 需 62 live LLM 复跑; 已由构造保证 (panel 分支仅作用 R155, R225 gate 路径逐字不变, 见 test_non_panel_m2_rule_single_instance_unchanged)

## 3. 463 条存量重筛

- [x] 3.1 写 `scripts/rescreen_gated.py` (--dry-run / 实跑 / --revert 还原): 选「单次放过」行 → panel 规则从费用重算 → 原地 UPDATE C→I + 可逆标签「单次闸重筛回升(原C)」; 断言跳过有 review 行; sqlite 先 142 后
- [ ] 3.2 dry-run 出翻转量分布 → 定 min_distinct_items 终值 (>100 条收紧) → **与用户确认后**落库 → 验证: FN-003 (211419211×R155) 翻 I  ⏸ 需 142/存量数据 + 用户确认阈值 (脚本已就绪, 命令见 deployment §10.5 ①)
- [x] 3.3 工作台「被闸降级」facet (gate_tag 非空维度, 复用 v0.9 facet 引擎, 与 verdict filter 正交) → 手工验证专家视角可列降级行 + 原始 V 推理

## 4. drift guard

- [x] 4.1 `result_persister` 写前查同 (rule,patient) sqlite 最新历史: 老V新C → 落 I + 「漂移防护(历史曾判V)」; 驳回豁免 (老 V 有专家驳回 review 则放行 C); `JAVERT_DRIFT_GUARD` 默认 on → 单测: 拦 / C→C 不拦 / 驳回放行 / off 回退 四路径
- [x] 4.2 写 `scripts/drift_report.py` 只读存量清单 (含 2026-07-08 晨 211440399 R063/R155) → 出报告交专家, 不自动改

## 5. 端到端 + 部署

- [ ] 5.1 `uv run pytest tests/ -v` 全绿 (✓ 653 passed + 1 skip; 唯 2 失败来自无关 WIP `hub_source.py` 未提交改动, 与本 change 无关); FN 回归 --against-baseline: FN-003 升档 (重筛后), FN-005 保持 full  ⏸ FN 部分需 62 live LLM
- [ ] 5.2 62 部署 (tar src+configs+scripts), 3.2 重筛对 142 执行, javert-web 重启 (facet); FN 回归 62 复跑  ⏸ 需 62 access (runbook 已写, deployment §10.5)
- [x] 5.3 `Javert/CLAUDE.md` 变更日志 + `docs/deployment_192_62.md` 升级步骤 (含重筛与还原命令)
