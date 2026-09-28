## ADDED Requirements

### Requirement: 2C 每次实际入队都有 attempt 标识

系统 SHALL 为每个实际入队的 2C 患者审计生成不含患者标识的 `attempt_id`，并在 `POST /api/audit/submit` 的 accepted 项与后续 `GET /api/audit/results/{SYXH}` 响应中回显。相同 SYXH 在运行中重复提交 MUST 幂等复用当前 attempt，不得新增队列任务；已完成后重新提交 MUST 生成新 attempt。

#### Scenario: 运行中重复提交复用 attempt

- **WHEN** 同一 SYXH 已处于 running，2C 再次提交该患者
- **THEN** 接口不重新探测本地或 Hub 数据源，返回 accepted 且 `attempt_id` 与当前任务一致，队列中不新增该患者任务

#### Scenario: 完成后重跑生成新 attempt

- **WHEN** 同一 SYXH 的上一轮任务已完成后再次提交
- **THEN** 新 accepted 项包含不同的 `attempt_id`，查询响应关联新 attempt

### Requirement: 2C 追加明确完成结果与进度

查询接口 MUST 保留既有 `status/results/summary/error` 字段，并追加 `outcome`、`error_code`、`retryable`、`attempt_id` 与 `progress`。`outcome` MUST 区分 `unknown/running/succeeded/partial/failed`；`progress` MUST 至少包含 `total/completed/failed`。

#### Scenario: 患者级异常不可伪装成功

- **WHEN** Hub 取数或 Router 在任何规则落库前抛出异常
- **THEN** 响应保持兼容的 `status=done`，但 `outcome=failed`、`results=[]`、`retryable=true` 且 `error_code` 为稳定非空机器码

#### Scenario: 单规则失败返回 partial

- **WHEN** 已选择多条规则且至少一条成功落库、至少一条审计或持久化失败
- **THEN** 终态响应为 `outcome=partial`，`progress.completed` 与 `progress.failed` 分别反映数量，成功结果仍可见

#### Scenario: 所有入选规则失败返回 failed

- **WHEN** 已选择至少一条规则且所有规则均审计或持久化失败，没有成功结果
- **THEN** 终态响应为 `outcome=failed`、`error_code=RULE_FAILURES`、`retryable=true` 且 `results=[]`

#### Scenario: Router 无候选是成功空结果

- **WHEN** Hub/本地取数与 Router 均成功，但 Router 未选择任何规则
- **THEN** 响应为 `status=done, outcome=succeeded, total=0, results=[]`，且无错误码

### Requirement: 2C 对模型格式失败返回可操作诊断

当某规则因模型输出截断或格式修复失败而保守落为 INCONCLUSIVE 时，2C 结果 MUST 返回全中文、可展示的 reasoning，并追加稳定 `diagnostic_code` 与 `retryable=true`；内部英文错误串不得直接暴露给调用方。

#### Scenario: length 截断最终失败

- **WHEN** Runner 在有界恢复后仍因 length 截断无法得到合法工具调用或 verdict
- **THEN** 2C 返回 `verdict=INCONCLUSIVE`、中文格式异常说明、`diagnostic_code=LLM_OUTPUT_TRUNCATED` 和 `retryable=true`

#### Scenario: 普通 malformed 最终失败

- **WHEN** 非 length 的 verdict JSON 与一次 repair 均无法解析
- **THEN** 2C 返回 `verdict=INCONCLUSIVE`、中文格式异常说明、`diagnostic_code=LLM_OUTPUT_MALFORMED` 和规则级 `retryable=true`，顶层 `retryable` 也为 true
