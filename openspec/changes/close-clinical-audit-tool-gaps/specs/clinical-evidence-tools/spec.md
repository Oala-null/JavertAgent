## ADDED Requirements

### Requirement: 费用单位与医嘱关联可见

系统 SHALL 在 Hub 原表存在对应字段时，把 `MXXMDW` 和 `YZID` 映射为费用契约的可选 `unit` 与 `order_id`，`search_fees` MUST 在非空时展示计价单位。老 CSV 缺列时 MUST 保持原输出且不得报错。

#### Scenario: Hub 费用单位进入工具

- **WHEN** 某费用数量为 4、`MXXMDW=次`
- **THEN** `search_fees` 输出数量 4 和单位“次”，规则无需从项目名后缀猜单位

#### Scenario: 老 CSV 缺单位列

- **WHEN** 费用 CSV 没有 `unit` 或 `order_id`
- **THEN** `search_fees` 保持既有量价输出，不显示空占位且不抛异常

### Requirement: 医嘱结构化优先且全文兜底

系统 SHALL 提供 `search_orders` 工具。Hub 的结构化医嘱行 MUST 规范化进入 notes 契约并优先返回；结构化医嘱不存在时，工具 MAY 从医嘱类病历全文返回关键词上下文，但 MUST 标记为非结构化候选且 MUST NOT 伪造数量、执行状态或医嘱类型。

#### Scenario: 结构化 ST 医嘱

- **WHEN** `TB_CIS_DRADVICE_DETAIL` 有项目名、`YZLB=ST`、数量和下达/执行时间
- **THEN** `search_orders` 返回一条结构化临时医嘱及其数量和时间

#### Scenario: OCR 历史病例全文兜底

- **WHEN** 结构化医嘱表无行，但医嘱类文书包含“结膜囊冲洗费 st”
- **THEN** `search_orders(keyword="结膜囊冲洗")` 返回该上下文并明确标记“非结构化候选”

### Requirement: 诊疗目录按有效日期查询

系统 SHALL 提供离线 `catalog_lookup` 工具，支持按编码或项目名称查询，并在提供服务日期时按信息起效/失效日期过滤。编码匹配 MUST 优先于名称匹配；多个有效候选或无有效候选时 MUST 如实返回歧义/缺失，不得选择性隐藏。

#### Scenario: 历史日期不用未来单位

- **WHEN** 查询 2026-04-20 的眼压检查，而 2026-07-01 版本有 2026-06-30 才生效的“单侧”条目
- **THEN** 工具不得用未来条目覆盖历史日期，并返回当日有效候选

#### Scenario: 编码精确命中

- **WHEN** 输入国家医保编码和服务日期恰好命中一个有效条目
- **THEN** 返回项目名、计价单位、收费标准、备注和有效期

### Requirement: OCR 报告全文作为弱兜底

`search_examinations` 与 `search_lab_results` 在结构化 Loader 无记录时 SHALL 查询同患者报告类病历全文。命中结果 MUST 标注“非结构化报告候选”；结构化 Loader 有记录时 MUST 保持原权威路径，不混入全文重复项。

#### Scenario: AB 超报告只在文书

- **WHEN** 检查表无行而报告类文书含“眼科AB型超声检查报告单”
- **THEN** `search_examinations(keyword="AB型超声")` 返回非结构化报告候选，而不是简单声称患者无检查报告

#### Scenario: 结构化报告优先

- **WHEN** 检查表已有对应报告，同时病历全文也包含同一报告
- **THEN** 工具只走结构化结果，不重复返回全文候选
