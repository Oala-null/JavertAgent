## ADDED Requirements

### Requirement: 漂移记录与运行时 Promise 必须是分离资产
系统 MUST 将一次已观察漂移记录为去标识的 `DriftCase`，并将运行时保证记录为独立、版本化的 `PromiseDefinition`；`observed` DriftCase MUST NOT 自动进入运行时执行集。

#### Scenario: 新发现仅进入观察态
- **WHEN** 维护者新增一个尚未完成人工确认的漂移案例
- **THEN** 案例状态为 `observed`
- **AND** `javert promise validate` 能发现并校验该案例
- **AND** 审计运行时不加载该案例，也不因此改变裁决

#### Scenario: Git 资产包含敏感标识
- **WHEN** DriftCase 或 PromiseCase 包含患者号、姓名、病历原文、未盐化 run/ownership 标识或禁止字段
- **THEN** 静态校验失败并返回非零状态
- **AND** 报告只指出文件和禁止字段类型，不回显敏感值

### Requirement: Promise 晋升必须有最小边界证据
系统 MUST 只允许同时满足以下条件的 draft Promise 晋升为 active：全部来源案例已确认、使用已注册 typed kind、scope 明确、至少有一个 positive 和一个 near-negative 案例、全部 Promise harness 校验通过、且存在去标识的确认依据和版本说明。

#### Scenario: 只有正例不能激活
- **WHEN** draft Promise 有 positive 案例但没有 near-negative 案例
- **THEN** `javert promise validate` 拒绝该 Promise 进入 active 执行集
- **AND** 错误明确指出缺少相邻反例

#### Scenario: 来源漂移尚未确认
- **WHEN** draft Promise 引用状态为 `observed` 或不存在的 DriftCase
- **THEN** 晋升校验失败
- **AND** 运行时不加载该 Promise

#### Scenario: 完整晋升材料通过
- **WHEN** draft Promise 的来源、typed kind、scope、正反例、确认依据和版本信息均合法，且全量 harness 无失败或冲突
- **THEN** 该版本可以显式标记为 active
- **AND** 激活行为可由 Promise ID、版本和来源案例追溯

### Requirement: Active Promise 必须按版本不可变
系统 MUST 使用新版本和 `supersedes` 链表达 active Promise 的扩大、修正或收窄；系统 MUST NOT 允许同一 Promise 同时存在多个 active head，也 MUST NOT 通过就地修改或删除旧版本改变历史含义。

#### Scenario: 合法替代旧版本
- **WHEN** 新版本声明 `supersedes` 当前 active 版本且版本号递增
- **THEN** 校验允许旧版本变为 `superseded`、新版本变为唯一 active head
- **AND** 旧定义和旧案例仍保留供历史追溯与回归

#### Scenario: 出现分叉 active head
- **WHEN** 两个版本均声明为同一 Promise 的 active head，或 `supersedes` 链分叉、循环、缺失
- **THEN** 校验失败并返回非零状态
- **AND** 审计运行时拒绝加载有歧义的 Promise 集

### Requirement: Promise 条件必须使用受控 typed kind
PromiseDefinition MUST 只能引用代码中注册、具有固定 schema 的 typed kind；配置 MUST NOT 接受任意表达式、任意 Python/SQL、模板执行或自由文本作为运行时判定条件。

#### Scenario: 未注册 kind
- **WHEN** PromiseDefinition 引用未注册的 kind 或携带该 kind schema 之外的执行字段
- **THEN** schema/registry 校验失败
- **AND** 该定义不能进入 active 执行集

### Requirement: Promise 不得从在线输出静默学习
系统 MUST NOT 因单次 LLM 输出、专家自由文本、工作台操作或在线统计而自动新增、扩大、激活或停用 Promise；任何运行时冲突只能生成去标识的候选漂移记录或指标，仍需受控确认和版本晋升。

#### Scenario: 线上结果与锁定保证冲突
- **WHEN** 线上历史或后续组件给出与 active Promise 不同的结论
- **THEN** 系统记录匿名冲突事实用于后续调查
- **AND** 不修改 PromiseDefinition、active head 或既有案例

### Requirement: 新 Promise 不得静默重写历史
新增、替代或停用 Promise MUST 只影响发布后符合 scope 的新审计执行；旧审计行、专家 review 与既有肿瘤资格 JSON MUST 保持原样，除非另有明确、独立的迁移授权。

#### Scenario: 发布新版本后读取旧结果
- **WHEN** 数据库中存在发布前写入且 `promise_trace_json` 为空的旧结果
- **THEN** 读取、同步和工作台展示仍兼容该旧行
- **AND** 系统不使用新 Promise 静默重算或回填该行
