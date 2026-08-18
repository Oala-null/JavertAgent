## ADDED Requirements

### Requirement: 眼科扩展规则按名称或编码召回

Router SHALL 对 R319-R322 使用收费项目核心名称与国家医保编码并集召回。医院在项目名后附加“费/单侧/单睑/部位/次”时 MUST 仍能召回；只有 A 超或只有 B 超时 R322 MAY 被 Router 召回，但 coexist_review precheck MUST 在 LLM 前确定性 CLEAN，双有时只注入中性共存事实。

#### Scenario: 项目名带计价后缀

- **WHEN** 费用名为“眼压检查费/单侧”或“睑治疗费/单睑”
- **THEN** Router 分别召回 R319 或 R320

#### Scenario: 编码召回医院别名

- **WHEN** 项目名不含标准中文关键词但国家医保编码命中规则 trigger_codes
- **THEN** Router 仍召回对应眼科规则

#### Scenario: A/B 共存进入审计

- **WHEN** 同一患者同时含 A 型超声与 B 型超声项目
- **THEN** Router 召回 R322，coexist_review precheck 产生不预设附属关系的费用共存事实块
