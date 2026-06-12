## ADDED Requirements

### Requirement: 裁决后确定性闸层

系统 SHALL 在 `runner` 解析完 LLM verdict、落库**之前**调用一个确定性纯函数 gate (`verdict_gate.apply_gate`)。gate MAY 将 verdict 降级 (V→I 或 V→CLEAN) 并打标签, 每次降级 MUST 记录可解释 `reason`。gate MUST NOT 升级 (不把 C/I 改成 V)。gate 可经 `JAVERT_VERDICT_GATE=off` 直通 (回滚)。

#### Scenario: gate 直通非降级裁决

- **WHEN** 一条 verdict 不命中任何 gate 条件 (或 gate 关闭)
- **THEN** verdict、confidence、evidence 原样落库, `gate_tag` 为空

#### Scenario: 降级写明原因

- **WHEN** gate 把某 V 降级
- **THEN** 落库 verdict 为降级后值, `gate_tag` 非空, reasoning 追加「gate: <原因>」, 原 LLM 判断保留可追溯

### Requirement: 文件缺失硬闸

对**文件依赖类**规则 (`configs/verdict_gate.yaml` 列出: 影像/麻醉记录/监测记录/报告单/条码 等), 当某 V 的支撑**仅为文件缺失** (V 证据全部 `source ∈ {etl_warning}` 或 locator 含 `ETL_GAP`, 且无任何正向 note/fee/exam/lab 佐证) 时, gate SHALL 将 V 降为 INCONCLUSIVE 并打标签 `缺文书`。

#### Scenario: 影像报告缺失降级

- **WHEN** R103 (影像) 判 V, 证据只有「未检索到影像报告 / ETL_GAP」无任何正向佐证
- **THEN** gate 降为 INCONCLUSIVE, `gate_tag=缺文书`, reason「文件缺失, 待线下核查」

#### Scenario: 有正向佐证不降级

- **WHEN** 文件依赖类规则的 V 除缺失外**还有**正向证据 (如反复多次收费 + 病程零相关记录)
- **THEN** gate **不**触发文件缺失闸 (保留原 verdict, 交其他闸/原判)

#### Scenario: 非文件依赖类不误降

- **WHEN** 一条非文件依赖类规则的 V 恰好证据稀少
- **THEN** 文件缺失闸**不**作用 (该闸仅限配置内规则)

### Requirement: 单次硬闸

对 M2 派生 (`derived_from_template == M2`) 的过度检查规则, 当该检查的**净不同收费次数 ≤ 1** (用退费净额的 `distinct_billing_dates`, 按规则 `exam_kw_primary_list` 关键词确定性重算, 不信 LLM 自报) 且规则**不在** `single_instance_violation` 例外集时, gate SHALL 将 V 降为 CLEAN。

#### Scenario: 单次检查放过

- **WHEN** R153 (AFP) 判 V, 而 AFP 在该患者净收费次数 = 1
- **THEN** gate 降为 CLEAN, `gate_tag=单次放过`

#### Scenario: 例外集穿透仍 V

- **WHEN** R156 (女性查 PSA, 在 `single_instance_violation` 例外集) 判 V, 即使净次数 = 1
- **THEN** gate **不**降级 (单次本身即违规), 保留 V

#### Scenario: 全退检查视为零次

- **WHEN** 某检查 12 行但净额 0 (完全充退)
- **THEN** 净次数视为 0 (≤1), gate 降为 CLEAN

#### Scenario: 净额不可用时 fail-open

- **WHEN** 退费净额 (`net_fees`) 因故不可用
- **THEN** 单次闸**不**降级 (保持原 verdict), MUST NOT 因数据缺失误降

### Requirement: 置信度底线硬闸

gate SHALL 对 `0.70 ≤ confidence < 0.85` 的 VIOLATION 强制改判 INCONCLUSIVE (把 base.txt 的软规则代码化)。

#### Scenario: 低置信 V 改判

- **WHEN** 一条 V 的 confidence = 0.80
- **THEN** gate 改判 INCONCLUSIVE, `gate_tag=低置信降级`

### Requirement: gate 标签落库

`gate_tag` SHALL 持久化到审计记录 (`audit_runs` sqlite + `Javert_audit_runs` mssql), 取值 ∈ `{缺文书, 单次放过, 低置信降级, ""}`, 供工作台 facet 过滤与统计。schema 变更 MUST 幂等。

#### Scenario: 标签随裁决双写

- **WHEN** 一条被 gate 降级的裁决落库并同步到 mssql
- **THEN** sqlite 与 mssql 的该行 `gate_tag` 一致非空
