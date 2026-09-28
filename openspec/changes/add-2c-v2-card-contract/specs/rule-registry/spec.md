## ADDED Requirements

### Requirement: 2C 与工作台行为大类映射口径

系统 SHALL 通过集中映射将规则 `violation_type` 投影为对外行为认定 code/title。纯虚构医药服务以及“虚构医药服务项目或以骗保为目的串换项目” SHALL 映射为 `T380206 / 提供不必要的医药服务`；明确 `串换项目` SHALL 保持独立大类，且在没有正式编码时 code 为空。

#### Scenario: 纯虚构规则映射

- **WHEN** 规则的 `violation_type` 为 `虚构医药服务项目` 或 `虚构医药服务`
- **THEN** 对外行为认定 SHALL 为 `T380206 / 提供不必要的医药服务`

#### Scenario: 虚构或串换混合规则映射

- **WHEN** 规则的 `violation_type` 为 `虚构医药服务项目或以骗保为目的串换项目`
- **THEN** 当前对外行为认定 SHALL 为 `T380206 / 提供不必要的医药服务`

#### Scenario: 明确串换规则保持独立

- **WHEN** 规则的 `violation_type` 为 `串换项目`
- **THEN** 对外 title SHALL 为 `串换药品、医用耗材、诊疗项目和服务设施`
- **AND** 在未取得正式编码前 code SHALL 为空字符串
