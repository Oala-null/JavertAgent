## ADDED Requirements

### Requirement: Hub 表族前缀可安全配置

Hub 数据访问层 SHALL 支持通过单一配置为所有固定 `TB_*` 基表名添加表名前缀。配置为空时 MUST 保持既有无前缀表名和内部列契约不变；配置非空时，批量 ETL、2C Hub 兜底与工作台实时 Hub 源 MUST 对同一前缀表族取数，MUST NOT 在缺表或查询失败时静默回退到无前缀表。表名前缀 MUST 仅允许安全 SQL 标识符字符并限制长度，非法值 MUST 在执行查询前失败。

#### Scenario: desus 表族单患者取数

- **WHEN** `JAVERT_HUB_DATABASE=TP_data_hub` 且 `JAVERT_HUB_TABLE_PREFIX=desus_`
- **THEN** 所有 Hub 查询引用 `desus_TB_*` 表，并把结果映射为与无前缀源相同的内部列契约

#### Scenario: 默认配置保持现状

- **WHEN** 未设置 Hub 表名前缀
- **THEN** Hub 查询仍引用既有 `TB_*` 表，SQL 与调用方行为不变

#### Scenario: 非法前缀拒绝执行

- **WHEN** 表名前缀包含点号、引号、括号、空白或 SQL 操作符
- **THEN** 配置加载或表名构造在发出任何数据库查询前失败

### Requirement: 脱敏单病人结果带来源标签

使用隔离的脱敏 Hub 表族进行验收时，系统 SHALL 仅运行该表族 manifest 中确认的患者，并 MUST 将每条持久化审计结果的 `batch_tag` 设为 `desus`，使 workbench 可按该 tag 筛选。该流程 MUST NOT 触发无患者范围的历史 pending 同步。

#### Scenario: 唯一脱敏病人进入 workbench

- **WHEN** `desus_patient_manifest` 只列出一个患者且该患者完成审计
- **THEN** workbench 结果库中该次患者规则结果均带 `batch_tag=desus`，且本次运行不产生其他患者结果

### Requirement: 隔离数据源命中项目可自包含回放

系统 SHALL 在审计结果持久化前，使用本次审计实际读取的患者收费切片确定性生成命中项目缓存，并将“已由审计收费快照验证”作为可识别的缓存元数据随 SQLite/SQL Server run 一起写入。workbench 在实时收费源不可用或指向其他表族时 MUST 仍能展示该已验证缓存中的真实净正收费项目；未验证的旧收费缓存 MUST 保持现有重新关联患者收费行的安全行为，MUST NOT 因 locator 或搜索词直接显示收费命中。

#### Scenario: desus 结果脱离临时快照仍显示命中

- **WHEN** `desus` 患者审计完成后私密六域临时目录已清理，且 workbench 默认 Hub 未指向 `desus_TB_*`
- **THEN** 包含真实收费证据的卡片仍从已验证缓存展示项目名、编码和费用页定位锚点

#### Scenario: 旧收费缓存继续重验

- **WHEN** 历史 `anchors_json` 是无验证元数据的旧列表并包含 fee/drug 项
- **THEN** workbench 必须用当前患者净正收费重新计算，不得直接信任旧收费缓存

#### Scenario: 已有 desus run 定向回填

- **WHEN** 对 `batch_tag=desus` 且 manifest 唯一患者的既有 run 使用同一 `desus` 收费切片执行确定性回填
- **THEN** 只更新该患者该 tag 的 `anchors_json`，不调用 LLM、不增删 run、不修改 verdict、review 或其他患者结果

### Requirement: 混合工作台按批次选择原文 Hub profile

工作台 SHALL 支持通过显式配置把 `batch_tag` 映射到只读 Hub database/table prefix profile。
当 CSV 无该患者数据时，系统 MUST 只对 latest batch tag 命中 profile 的患者使用对应 Hub 源；
未命中 profile 的患者 MUST 继续使用默认 Hub 源。profile 的 database 与 table prefix MUST 经过
安全标识符校验，且 profile 不得改变审计结果、全局默认 Hub 配置或其他患者取数。

#### Scenario: desus 原文页签从隔离表族加载

- **WHEN** 患者 latest batch tag 为 `desus`，profile 映射到 `TP_data_hub/desus_`，且 CSV 无该患者
- **THEN** 费用、文书、检验/检查页签从 `desus_TB_*` 返回数据，命中费用锚点可在右侧面板定位

#### Scenario: 其他患者保持默认 Hub

- **WHEN** 患者 latest batch tag 没有配置 profile
- **THEN** CSV miss 后仍使用既有 `JAVERT_HUB_DATABASE/JAVERT_HUB_TABLE_PREFIX`，不得查询 `desus_` profile

#### Scenario: profile 配置非法时拒绝启动

- **WHEN** profile database 或 table prefix 包含分号、点号、引号、空白或 SQL 片段
- **THEN** 配置加载在创建 Hub 连接或执行查询前失败
