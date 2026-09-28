## ADDED Requirements

### Requirement: 费用原始计价字段无损映射

Hub 费用映射 SHALL 保留原表 `MXXMDW` 与 `YZID`，分别输出为可选 `unit` 与 `order_id`。该追加 MUST 同时更新 schema manifest，且旧 CSV 缺列时保持兼容。

#### Scenario: 计价字段跨 Hub 到内部契约

- **WHEN** Hub 费用行含 `MXXMDW=次` 和非空 `YZID`
- **THEN** 内部费用 DataFrame 对应行包含相同的 `unit` 与 `order_id`

### Requirement: 结构化医嘱合流到文书契约

Hub 文书读取 SHALL 在医嘱表存在时读取患者医嘱，并把每条医嘱转换为可定位的 notes 行；医嘱表缺失或为空时 MUST 保持原文书行为。结构化医嘱行 MUST 带独立来源标记，供工具避免与全文医嘱重复计数。

#### Scenario: 医嘱表有行

- **WHEN** 患者有两条结构化医嘱和一份病历文书
- **THEN** `fetch_notes` 返回一份原文书加两条 `data_hub_advice` 行

#### Scenario: OCR 医嘱表为空

- **WHEN** OCR 患者医嘱表为 0 行
- **THEN** `fetch_notes` 仍返回原有医疗文书，且不会制造空医嘱行
