# tool-result-integrity — delta spec

## ADDED Requirements

### Requirement: 分段截断保全必留头部

工具结果含必留标记行时, audit runner 的截断 MUST 保全标记之前的整段内容, 只对标记之后的明细段截断; 发生截断时 MUST 在结果尾部附加可见的截断提示 (含被截字符数量级信息). 不含标记的工具结果 MUST 保持现有尾截断行为不变.

#### Scenario: drug bulk 超长输出 ground truth 完整

- **WHEN** `drug_audit_lookup` bulk 输出总长超过截断上限, 且『患者病案首页诊断 ground truth』块位于必留段
- **THEN** 喂给 LLM 的工具结果中 ground truth 块逐字完整, 被截的只有命中药明细, 且末尾带截断提示

#### Scenario: search_notes 反向语义告警保全

- **WHEN** `search_notes` 结果超长且含 `[否认段]/[选项框]` 告警 (位于必留段)
- **THEN** 告警内容不被截断

#### Scenario: 无标记工具行为回归

- **WHEN** 其余工具 (如 `search_fees`) 输出超长且不含必留标记
- **THEN** 截断行为与本 change 之前一致 (尾截断到上限)

### Requirement: 截断上限可配置

工具结果截断上限 MUST 可通过 `llm.yaml` 的 `tool_result_max_chars` 配置; 未配置时默认值 MUST 为 2000 (与现状一致).

#### Scenario: 配置生效

- **WHEN** `llm.yaml` 设 `tool_result_max_chars: 4000`
- **THEN** 单工具结果按 4000 字符预算截断

#### Scenario: 未配置默认不变

- **WHEN** `llm.yaml` 未含该键
- **THEN** 截断上限为 2000, 全链路行为与现状一致
