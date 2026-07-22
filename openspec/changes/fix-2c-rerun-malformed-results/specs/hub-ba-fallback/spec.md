## ADDED Requirements

### Requirement: 手术明细使用规范表名

Hub 手术取数及 BA 手术时间回退 MUST 只查询规范表 `TB_OPERATION_DETAIL`，不得查询拼写错误的 `TB_OPRATION_DETAIL`；离线 stub 测试 MUST 对生成 SQL 的规范表名做精确断言。

#### Scenario: IH 手术查询使用规范表

- **WHEN** `fetch_ss` 查询任意患者的 IH 手术明细
- **THEN** SQL `FROM` 使用 `TB_OPERATION_DETAIL`，不包含 `TB_OPRATION_DETAIL`

#### Scenario: BA 时间回退使用规范表

- **WHEN** `fetch_ss` 构造 SYSSK 的手术开始时间回退子查询
- **THEN** 内层聚合查询使用 `TB_OPERATION_DETAIL`，不包含 `TB_OPRATION_DETAIL`
