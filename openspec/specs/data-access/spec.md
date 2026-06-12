# data-access Specification

## Purpose
TBD - created by syncing change fix-fee-refund-netting. Update Purpose after archive.
## Requirements
### Requirement: 退费净额聚合

数据访问层 SHALL 提供按项目的退费净额聚合, 使「该费用是否用过 / 收了几次 / 明细」语义按 `+N/-N` 净额计算。系统 MUST 按 `med_list_codg` (无码退项目名) 分组对 `cnt` 求和, 净额 ≤ 0 的项 MUST 从「用过」判定与明细展示中剔除; 净额 > 0 的项 SHALL 显示净量。原始全量 fee 行 (含退费) MUST 仍可访问 (审计留痕)。

#### Scenario: 完全充退项不计不显示

- **WHEN** 某药 `地佐辛(易可定)注射液` 共 12 行、跨 5 个日期, 但 `sum(cnt) = 0`
- **THEN** net helper 判该项净量 0, `search_fees` 明细**不**列该项、计数**不**含该项, 工作台费用区也不显示

#### Scenario: 部分退显示净量

- **WHEN** 某项收 `+2` 后退 `-1` (净 1)
- **THEN** 该项保留, 显示净量 1, MUST NOT 整条剔除

#### Scenario: 计数等于净不同收费

- **WHEN** `住院诊疗费` 共 40 行、含 7 次退费、净 33、覆盖 33 个不同日期
- **THEN** helper 暴露该项 `net_qty=33` 与 `distinct_billing_dates=33`, 供次数门控使用

#### Scenario: search_fees 计数不再虚高

- **WHEN** 患者某费用项有退费行, 调 `search_fees(keyword=...)`
- **THEN** 返回的「共 N 条」与明细按净额计, 不含被抵消的退费行

#### Scenario: 原始退费历史仍可访问

- **WHEN** 调用方需要查看「曾收曾退」的原始行
- **THEN** 原始全量 fee 访问 (`all_fees`) 仍返回含退费行的完整数据, 净额聚合是独立显式调用
