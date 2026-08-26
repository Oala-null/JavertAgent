## ADDED Requirements

### Requirement: 同次审计产生公开短标题
普通 LLM 审计的最终结构化响应 MUST 在 verdict、confidence、evidence、reasoning 之外包含 headline，且 MUST 使用同一次模型调用产生，不得新增摘要模型请求。

#### Scenario: 普通规则输出 headline
- **WHEN** 模型基于工具证据输出最终 fenced/structured verdict JSON
- **THEN** JSON 同时包含单行 headline 与完整 reasoning，runner 解析并分别保留两个字段

#### Scenario: repair 和 deadline 收敛保持字段一致
- **WHEN** runner 进入 malformed repair、native tool budget 或 deadline verdict 路径
- **THEN** 所有收敛提示和严格 Schema 仍要求 headline，字段集合不得因路径不同而漂移

### Requirement: headline 描述对象与主要风险且保持短小
headline MUST 为单行医院可读中文短标题，长度 MUST 在配置的 15–60 字范围内，并 MUST 概括审核对象与主要违规方式或关键证据缺口。headline MUST NOT 包含完整推理、处置建议或证据列表。

#### Scenario: 多项目审核压缩为一条标题
- **WHEN** 同一规则涉及三个或更多药品/收费项目
- **THEN** headline 最多点名两个对象并使用“等 N 项”聚合，同时保留共同风险方式

#### Scenario: 过长或多行标题被拒绝
- **WHEN** 模型 headline 超过上限、包含换行或形成编号列表
- **THEN** headline gate 拒绝该文本并生成确定性安全回退，审计结果仍正常落库

### Requirement: headline 与最终裁决一致
headline gate MUST 在 verdict gate、结构化求值、Promise 和技术质量门控之后运行，并以最终 verdict 为准。headline 失败 MUST NOT 修改 verdict、confidence、reasoning 或 evidence。

#### Scenario: 原始违规被降为待复核
- **WHEN** 模型输出 VIOLATION 标题但 verdict gate 将最终 verdict 降为 INCONCLUSIVE
- **THEN** 最终 headline 使用“疑似/待核查/依据不足”语义，不得保留确认违规措辞

#### Scenario: 技术质量门控改为安全结果
- **WHEN** 技术失败隔离将最终结果改为 CLEAN 或不输出患者风险
- **THEN** headline 表达“未形成可复核异常证据”，不得暗示患者违规

#### Scenario: headline 门控失败不影响主裁决
- **WHEN** headline 含禁词或与最终 verdict 冲突
- **THEN** 系统只替换 headline 并记录原因枚举，原 verdict/reasoning/evidence 保持不变且不重复调用模型

### Requirement: headline 不泄露内部机制或患者身份
模型 prompt、headline gate 和公开投影 MUST 禁止患者姓名/编号、规则代号、工具名、英文 verdict、gate/precheck、run/ownership 标识及内部错误码进入 headline。

#### Scenario: 模型回显规则和工具名
- **WHEN** headline 包含规则代号、`search_*`、英文裁决或内部 gate 术语
- **THEN** 门控拒绝原 headline，公开响应和持久化标题使用已清洗的确定性回退

#### Scenario: 模型回显患者标识
- **WHEN** headline 包含当前患者标识或已知身份字段
- **THEN** 门控拒绝该标题，日志不得记录被拒绝的原文

### Requirement: 非普通模型路径生成确定性标题
预检短路、Promise、肿瘤结构化求值、无有效 verdict 和技术故障路径 MUST 产生符合相同公开约束的确定性 headline，MUST NOT 为补标题额外调用模型或从 reasoning 反解析事实。

#### Scenario: 预检短路 CLEAN
- **WHEN** 确定性 precheck 在 LLM 前返回 CLEAN
- **THEN** AuditResult 仍包含基于规则与预检结构事实生成的 CLEAN headline，LLM 调用次数保持 0

#### Scenario: 结构化肿瘤资格结果
- **WHEN** RD04 使用 eligibility_evaluation 覆盖普通模型裁决
- **THEN** headline 依据结构化最终 disposition 和已知审核对象生成，并与 legacy verdict 投影一致

### Requirement: headline 可持久化且兼容旧行
SQLite `audit_runs` 与 SQL Server `javert_audit_runs` MUST 使用 nullable headline 列保存新结果；所有完整读写、同步和历史回放路径 MUST 保留该字段。旧行没有 headline 时 MUST 仍可读取，不得批量模型回填。

#### Scenario: 新结果 SQLite 与 SQL Server 双写
- **WHEN** 新 AuditResult 同步写入本地和 SQL Server
- **THEN** 两端保存相同 headline、完整 reasoning 和 evidence，重读后字段保持一致

#### Scenario: 读取迁移前历史行
- **WHEN** 历史行 headline 为 NULL 或数据库由幂等迁移新增该列
- **THEN** AuditResult 读取成功并使用空 headline/确定性展示回退，旧 verdict/reasoning/evidence 不变

### Requirement: 所有公开结果契约 additive 返回 headline
v1、v2、v3、SSE 与 Workbench 完整结果 MUST additive 返回顶层 headline；`public_explanation` MUST additive 包含 headline。现有 reasoning、evidence、narrative 和其他字段 MUST 不删除、不改名、不缩短。

#### Scenario: v3 card 同时返回短标题和完整推理
- **WHEN** 2C 查询已完成患者的 v3 结果
- **THEN** 每张 card 返回 headline、reasoning、evidence，headline 可用于首屏，reasoning 可用于完整展开

#### Scenario: 旧客户端忽略新增字段
- **WHEN** 客户端仍按旧 v3 DTO 读取既有字段
- **THEN** 新增 headline 不改变端点、状态、matched_items 或 reasoning/evidence 语义

### Requirement: headline 质量可观测且不记录业务原文
系统 MUST 记录 headline 总数、门控回退数和原因枚举，MUST NOT 在日志中记录 headline、reasoning、患者标识或证据原文。上线门禁 MUST 覆盖格式、禁词、裁决一致性和去标识事实忠实度。

#### Scenario: headline 门控发生回退
- **WHEN** 一条 headline 因超长、禁词或 verdict 冲突被替换
- **THEN** 指标增加对应原因计数，日志不包含原 headline 或病例信息

#### Scenario: golden 集验收
- **WHEN** 使用去标识结果集验证违规、待复核、合规、多项目和 gate 降级场景
- **THEN** 长度/单行/禁词/最终 verdict 一致通过率为 100%，且人工抽检不发现新增无证据事实
