# data-access — delta spec

## ADDED Requirements

### Requirement: 费用行明细字段暴露

`search_fees` 的行输出在源数据存在对应列时 MUST 包含 单价与数量 (形如 `单价×数量`) 以及 开单科室/开单医师; 源数据缺列时对应字段 MUST 整体省略 (不出现空占位符), MUST NOT 报错. 行锚 `⟨行=i⟩` 与既有列的文本 MUST 保持不变 (只追加).

#### Scenario: 量价信号可见

- **WHEN** 患者费用行含 单价 86.00、数量 3, LLM 调用 `search_fees`
- **THEN** 该行输出含 `86.00×3` 形态的量价信息, 合计金额列不变

#### Scenario: 科室医师可见

- **WHEN** hub/内部数据带开单科室与开单医师列
- **THEN** 行输出含科室与医师, 串换科室/分解收费类规则可据此审计

#### Scenario: 缺列优雅省略

- **WHEN** 外部医院 CSV 未提供单价/科室/医师列
- **THEN** 行输出与本 change 之前一致 (无新增字段、无占位符、无报错)
