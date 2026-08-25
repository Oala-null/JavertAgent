## ADDED Requirements

### Requirement: Workbook covers the current rule inventory
系统 SHALL 从当前 `configs/rules/*.yaml` 形成协作工作簿，并在主表中为每一个成功加载的当前规则生成且仅生成一行。

#### Scenario: Current inventory is exported once
- **WHEN** 工作簿从当前规则目录生成
- **THEN** 主表规则行数 SHALL 等于当前 Rule YAML 数量，且规则编号 SHALL 唯一、无遗漏、无重复

### Requirement: Main sheet stays simple
工作簿 SHALL 提供名为 `规则协作主表` 的主 Sheet，规则表 SHALL 只包含 `规则编号 / 原始规则原文 / 我们的处理 / 处理等级 / 专家意见 / 状态` 六列，并提供筛选和冻结表头。

#### Scenario: Expert opens the main sheet
- **WHEN** 专家打开工作簿
- **THEN** 第一可见 Sheet SHALL 为 `规则协作主表`，且无需查看技术字段即可完成分级和填写意见

### Requirement: Source wording is traceable
系统 MUST 区分国家 0325 清单规则、药品派生规则和本地专家扩展规则；存在国家来源行时，主表 SHALL 展示来源“问题”原文，技术明细 SHALL 同时保留来源原文和当前规则问题。

#### Scenario: Official rule has a source row
- **WHEN** 当前规则编号能够映射到 0325 来源行
- **THEN** 主表原文 SHALL 来自来源行，技术明细 SHALL 记录来源类型和来源序号

#### Scenario: Derived or expert rule has no source row
- **WHEN** 当前规则没有 0325 来源行
- **THEN** 主表 SHALL 使用当前规则问题，技术明细 SHALL 将其标记为药品派生或专家扩展，不得冒充国家清单原文

### Requirement: Processing summary is concise and reproducible
系统 SHALL 从现有规则字段确定性生成简明“我们的处理”，完整处理文本 SHALL 保留在技术明细中；系统 MUST NOT 使用新的临床推断替规则自动改写含义。

#### Scenario: Rule has a prompt addon
- **WHEN** 规则存在非空 `prompt_addon`
- **THEN** 主表 SHALL 展示其首个实质段落的压缩摘要，技术明细 SHALL 保留完整文本

#### Scenario: Rule lacks processing logic
- **WHEN** `prompt_addon` 为空
- **THEN** 主表 SHALL 明确显示尚未形成自动处理逻辑或现有 notes 提示，不得显示为空白处理结论

### Requirement: Handling level has exactly three options
Rule schema SHALL 提供 `handling_level` 字段并只允许 `违规（阻断） / 可疑（警告） / 提醒（引导）` 三档；当前每个 Rule YAML MUST 显式包含合法值。主表 `处理等级` SHALL 从该字段填充并保持相同三档下拉；工作簿 MUST NOT 提供“合规”作为规则等级。

#### Scenario: Expert assigns a handling level
- **WHEN** 专家编辑任一规则的处理等级
- **THEN** 单元格 SHALL 提供三档下拉选择，并根据所选等级显示一致的红、橙、蓝视觉提示

#### Scenario: Rule omits the field in an external or legacy fixture
- **WHEN** Rule 输入没有 `handling_level`
- **THEN** schema SHALL 使用保守默认值 `可疑（警告）` 保持旧调用兼容

#### Scenario: Rule contains an invalid level
- **WHEN** Rule 输入的 `handling_level` 不属于三档枚举
- **THEN** Rule 加载 SHALL 失败并指出 `handling_level` 字段错误

#### Scenario: Workbook is regenerated from classified rules
- **WHEN** 首版 YAML 分级完成后生成工作簿
- **THEN** 主表每一行的处理等级 SHALL 与对应 Rule YAML 完全一致，且三档计数之和 SHALL 等于当前规则总数

### Requirement: Technical details remain available
工作簿 SHALL 提供名为 `技术明细` 的第二 Sheet，并按规则编号保留来源、当前领域、违规类型、当前问题、示例、状态、优先级、模板、触发词、编码、工具、预期信号、notes、完整 prompt 和 precheck 字段。

#### Scenario: Technical user traces a main row
- **WHEN** 技术人员按主表规则编号查询技术明细
- **THEN** SHALL 找到唯一对应行并能看到完整运行时配置及来源说明

### Requirement: Workbook contains no patient or credential data
工作簿 MUST 只包含规则和设计元数据，不得包含患者数据、运行标识、数据库凭据、环境变量或连接信息。

#### Scenario: Final workbook is inspected
- **WHEN** 最终工作簿完成 QA
- **THEN** 其 Sheet、表头和内容来源 SHALL 限于规则 YAML、国家规则来源行及固定的分级说明

### Requirement: Workbook is verified before delivery
系统 MUST 在交付前检查规则数量、唯一规则编号、主表六列、三档字典、YAML 与工作簿分级一致性、关键来源样本和所有 Sheet 的视觉可读性。

#### Scenario: Workbook passes delivery gate
- **WHEN** 内容检查、公式错误扫描和所有 Sheet 渲染检查均完成
- **THEN** 最终 `.xlsx` SHALL 保存到本次 `outputs/<thread-id>/` 目录并作为唯一工作簿交付
