## ADDED Requirements

### Requirement: 工作台与 2C 必须共享同一公开解释投影
系统 MUST 通过同一确定性 presenter 生成 `public_explanation`，至少包含 `conclusion`、`audit_items`、`charge_facts`、`basis`、`clinical_evidence` 和 `review_needs`；工作台与 2C v1/v2/v3 对同一持久化结果 MUST 使用相同语义投影。

#### Scenario: 展示 Promise 锁定的 CLEAN
- **WHEN** 审计结果由退费净数量 Promise 锁定为 CLEAN
- **THEN** `conclusion` 用医生可读中文说明退费抵消后的净数量事实
- **AND** `charge_facts` 仅包含可由费用事实和 Promise trace 证明的结构化字段
- **AND** 工作台与 2C 的结论、事实和复核需求一致

#### Scenario: 某类事实没有确定来源
- **WHEN** 持久化结果没有足够的结构化费用、依据或临床证据
- **THEN** 对应数组为空或将缺口写入 `review_needs`
- **AND** presenter 不从 LLM 散文猜测或补造字段

### Requirement: 公开解释不得暴露内部工程术语或原始 JSON
公开结构和工作台默认视图 MUST NOT 展示内部 rule ID（如 R/RD 编号）、tool 名、gate、run/ownership ID、英文 verdict、内部 reason code 或原始 `evidence_json`；历史 `reasoning/evidence/rule_id` API 字段 MAY 为兼容保留，但新增/修改字段 MUST 遵守只加不删不改名约束。

#### Scenario: 历史 reasoning 含内部编号
- **WHEN** 旧结果 reasoning 包含“根据规则 R212”或其他内部实现词
- **THEN** 工作台默认解释不原样展示该内容
- **AND** 兼容摘要先经过公共化处理
- **AND** `public_explanation` 不含该内部编号

#### Scenario: Evidence 是 JSON 字符串
- **WHEN** 旧行仅有原始 evidence JSON
- **THEN** 工作台不得将 JSON 文本直接呈现给医院用户
- **AND** 只展示 presenter 能确定映射的公开字段，其余保留为内部追溯信息

### Requirement: 公开命中项目必须对应患者实际收费事实
公开 fee/drug hit MUST 关联当前患者的实际收费组，且该组退费净额计算后的净数量大于 0；locator、搜索词、规则标题、泛化词或“未找到”证据 MUST NOT 作为命中项目。

#### Scenario: 没有任何匹配收费行
- **WHEN** 规则查询或 evidence 只有“定位”等抽象 locator，但不存在关联患者收费行
- **THEN** 公开 `matched_items`/命中项目为空
- **AND** 有价值的不确定性仅可进入内部 evidence 或 `review_needs`

#### Scenario: 存在真实净正收费组
- **WHEN** 收费行可关联到审计目标且分组净数量大于 0
- **THEN** 公开 hit 使用实际收费名称或有 KB 依据的规范药名
- **AND** 保留可供原文/收费定位的非敏感关联

#### Scenario: 旧缓存含抽象命中
- **WHEN** 旧 `anchors_json` 缓存含无患者实际费用关联的命中
- **THEN** 渲染时重算或过滤该缓存
- **AND** 缓存不得绕过真实收费事实约束

### Requirement: 行为类别必须按 2C H/I 公开口径唯一分组
系统 MUST 以 `(behavior_code, behavior_name)` 作为工作台类别组和 chip 的业务键，并 MUST 使用仓库内“两库汇总”H/I 列验证所有 ready 规则的公开映射；同一公开键在患者视图中 MUST 只出现一个组，组内可以保留多条规则结果。

#### Scenario: 多个内部类型映射到同一公开类别
- **WHEN** 两条结果的内部 `violation_type` 不同，但公开 `(behavior_code, behavior_name)` 相同
- **THEN** 工作台只显示一个公开类别组/chip
- **AND** 两条规则卡片仍在该组内分别可追溯

#### Scenario: Ready 规则没有 H/I 映射
- **WHEN** ready 规则既无有效 H/I 映射也无合法显式例外
- **THEN** Promise validate/harness 失败
- **AND** 客户界面不得回退显示内部 `violation_type`

#### Scenario: 串换暂无正式编码
- **WHEN** 串换规则尚未获得正式 I 列编码
- **THEN** 映射必须携带稳定 exception key、`exception=true` 和来源说明
- **AND** 校验报告明确计入例外，不能把它伪装成普通 H/I 映射

### Requirement: 2C 卡片兼容性与工作台分组必须相互独立
2C MUST 继续按既有单规则卡片语义返回结果，不得因工作台按公开行为类别合组而合并不同规则的裁决；新增 `public_explanation`、Promise 摘要或公开类别字段 MUST 是 additive 的。

#### Scenario: 同类别有两条规则结果
- **WHEN** 一个患者在同一公开类别下存在两条不同规则结果
- **THEN** 工作台展示一个类别组和两张规则卡
- **AND** 2C 仍返回两条对应的规则结果记录
- **AND** 既有字段名、枚举和 v3 收费行展开语义不变

### Requirement: 旧行必须可展示但不得被新知识重算
当旧数据库行没有 `public_explanation` 或 `promise_trace_json` 时，系统 MUST 通过兼容投影展示可确定的信息，并 MUST NOT 触发规则、LLM、知识库或 Promise 的静默重评。

#### Scenario: 展示迁移前旧行
- **WHEN** 工作台或 2C 读取迁移前的旧结果
- **THEN** 请求成功且旧字段仍可用
- **AND** `public_explanation` 仅由该行已持久化事实安全派生
- **AND** 数据库原行不被回填或改写
