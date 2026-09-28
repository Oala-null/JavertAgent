# deterministic-precheck Specification

## Purpose
TBD - created by archiving change pilot-deterministic-precheck. Update Purpose after archive.
## Requirements
### Requirement: 模板级确定性事实预检

系统 MUST 为声明了 `precheck` 结构化字段 (A 类项目集 `a_items` + B 类项目集 `b_items`) 的规则, 在进入 LLM 之前对患者退费净额后的费用做确定性预检: 按项目名子串判定 A 类与 B 类是否各有命中。预检 MUST 先剔除完全充退项 (净额 ≤ 0) 再判命中。预检产出三种 outcome 之一: `clean` (A 或 B 命中为空) / `facts` (A、B 都命中) / `skip` (费用数据不可用)。预检 MUST NOT 依据诊断或费用类别做**短路 CLEAN** 判定 (只认 A∩B 费用并存缺失这一条)。

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

### Requirement: 预检事实机器可复核

事实成立 (`facts`) 且 LLM 判 VIOLATION 时, 系统 MUST 把预检给定的 A/B 命中费用行确定性地并入结果 evidence (来源标记为费用来源、locator 为费用项目名), 使工作台命中项目解析器能 join 出国家码/院内码与原文锚点。新增 VIOLATION 的 evidence MUST 至少含一条带费用行定位的机器锚点, 不依赖 LLM 自由文本引用是否准确。

#### Scenario: 事实成立判 V → evidence 含系统给定费用行锚点

- **WHEN** 某 M1 规则预检 `facts` 成立且 LLM 裁决 VIOLATION
- **THEN** 结果 evidence 至少含一条 `source` 为费用来源、`locator` 为 A 类 (及/或 B 类) 命中费用项目名的证据, 且 `hit_resolver` 能据此解析出该 fee 行的编码与可跳转锚点

### Requirement: 预检可开关回滚

系统 MUST 提供 `precheck` 开关 (config 字段 + 环境变量 `JAVERT_PRECHECK`)。置 `off` 时所有规则 (含带 `precheck` 字段者) MUST 走原有 LLM 路径, 与本 change 之前行为一致。

#### Scenario: 关闭预检回退原路径

- **WHEN** 设 `JAVERT_PRECHECK=off` 后对带 precheck 字段的 M1 规则跑 audit
- **THEN** 不发生短路、不注入事实块, LLM 按原 `prompt_addon` 自由探索费用与文书

