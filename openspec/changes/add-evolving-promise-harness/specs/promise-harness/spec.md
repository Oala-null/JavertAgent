## ADDED Requirements

### Requirement: Promise Harness 必须完全离线且确定
`javert promise run` MUST 只使用仓库内的 Promise 定义、去标识案例、确定性 evaluator、公开 presenter fixtures 和本地分类源资产；命令 MUST NOT 调用 LLM、网络、SQL Server、生产 hub 或其他外部数据库。

#### Scenario: 在隔离环境运行
- **WHEN** 网络、LLM 和数据库连接均不可用时执行 `javert promise run`
- **THEN** 命令仍能完成全部案例
- **AND** 结果只由版本化仓库资产和纯函数实现决定

#### Scenario: 实现尝试访问外部依赖
- **WHEN** 某个 Promise evaluator 或 harness fixture 尝试发起网络、LLM 或数据库调用
- **THEN** 测试立即失败并返回非零状态
- **AND** 失败报告标识 case/promise 和依赖类型，不包含连接凭据或患者数据

### Requirement: Harness 必须验证定义、引用和公开映射
`javert promise validate` MUST 校验 schema、typed kind、scope、版本链、唯一 active head、source case 状态、positive/near-negative 完整性、禁止敏感字段、行为类别 H/I 源表映射及显式例外。

#### Scenario: 任一引用失效
- **WHEN** active Promise 引用不存在的规则、案例、版本、行为映射或未声明例外
- **THEN** validate 返回非零状态
- **AND** 不生成可供运行时加载的有效 Promise 集

#### Scenario: 全部引用有效
- **WHEN** 所有定义、案例、规则 scope、版本链和类别映射均合法
- **THEN** validate 返回零状态
- **AND** 以规范顺序报告 Promise 数量、案例数量和显式例外数量

### Requirement: 每条 Active Promise 必须同时证明命中与不越界
Harness MUST 对每个 active Promise 执行全部 positive 和 near-negative 案例；positive MUST 得到声明的保证和终局属性，near-negative MUST 返回 NOT_APPLICABLE 或该案例明确声明的非保证结果。

#### Scenario: 退费净数量正例
- **WHEN** `PR-D001` 案例处于显式计数型规则 scope、目标费用组唯一、确有退费且退费后净数量不大于 1
- **THEN** evaluator 返回 LOCKED CLEAN 和稳定 reason code

#### Scenario: 相邻但不应命中的反例
- **WHEN** `PR-D001` 案例无退费、净数量大于 1、目标不唯一、数量不可解析、规则不在 scope，或属于一次即违规/组合项目/M1 主附项目语义
- **THEN** evaluator 不得返回该 Promise 的 CLEAN 保证

### Requirement: 历史晋升案例必须持续回归
Harness MUST 在运行 active Promise 案例之外继续加载所有已 promoted 的 DriftCase 和 superseded 版本的历史边界案例，以证明新版本没有无说明地遗忘既有保证或扩大到旧反例。

#### Scenario: 新版本破坏历史正例
- **WHEN** 新 active 版本使任一已 promoted 的历史正例不再满足其有效保证，且没有经过显式收窄迁移说明
- **THEN** harness 失败并返回非零状态

#### Scenario: 新版本误命中历史反例
- **WHEN** 新 active 版本开始命中某个历史 near-negative，而版本迁移没有明确允许并提供新证据
- **THEN** harness 失败并报告 unexpected match

### Requirement: Harness 必须检测非确定输出和 Promise 冲突
Harness MUST 对同一案例至少执行两次，并在移除时间、随机 run ID 等非业务元数据后比较规范化输出；若输出不同或多个 terminal Promise 给出不同保证，命令 MUST 失败。

#### Scenario: 重复运行产生不同结果
- **WHEN** 同一案例的规范化 verdict、reason code、trace facts、公开解释、hits 或类别在两次执行间不同
- **THEN** harness 返回非零状态并标记 `NON_DETERMINISTIC_OUTPUT`

#### Scenario: 两条 Promise 给出相反保证
- **WHEN** 同一规则与事实同时命中两个 terminal Promise，且保证不一致
- **THEN** harness 返回非零状态并标记 `PROMISE_CONFLICT`
- **AND** 不按配置加载顺序选择赢家

### Requirement: Harness 报告必须适合提交门禁且不泄露数据
命令 MUST 提供人类可读与 `--json` 两种稳定报告；任一 schema、案例、冲突、映射或确定性检查失败时 MUST 返回非零状态。报告 MUST 仅包含 case/promise ID、状态、reason code、汇总计数和安全耗时分桶。

#### Scenario: 门禁成功
- **WHEN** 所有 active 与历史案例、公开 presenter fixtures 和映射检查通过
- **THEN** 命令返回零状态
- **AND** JSON 输出可由 CI 解析且键顺序/集合顺序规范化

#### Scenario: 门禁失败
- **WHEN** 任一检查失败
- **THEN** 命令返回非零状态并列出最小失败定位
- **AND** 输出不包含 patient_id、项目原文、文书、SQL、凭据或完整 trace JSON
