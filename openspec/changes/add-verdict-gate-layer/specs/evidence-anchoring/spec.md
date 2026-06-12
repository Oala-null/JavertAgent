## MODIFIED Requirements

### Requirement: 证据来源归一与锚定

`hit_resolver` 将证据解析为命中项目时, SHALL **不再静默丢弃** `lab` / `examination` 来源的证据 —— 它们 MUST surface 为可见命中项目 (展示项名 + 日期 + 数值/结论), 让指征即使来自 `search_lab_results` / `search_examinations` 也能被专家看到。`etl_warning` 来源 MAY 仍不锚 (它表征缺失而非实证), 但被 gate 打了 `缺文书` 标签的项 SHALL 可在工作台识别。

#### Scenario: 检验证据可见

- **WHEN** 一条 evidence `source=lab, locator="甲胎蛋白 2024-08-08", text="AFP 12.3"`
- **THEN** `hit_resolver` 产出一条可见命中项目 (项名+日期+数值), MUST NOT 因 source 非 note/fee/drug 而丢弃

#### Scenario: 检查证据可见

- **WHEN** 一条 evidence `source=examination` (如心脏彩超报告)
- **THEN** surface 为命中项目, 专家可见该指征来源

#### Scenario: 病程症状命中精确高亮

- **WHEN** 审计经 `scan_progress_indications` 把症状词 (含子阶段+char 偏移) 写入 note 证据
- **THEN** openSourcePanel 按该关键词在原文 level-2 精确高亮 (不再因只查诊断而跳不到原文)
