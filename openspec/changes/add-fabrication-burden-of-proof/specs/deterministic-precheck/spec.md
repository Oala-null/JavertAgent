# deterministic-precheck Spec Delta

## MODIFIED Requirements

### Requirement: 模板级确定性事实预检

系统 MUST 为声明了 `precheck` 结构化字段 (A 类项目集 `a_items` + B 类项目集 `b_items` + 可选 `mode`, 缺省 `coexist`) 的规则, 在进入 LLM 之前对患者退费净额后的费用做确定性预检: 按项目名子串判定 A 类与 B 类是否各有命中。预检 MUST 先剔除完全充退项 (净额 ≤ 0) 再判命中。预检产出三种 outcome 之一: `clean` / `facts` / `skip` (费用数据不可用), 各 outcome 的成立条件由 `mode` 决定。`mode: coexist` (M1 语义) 时: `clean` = A 或 B 命中为空, `facts` = A、B 都命中; 且 MUST NOT 依据诊断或费用类别做**短路 CLEAN** 判定 (coexist 只认 A∩B 费用并存缺失这一条)。未声明 `mode` 的既有规则行为 MUST 与本 change 之前逐字一致。

#### Scenario: A 类费用缺失 → 短路 CLEAN 不进 LLM

- **WHEN** 患者费用中不含任何匹配某 M1 规则 A 类项目名的净正收费行
- **THEN** 预检返回 `clean`, 该 (rule, patient) 直接落 CLEAN 并带 `precheck_tag`「无A项」, 且**不发起任何 LLM 调用**

#### Scenario: A∩B 并存 → 事实成立进 LLM 窄问题

- **WHEN** 患者费用中 A 类与 B 类项目名各至少一行净正收费命中
- **THEN** 预检返回 `facts`, 产出 A/B 命中行 (项目名 + 金额 + 日期) 的事实块, 该规则进 LLM 且注入的用户消息明示只需用 `search_notes` 核实反证、不要再调 `search_fees`

#### Scenario: 完全充退项不算并存

- **WHEN** 患者某 B 类项目所有收费行被等量退费抵消 (净额 ≤ 0), A 类正常命中
- **THEN** 该 B 类项目 MUST NOT 计入 B 命中; 若无其它 B 命中则预检返回 `clean` (短路 CLEAN)

#### Scenario: 费用数据不可用 → 跳过预检 fail-open

- **WHEN** 无法取得该患者费用 (loader 取数异常 / 缺关键列)
- **THEN** 预检返回 `skip`, 该规则走原有 LLM 自由探索路径 (绝不因数据缺失误判 CLEAN)

#### Scenario: 未声明 mode 的规则默认 coexist

- **WHEN** 既有 M1 规则的 `precheck` 字段无 `mode`
- **THEN** 预检按 coexist 语义执行, 与本 change 之前行为逐字一致

## ADDED Requirements

### Requirement: companion 模式预检 (术式↔配套缺失)

`mode: companion` 的规则 (A=术式项目集, B=必备配套药/耗材项目集), 预检 outcome MUST 为: A 无净正命中 → `clean` (规则不适用, 零 LLM); A 命中且 B 无净正命中 → `facts`, 注入「收取术式 X 但全费用单无任何配套 Y」事实块 (含 A 命中行明细 + B 检索词清单), LLM 只需核实操作文书反证; A、B 均命中 → `skip`, 走原 LLM 路径且 MUST NOT 注入任何偏置性事实块 (配套在场则虚构信号消失, 但不构成 CLEAN 证明)。companion 的 `facts` 判 VIOLATION 时 evidence MUST 并入 A 命中费用行机器锚点 (复用既有 facts→evidence 机制)。

#### Scenario: 收溶栓术无溶栓药 → facts 窄问题

- **WHEN** 患者费用含「经皮穿刺脑血管腔内溶栓术」净正收费, 且全费用单无任何溶栓药 (尿激酶/阿替普酶/rt-PA/替奈普酶/瑞替普酶) 净正命中
- **THEN** 预检返回 `facts`, 事实块陈述术式收费行与配套缺失, LLM 仅需 search_notes 核实手术记录

#### Scenario: 未收术式 → 零 LLM 短路

- **WHEN** 患者费用无任何 A 类术式命中
- **THEN** 预检返回 `clean`, 不发起 LLM 调用

#### Scenario: 配套在场 → 不注偏置走原路径

- **WHEN** 患者既收了溶栓术也收了阿替普酶
- **THEN** 预检返回 `skip`, 该规则按原 prompt_addon 自由探索, 无事实块注入
