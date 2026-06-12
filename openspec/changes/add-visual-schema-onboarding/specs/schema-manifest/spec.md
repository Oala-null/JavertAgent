## ADDED Requirements

### Requirement: Schema manifest 唯一真相源

系统 SHALL 提供 `configs/schema_manifest.yaml` 作为数据模型唯一真相源,声明所有 spoke(费用/文书/诊断/手术/化验/检查/麻醉/病理及未来新增)。每个 spoke MUST 声明:中文名、内部字段列表(每字段含 `key`/中文名/`required` 布尔)、消费它的 `loader` 与 `tool`、状态档位 `status`(`live`/`view`/`stored`)、连接键策略(`join_key` 及可选 `via_bridge`)。UI 渲染、ETL 转换、tool registry 注册 MUST 全部从该 manifest 读取,不得各自硬编码字段表。

#### Scenario: 三处共读同一 manifest

- **WHEN** 修改 `schema_manifest.yaml` 中某 spoke 的必填字段集合
- **THEN** GUI 映射表单、`etl_import` 必填校验、tool registry 注册三处 MUST 同步反映该变更,无需改任何代码

#### Scenario: 新增数据类型零改代码

- **WHEN** 在 manifest 追加一段新 spoke(声明字段 + status + join_key)
- **THEN** GUI 星图 MUST 自动多出一个对应星点,ETL MUST 自动多转一张表,无需改 UI 或 ETL 代码

### Requirement: 三档兜底契约

每个在 UI 展示的 spoke MUST 挂一个 `status` 档位,且其最坏情况 MUST NOT 是崩溃或静默丢弃:`live`(专用工具消费,正常产裁决)、`view`(有读取视图 + 概览展示,最坏=被存下且概览可见且诚实标注"暂不参与判定")、`stored`(只存不审,最坏=存下且概览列出原文)。tool registry SHALL 据 status 注册:`live` 注册真工具,`view` 注册视图工具,`stored` 或无 tool 的 spoke 注册兜底 stub。

#### Scenario: view spoke 诚实标注

- **WHEN** 麻醉/病理(status=view)被展示且无审计裁决产出
- **THEN** UI 与工具输出 MUST 标注"已接收·暂不参与判定",MUST NOT 冒充 live 已覆盖

#### Scenario: stored spoke 不静默丢

- **WHEN** 用户声明一个全新表(如"输血记录",无对应工具)
- **THEN** 系统 MUST 存下其数据并在病案概览列出原文,MUST NOT 静默丢弃或报错崩溃

#### Scenario: 无处理路径的 spoke 不予展示

- **WHEN** manifest 中某 spoke 缺少任何 loader/视图/兜底 stub
- **THEN** 该 spoke MUST NOT 出现在 UI 左侧星图(只展示有保证处理路径的 spoke)

### Requirement: 桥表连接键模型

manifest spoke SHALL 支持声明 `join_key`(本表连到 hub 用哪一列)与可选 `via_bridge`(桥表名 + 源键⇄canonical 键的列对)。不同 spoke MUST 允许声明不同的 `join_key`(适配各表患者键不在同一命名空间的真实情况)。

#### Scenario: 逐表声明不同连接键

- **WHEN** 费用表声明 `join_key=hsp_account_no`、文书表声明 `join_key=medcasno` 并指向桥表 `r_basy`
- **THEN** ETL MUST 按各表自己的键 + 桥表交叉表归一,而非假设所有表用同一列

### Requirement: 国标别名种子库

系统 SHALL 提供 `configs/field_alias.yaml`,把国标(医保结算清单 / 病案首页字段码)及常见中文列名映射到 Javert 内部字段语义(如 `fees.amount ← [det_item_fee_sumamt, 金额, ...]`)。该库 SHALL 可从已有 szx/song 映射反推生成。

#### Scenario: 上传列名自动预填

- **WHEN** 上传文件含列 `det_item_fee_sumamt`
- **THEN** 系统 MUST 据别名库自动把它预填映射到 `fees.amount`,用户仅需确认或修改
