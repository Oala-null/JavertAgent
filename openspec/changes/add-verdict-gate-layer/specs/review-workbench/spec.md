## ADDED Requirements

### Requirement: 缺文书 facet 过滤

工作台 SHALL 提供 `缺文书` facet, **默认隐藏**被 gate 打了 `gate_tag=缺文书` 的裁决项 (这些是「待线下核查」噪音, 用户要求展示时不看), 并提供开关让专家按需翻看。facet MUST 与既有 verdict filter / 其他 facet 叠加 (纯前端 show/hide)。

#### Scenario: 默认隐藏缺文书项

- **WHEN** 专家打开某患者违规列表, 其中含 `gate_tag=缺文书` 的项
- **THEN** 这些项默认不显示, 列表只剩真正需要看的违规/疑似

#### Scenario: 开关翻看缺文书

- **WHEN** 专家切换 `缺文书` facet 为显示
- **THEN** 被隐藏的缺文书项重新出现, 标注「待线下核查」

#### Scenario: 与 verdict filter 叠加

- **WHEN** 专家同时筛 `仅看 VIOLATION` 且 `隐藏缺文书`
- **THEN** 两个条件 AND 叠加生效, 不互相覆盖

### Requirement: gate 改判可追溯

工作台 SHALL 让专家能识别一条裁决是否被 gate 降级 (展示 `gate_tag` 与降级 reason), 便于核对 gate 行为。

#### Scenario: 展示降级标签

- **WHEN** 一条裁决 `gate_tag=单次放过` (被 gate 从 V 降为 C)
- **THEN** 详情页展示该标签与原因, 专家可判断 gate 是否正确
