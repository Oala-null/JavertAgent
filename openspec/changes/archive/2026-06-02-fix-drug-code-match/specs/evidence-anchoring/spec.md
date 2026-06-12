## MODIFIED Requirements

### Requirement: 命中项目编码富集

`hit_resolver` 将一条 drug/fee 证据解析为「命中项目」时, drug 分支 SHALL **优先按国家医保药品码** join 患者实际 fee 行得到 `code_nat`/`code_local`, 仅在证据无可用码时退回通用名 stem 子串。系统 MUST NOT 因通用名子串相似而把命中项目锚到**错误**的 fee 行/编码。

#### Scenario: 相似药名锚到正确编码

- **WHEN** 一条 drug 证据对应通用名 `奥美拉唑`, 患者 fee 同时存在 `奥美拉唑肠溶胶囊` 与 `艾司奥美拉唑肠溶片` (码不同)
- **THEN** `_match_fee_rows` 仅返回码匹配的 `奥美拉唑` fee 行及其 `med_list_codg`, **不**把 `艾司奥美拉唑` 行的编码错配给该命中项目

#### Scenario: 命中项目编码与审计工具一致

- **WHEN** 工作台渲染某药品规则违规卡的「命中项目」块
- **THEN** 所显 `code_nat · name` 与 `drug_audit_lookup` 审计时码 join 命中的 fee 行一致 (共用 `code_match` 纯函数)

#### Scenario: 无码证据兜底不臆造编码

- **WHEN** 某 drug 证据在患者 fee 里找不到任何国家码匹配行 (仅名兜底)
- **THEN** 命中项目仍出一条 (drug 带限定 `basis`), 但编码列留空并标 `needs_review`, MUST NOT 猜配一个相似药的码
