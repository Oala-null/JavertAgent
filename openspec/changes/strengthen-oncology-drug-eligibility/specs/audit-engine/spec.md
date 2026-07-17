# audit-engine — delta spec

## ADDED Requirements

### Requirement: AuditResult 携带可选结构化资格结果

`AuditResult` SHALL 在既有 `verdict`、`confidence`、`reasoning`、`evidence`、`tool_calls` 及运行元数据之外新增可选顶层字段 `eligibility_evaluation`。该对象存在时 MUST 包含 `audit_disposition` (`NO_VIOLATION_FOUND / VIOLATION_FOUND / REVIEW_REQUIRED`)、`eligibility_status` (`SATISFIED / NOT_SATISFIED / DOCUMENTATION_GAP / CONFLICT`)、`criterion_assessments[]`、`proof_tree`、`data_quality_flags[]` 和 `documentation_suggestions[]`; 文书建议项 MUST 与证据和条件状态分离, 不得反向修改任何 `criterion_assessments` 或 `proof_tree` 状态。未接入结构化资格求值的规则 MUST 使用 `eligibility_evaluation=None` 并保持原结果行为。

#### Scenario: 肿瘤药审核返回完整结构化结果

- **WHEN** `RD04` 完成一个已编译医保限定的肿瘤药审核
- **THEN** `AuditResult.eligibility_evaluation` 包含逐条件状态、证明树、双轴结果、数据质量标志及条件级文书建议, 同时既有 `reasoning` 与 `evidence` 仍可供旧界面读取

#### Scenario: 非肿瘤规则不被强制伪造资格结果

- **WHEN** 一条既有非肿瘤规则完成审核且没有结构化资格求值
- **THEN** `eligibility_evaluation` 为 `None`, 其 `verdict`、`reasoning`、`evidence`、`tool_calls` 和运行元数据与变更前保持一致

#### Scenario: 文书建议不改变事实状态

- **WHEN** 某医保条件因文书未记录而为 `UNKNOWN`, 并生成一条 `documentation_suggestions` 建议
- **THEN** 该条件仍为 `UNKNOWN`, `proof_tree` 不得因建议文字变为 `SATISFIED`, 审核处置只能由资格求值与策略映射产生

### Requirement: 双轴结果投影为旧 verdict

当 `eligibility_evaluation` 存在时, 既有 `verdict` SHALL 是 `audit_disposition` 的确定性兼容投影: `NO_VIOLATION_FOUND → CLEAN`, `VIOLATION_FOUND → VIOLATION`, `REVIEW_REQUIRED → INCONCLUSIVE`。`eligibility_status` MUST 保持为独立轴, 因此 `DOCUMENTATION_GAP` 不得被硬编码为单一旧 verdict; 系统 MUST 根据审核处置分别表达“未发现违规但建议补充文书”与“关键事实不足需人工复核”。任何与投影规则矛盾的结构化结果和旧 verdict 组合 MUST 在写入前被拒绝或规范化, 不得持久化自相矛盾的数据。

#### Scenario: Pola 移植适合性缺口金标兼容为 CLEAN 且保留文书缺口

- **WHEN** Pola 移植适合性缺口合成金标结果为 `audit_disposition=NO_VIOLATION_FOUND`、`eligibility_status=DOCUMENTATION_GAP`, 建议如拟使用维泊妥珠单抗则补充“不适合造血干细胞移植”的文书及简要原因
- **THEN** 旧 `verdict` 为 `CLEAN`, `documentation_suggestions` 被完整保留, 系统不得把74岁本身写成移植不适合的确定性医学证明

#### Scenario: Pola 周期冲突金标兼容为 INCONCLUSIVE 且保留文书缺口

- **WHEN** Pola 周期冲突合成金标因早期治疗史不全及时间冲突得到 `audit_disposition=REVIEW_REQUIRED`、`eligibility_status=DOCUMENTATION_GAP`
- **THEN** 旧 `verdict` 为 `INCONCLUSIVE`, 不得仅凭“第四次方案化疗”改写为 CLEAN 或 VIOLATION

#### Scenario: 明确不满足兼容为 VIOLATION

- **WHEN** 结构化求值具有决定性反证且结果为 `audit_disposition=VIOLATION_FOUND`、`eligibility_status=NOT_SATISFIED`
- **THEN** 旧 `verdict` 为 `VIOLATION`, 旧消费者无需理解新字段仍能得到正确裁决

