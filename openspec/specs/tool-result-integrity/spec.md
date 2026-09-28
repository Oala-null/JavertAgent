# tool-result-integrity Specification

## Purpose
保证 audit runner 截断工具结果时不丢失裁决所必需的头部信息 (病案首页 ground truth、反向语义告警等), 只裁明细段并给出可见提示, 且截断上限可通过配置调整。

## Requirements
### Requirement: 分段截断保全必留头部

工具结果含必留标记行时, audit runner 的截断 MUST 保全标记之前的整段内容, 只对标记之后的明细段截断; 发生截断时 MUST 在结果尾部附加可见的截断提示 (含被截字符数量级信息). 不含标记的工具结果 MUST 保持现有尾截断行为不变. 必留头部本身超过截断上限时, runner MUST 对头部也硬截到上限并附带可见的头部截断提示 (防止单条工具结果整体超预算导致上下文溢出), 而非无条件全留.

#### Scenario: drug bulk 超长输出 ground truth 完整

- **WHEN** `drug_audit_lookup` bulk 输出总长超过截断上限, 且『患者病案首页诊断 ground truth』块位于必留段, 且必留头部本身不超上限
- **THEN** 喂给 LLM 的工具结果中 ground truth 块逐字完整, 被截的只有命中药明细, 且末尾带截断提示

#### Scenario: 必留头部超总预算时硬截

- **WHEN** 必留头部 (标记及之前内容) 长度超过截断上限
- **THEN** 头部被硬截到上限、明细段全部丢弃, 结果末尾带「头部超预算」截断提示, 且总长不超过上限量级

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

### Requirement: 必留头部标记语义进 base prompt

`base.txt` MUST 含一行说明 `====[必留头部结束]====` 标记的语义 (标记之前是本次截断保全的必留证据、之后是可能被截的明细), 使 LLM 能正确解读工具结果中出现的该标记.

#### Scenario: prompt 含标记说明

- **WHEN** 读取 `src/javert/audit/prompts/base.txt`
- **THEN** 存在解释 `====[必留头部结束]====` 标记含义的说明行
