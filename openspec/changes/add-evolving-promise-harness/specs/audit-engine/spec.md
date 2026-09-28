## ADDED Requirements

### Requirement: Terminal Promise 必须在任何可持久化裁决路径前求值
审计引擎 MUST 在完成 Promise 所需的确定性事实归一后、调用 LLM 或进入任何可提前持久化的普通裁决路径前执行 active `decision_pre_llm` Promise；命中 terminal Promise 时 MUST 直接生成带 LOCKED trace 的结果且零 LLM，未命中时 MUST 保持现有 precheck/LLM 链路行为。

#### Scenario: Terminal Promise 命中
- **WHEN** 当前规则与患者事实命中 active terminal Promise
- **THEN** Runner 直接返回该 Promise 声明的 verdict
- **AND** `tool_calls` 为空且不调用 LLM
- **AND** 结果携带 Promise ID、版本、kind、finality、reason code 和最小事实摘要

#### Scenario: Promise 不适用
- **WHEN** 规则不在 scope 或任一必要确定性事实缺失
- **THEN** evaluator 返回 NOT_APPLICABLE
- **AND** 原有 precheck、结构化求值、LLM 与错误处理链路按既有顺序执行

### Requirement: 首条退费 Promise 必须严格限定于同项目次数型违规
`PR-D001 refund-net-single-clean` MUST 仅适用于显式 scope 内、违规成立条件为“同一目标收费项目净数量超过 1”的规则；只有目标组唯一、原始组确有负数量退费、数量均可解析且目标组 `net_qty <= 1` 时，系统 MUST 锁定 CLEAN。

#### Scenario: 退费后同项目净数量为 1
- **WHEN** 规则在显式 scope 内，唯一目标项目原收费数量为 2、退费数量为 -1，且全部数量可解析
- **THEN** 系统生成 LOCKED CLEAN
- **AND** reason code 为稳定的退费净数量边界码
- **AND** facts 记录净数量和退费计数，不记录患者标识或自由文本

#### Scenario: 没有实际退费
- **WHEN** 唯一目标项目净数量为 1，但原始组没有任何负数量收费行
- **THEN** 该 Promise 返回 NOT_APPLICABLE

#### Scenario: 净数量仍超过 1
- **WHEN** 目标组确有退费但退费后 `net_qty > 1`
- **THEN** 该 Promise 返回 NOT_APPLICABLE
- **AND** 不阻止现有链路判定违规或疑似

#### Scenario: 非计数型或多目标语义
- **WHEN** 规则属于一次即违规、套餐多项目、串换、虚构、限定支付、M1 主附项目并存，或无法确定唯一目标组
- **THEN** 该 Promise MUST NOT 锁定 CLEAN

### Requirement: Promise trace 必须最小化、可空且可双写
SQLite 与 SQL Server 结果模型 MUST 以可空 `promise_trace_json` 保存 Promise ID、版本、kind、finality、reason code 和允许的最小事实；迁移 MUST 幂等，旧行为空时 MUST 可读取、可同步且不回填。

#### Scenario: 写入新锁定结果
- **WHEN** 新审计由 Promise 锁定并双写结果
- **THEN** SQLite 与 SQL Server 保存语义等价的 trace
- **AND** 旧 verdict、reasoning、evidence 和 eligibility 字段契约保持兼容

#### Scenario: 读取迁移前旧行
- **WHEN** 旧行没有 `promise_trace_json`
- **THEN** 反序列化结果的 Promise trace 为 null
- **AND** 同步与工作台请求成功
- **AND** 系统不回填该字段

### Requirement: Locked 结果不得被历史漂移防护改写
持久化前 drift guard 遇到 `finality=LOCKED` 的结果时 MUST 保持其 verdict；若历史结果与锁定保证冲突，只能在 trace 中追加 `historical_conflict=true` 并记录匿名指标。

#### Scenario: 历史违规后出现锁定 CLEAN
- **WHEN** 同一规则/患者存在历史 VIOLATION，而当前确定性 Promise 生成 LOCKED CLEAN
- **THEN** 当前结果仍以 CLEAN 持久化
- **AND** trace 标记 historical conflict
- **AND** 不把结果降为 INCONCLUSIVE

#### Scenario: 普通 CLEAN 遇到历史违规
- **WHEN** 当前 CLEAN 没有 LOCKED Promise trace 且存在历史 VIOLATION
- **THEN** 现有 drift guard 行为保持不变

### Requirement: 运行时 Promise 冲突必须 fail closed
若多个 terminal Promise 对同一次审计给出不一致保证，系统 MUST NOT 按加载顺序选择结果；系统 MUST 返回 INCONCLUSIVE 并携带内部 `PROMISE_CONFLICT` 诊断，同时不在客户字段或日志中暴露患者事实。

#### Scenario: 两个 terminal Promise 冲突
- **WHEN** 同一规则与确定性事实同时命中 CLEAN 和 VIOLATION 保证
- **THEN** Runner 返回 INCONCLUSIVE
- **AND** 不调用 LLM 来随机仲裁
- **AND** 仅以 Promise ID/版本和安全错误码记录冲突
