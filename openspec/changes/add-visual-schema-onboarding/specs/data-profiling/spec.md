## ADDED Requirements

### Requirement: 按需逐列剖析

系统 SHALL 提供按需(用户点哪列算哪列)列剖析,MUST NOT 在文件导入时自动全列硬算。剖析 SHALL 对大文件先采样给近似结果、并提供"全量精确扫描"选项;计算 MUST 流式(chunk),不全量载入内存。剖析输出按列类型 MUST 含:数值列范围/计数/空值率;键列唯一值数/重复度;日期列见日期探测要求。

#### Scenario: 点列才算

- **WHEN** 用户上传 391MB 文件但未点任何列的剖析
- **THEN** 系统 MUST NOT 触发全列统计计算

#### Scenario: 采样近似 + 全量精确

- **WHEN** 用户点某列剖析
- **THEN** 系统 SHALL 先用采样秒出近似范围,并提供"全量精确扫描"按钮按需触发精确结果

#### Scenario: 键列可作连接键判断

- **WHEN** 用户剖析某候选连接键列
- **THEN** 系统 MUST 报告唯一值数与重复度,供判断其能否作连接键

### Requirement: 日期列逐列格式探测

系统 SHALL 对每个日期列独立探测格式,MUST NOT 设全局 dayfirst。探测 MUST 采样整列(非仅头几行)、按 shape 聚类、寻找 day>12 样本判定 dayfirst;解析 MUST 容忍小数秒精度浮动(.9~.999999)、缺秒、纯日期等变体;MUST 识别"纯时间无日期"列并标记其不可做时间窗口分析;少数派异常 shape(如表头泄漏值)MUST 标红为脏数据。归一结果统一存 ISO。

#### Scenario: 同文件内不同列不同格式

- **WHEN** 同一来源费用列为 `24/12/2025`(日在前)、文书列为 `2026-01-05`(ISO)
- **THEN** 系统 MUST 分别判定两列格式正确解析,MUST NOT 用一个全局 dayfirst 把其中一列解析错

#### Scenario: 日==月样本不误判

- **WHEN** 日期列头部样本为 `11/11/2025`(无法区分 D/M 与 M/D)
- **THEN** 系统 MUST 继续扫描整列直到出现 day>12 的样本以判定 dayfirst,而非据头几行武断判定

#### Scenario: 纯时间无日期列

- **WHEN** 某列值形如 `09:28:05.794134`(无日期分量)
- **THEN** 系统 MUST 标记该列"无日期分量",并禁止用它做时间窗口分析

#### Scenario: 表头泄漏标红

- **WHEN** 某日期列含极少数无法解析的值(如字面 `"reportDate"`)
- **THEN** 系统 MUST 把这些异常行标红为脏数据并报告解析成功率

### Requirement: 连接预检

系统 SHALL 提供连接预检(纯计算,不调 LLM):映射好连接键后计算键交集覆盖率,并对时间敏感连接计算时间窗口重叠(揭露化验时间落在住院期外等错位)。预检判据 MUST 为"两个 getter 都能解析得出同一 patient_id"而非简单键集合相等。结论 MUST 给三态:🟢可靠 / 🟡部分匹配可继续 / 🔴键几乎不交挡住。

#### Scenario: 键交集覆盖率

- **WHEN** 对 5 个患者键做化验表连接预检,4 个命中
- **THEN** 系统 MUST 报告覆盖 80% 并列出未命中键,给🟡结论

#### Scenario: 判据非集合相等

- **WHEN** 文书表键落复合键形态、与费用裸号集合"看似不等",但 `get_notes`/`get_fees` 实际都能解析出同一 patient_id
- **THEN** 预检 MUST 以两 getter 实际解析非空为通过判据,MUST NOT 因键集合形态不同而误判失败

#### Scenario: 时间窗口错位告警

- **WHEN** 病案首页住院期为某窗口,而化验报告时间大部分落在窗口外
- **THEN** 系统 MUST 报告"X% 记录落在住院期外"并给🟡,提示可加日期窗口过滤

### Requirement: 剖析/预检逻辑 GUI 与 CLI 共用

列剖析与连接预检逻辑 SHALL 实现为可复用模块,`etl_import` 校验阶段与 `/onboarding` GUI MUST 调用同一套实现。

#### Scenario: CLI 与 GUI 同源

- **WHEN** 通过 CLI `etl_import` 或通过 GUI 触发对同一数据的连接预检
- **THEN** 两条路径 MUST 产出一致的覆盖率与三态结论
