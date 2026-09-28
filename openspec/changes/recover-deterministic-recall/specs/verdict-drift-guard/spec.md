# verdict-drift-guard Spec Delta

## ADDED Requirements

### Requirement: 重跑漂移老 V 新 C 拦截为 I

同一 (rule_id, patient_id) 重跑时, 若历史最新裁决为 VIOLATION 而本次为 CLEAN, persist 层 MUST 把本次落库裁决改为 INCONCLUSIVE 并打「漂移防护(历史曾判V)」标签, 使专家可见可裁。防护 MUST 只升到 INCONCLUSIVE, MUST NOT 直接恢复 VIOLATION。历史行 MUST NOT 被改写。

#### Scenario: R063 式 V→C 漂移被网住

- **WHEN** 某 (rule, patient) 历史最新为 VIOLATION, 重跑 LLM 判 CLEAN
- **THEN** 落库 INCONCLUSIVE + 漂移标签, 工作台当前裁决显示 I, 历史区可见原 V

#### Scenario: C→C / I→C 不触发

- **WHEN** 历史最新裁决为 CLEAN 或 INCONCLUSIVE, 重跑判 CLEAN
- **THEN** 照常落 CLEAN, 无标签

### Requirement: 专家已驳回的 V 不被复活

历史 VIOLATION 上存在专家 latest review 且审核结论为驳回时, drift guard MUST NOT 拦截本次 CLEAN — 专家已裁定该 V 为误判, 新 C 是修正而非漂移。

#### Scenario: 驳回后的重跑不再弹回 I

- **WHEN** 老 V 已被专家审核为驳回, 重跑判 CLEAN
- **THEN** 落 CLEAN, 不打漂移标签

### Requirement: drift guard 可开关且存量只报告不自动翻

系统 MUST 提供 `JAVERT_DRIFT_GUARD` 开关 (默认 on), 置 off 时落库行为与本 change 之前逐字一致。对存量已发生的 V→C 翻转, 系统 MUST 提供只读报告脚本列出清单供专家人工裁定, MUST NOT 自动改写存量行。

#### Scenario: 关闭开关回退

- **WHEN** `JAVERT_DRIFT_GUARD=off` 时发生老 V 新 C
- **THEN** 照常落 CLEAN (原行为), 无标签

#### Scenario: 存量翻转出清单

- **WHEN** 运行存量漂移报告脚本
- **THEN** 输出全库 (rule, patient) 维度历史含 V 而当前为 C 的清单 (含两次 run_id 与批次, 覆盖 2026-07-08 晨 211440399 R063/R155 翻转), 不修改任何行