#### Scenario: 矛盾投影不得落库

- **WHEN** 待写入结果同时携带 `audit_disposition=NO_VIOLATION_FOUND` 和 `verdict=VIOLATION`
- **THEN** 结果校验 MUST 拒绝该组合或在持久化前按映射规范化为 `CLEAN`, 数据库中不得出现不一致记录

### Requirement: 结构化资格结果持久化与旧记录加载

SQLite `audit_runs` 与 SQL Server `javert_audit_runs` SHALL 使用单一 nullable `eligibility_json` 字段持久化 `eligibility_evaluation` 的完整 JSON, schema 迁移 MUST 幂等。写入和跨库同步 MUST 保持该对象不丢字段; 读取时 SHALL 反序列化为 `AuditResult.eligibility_evaluation`。变更前没有该列或该值为 `NULL`/空值的历史记录 MUST 正常加载为 `eligibility_evaluation=None`, 并逐字保留原 `verdict`、`reasoning`、`evidence` 和 `tool_calls`; 旧记录不得因缺少新字段被重判或回填推测性资格状态。

#### Scenario: 结构化结果双库往返不丢失

- **WHEN** 一个包含条件证据、证明树、数据质量标志和文书建议的 `AuditResult` 写入 SQLite、同步到 SQL Server并分别读回
- **THEN** 两次反序列化的 `eligibility_evaluation` 与写入对象语义等价, 既有结果字段也保持不变

#### Scenario: SQLite 旧表幂等增加 eligibility_json

- **WHEN** `SqliteStore` 打开一个存在 `audit_runs` 但没有 `eligibility_json` 的旧数据库
- **THEN** 迁移只增加一次 nullable `eligibility_json`, 重启后不重复执行且既有行仍可读取

#### Scenario: SQL Server 旧表幂等增加 eligibility_json

- **WHEN** 部署脚本检查到 `javert_audit_runs` 尚无 `eligibility_json`
- **THEN** 系统幂等增加可空列而不删除、重建或改写历史审核行

#### Scenario: 历史记录缺少新字段仍可加载

- **WHEN** 读取一条仅含旧 `verdict/reasoning/evidence_json/tool_calls_json` 的历史审核记录
- **THEN** `AuditResult` 正常构造且 `eligibility_evaluation=None`, 旧字段值与变更前一致, 工作台和 API 不报错

#### Scenario: 新 API 对旧消费者保持兼容

- **WHEN** API 或 SSE 返回一个带 `eligibility_evaluation` 的新审核结果
- **THEN** 既有顶层 `verdict/reasoning/evidence` 字段仍存在且语义不变, 不读取新字段的客户端仍能正常显示和统计

### Requirement: 结构化扩展不改变既有 precheck-first 路径

本变更 MUST 保留“带 precheck 字段规则的 precheck-first 执行路径”的全部既有语义。未声明结构化肿瘤资格求值的规则 MUST 按原顺序执行 precheck、LLM loop 与 `apply_gate`; precheck `clean` 仍为零 LLM、零工具调用短路, precheck `facts` 仍注入事实并在 VIOLATION 时合并费用 evidence, `skip`/无 precheck/开关关闭仍走既有 LLM loop。新结构化字段的默认值和持久化 MUST NOT 增加工具调用、改变 gate 结果或导致旧测试结果漂移。

#### Scenario: precheck clean 仍零调用短路

- **WHEN** 某既有 M1 规则的 precheck 返回 `clean` 且该规则未接入结构化肿瘤资格求值
- **THEN** `Runner.audit` 仍返回 `verdict=CLEAN`、`tool_calls` 为空、没有 LLM 调用, 并令 `eligibility_evaluation=None`

#### Scenario: precheck facts 仍合并费用锚点

- **WHEN** 某既有规则 precheck 返回 `facts` 且 LLM 最终裁决 VIOLATION
- **THEN** 结果仍合并预检费用 evidence 并保留原 precheck 标签, 新字段不得覆盖或丢失该证据

#### Scenario: 无 precheck 规则执行路径不变

- **WHEN** 一条既有规则既无 `precheck` 也未接入结构化肿瘤资格求值
- **THEN** 执行顺序、LLM/tool 调用、`apply_gate` 和最终旧 verdict 与变更前一致, 唯一新增模型默认值为 `eligibility_evaluation=None`
