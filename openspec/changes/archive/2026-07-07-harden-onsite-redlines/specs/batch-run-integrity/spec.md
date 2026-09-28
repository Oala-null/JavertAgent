# batch-run-integrity — delta spec

## ADDED Requirements

### Requirement: persist 失败对 SSE 消费方可感知

run-batch 端点 MUST 仅在裁决成功落库 (sqlite) 后发送 `result` 事件; persist 失败时 MUST 发送 `fail` 事件 (含 rule_id 与失败阶段标识), MUST NOT 发送 `result`. 既有事件的字段结构 MUST 保持向后兼容 (只新增, 不改名不删).

#### Scenario: 落库失败不再静默

- **WHEN** run-batch 中某条规则裁决完成但 sqlite 写入抛错
- **THEN** SSE 流发出该 rule_id 的 `fail` 事件, BFF 不会收到与库中不存在的 run 对应的 `result`

#### Scenario: 正常路径零变化

- **WHEN** 裁决与落库均成功
- **THEN** `result` 事件字段与本 change 之前逐字一致

### Requirement: 未知规则显式失败

run-batch 请求中包含不存在的 rule_id 时, 系统 MUST 对每个未知 rule_id 发送 `fail` 事件, MUST NOT 静默丢弃——消费方能区分"规则不存在"与"漏返回".

#### Scenario: 拼错的规则号有回执

- **WHEN** BFF 请求 rules=[R191, R999] 且 R999 不存在
- **THEN** R191 正常执行, R999 收到 `fail` 事件 (标识为未知规则)

### Requirement: 串行审计失败隔离

`audit-patient` 串行模式下单条规则的 LLM/执行失败 MUST 标记该条 failed 并继续执行剩余规则, MUST NOT 中断整个患者的审计 (与并发模式行为一致).

#### Scenario: 一条炸不拖全患者

- **WHEN** 串行跑 20 条规则, 第 3 条 LLM 调用抛错
- **THEN** 第 4-20 条照常执行, 汇总中第 3 条状态为 failed

### Requirement: LLM 4xx 快速失败

LLM provider 对 HTTP 4xx 响应 (429 除外) MUST 立即失败不重试, 错误信息 MUST 含状态码与响应体摘要 (上下文超长等 400 类问题可直接诊断); 5xx 与网络错误的既有重试行为 MUST 保持不变.

#### Scenario: 400 超长不再重试三次

- **WHEN** 某规则 prompt 超出模型上下文, sglang 返回 400
- **THEN** 该次调用立即失败且错误含 400 与 body 摘要, 不产生额外 2 次重试等待

#### Scenario: 429/5xx 照旧重试

- **WHEN** sglang 返回 429 或 503
- **THEN** 重试行为与本 change 之前一致
