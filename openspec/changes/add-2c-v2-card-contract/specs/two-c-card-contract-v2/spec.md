## ADDED Requirements

### Requirement: 独立且向后兼容的 2C v2 端点

系统 SHALL 提供 `/api/audit/v2/submit` 与 `/api/audit/v2/results/{SYXH}`，并 MUST 保留既有 v1 路径、字段和行为。v2 submit SHALL 接受与 v1 相同的患者对象数组，并与 v1 共享任务幂等和 `attempt_id`。

#### Scenario: v2 提交采用既有入参

- **WHEN** 调用方 POST `[{"SYXH":"J66252","YLZZJGDM":"H31010600042"}]` 到 `/api/audit/v2/submit`
- **THEN** 系统返回 HTTP 202、`accepted/rejected` 和本次 `attempt_id`
- **AND** 同一患者已有 running 任务时 MUST 复用该任务，不得重复入队

#### Scenario: v1 保持可用

- **WHEN** v2 发布后调用既有 `/api/audit/submit` 与 `/api/audit/results/{SYXH}`
- **THEN** v1 的路径和既有字段 SHALL 保持可用

### Requirement: v2 返回可还原 Web 的完整卡片

v2 结果 SHALL 在顶层返回版本、患者、状态、进度、汇总和 `cards[]`。每张卡片 MUST 包含行为大类 code/title、规则问题与元数据、三态裁决、置信度、推理、证据、命中项目、可空肿瘤资格对象及完成时间。

#### Scenario: 多条规则形成多张卡片

- **WHEN** 一个患者已有多条完成的规则结果
- **THEN** v2 SHALL 为每条完成结果返回一张 card
- **AND** card SHALL 按稳定规则顺序返回

#### Scenario: CLEAN 结果也返回

- **WHEN** 某条规则的 verdict 为 `CLEAN`
- **THEN** v2 SHALL 返回该 card 并计入 `summary.clean`
- **AND** 不得因为 verdict 为 CLEAN 而删除其可解析命中药品、证据或肿瘤资格条件

### Requirement: 命中项目 code/name/time 严格关联

v2 SHALL 以 `matched_items[]` 表达命中项目，每项至少包含 `code`、`name` 和 `occurrence_time`。兼容投影 `hit_codes[]`、`hit_names[]`、`hit_times[]` MUST 等长，且同一索引 MUST 来自同一个 `matched_items` 元素。

#### Scenario: 一个卡片有多个命中项目

- **WHEN** 一条审计结果解析出三个费用或药品命中
- **THEN** `matched_items` SHALL 同时返回三个项目
- **AND** 三个兼容数组长度 SHALL 都等于三，索引一一对应

#### Scenario: 同一项目发生在多个日期

- **WHEN** 同一 code/name 对应患者费用明细中的多个不同 `fee_ocur_time`
- **THEN** v2 SHALL 按不同发生时间返回多个 `matched_items` 元素
- **AND** SHALL 按 `(code,name,occurrence_time)` 稳定去重

#### Scenario: 无法取得编码或时间

- **WHEN** evidence 能确定命中名称但无法关联患者费用行
- **THEN** v2 SHALL 保留该命中名称
- **AND** 缺失的 `code` 或 `occurrence_time` SHALL 使用空字符串，不得丢弃整个命中

### Requirement: RD04 肿瘤限定条件完整返回

v2 SHALL 返回持久化的完整 `eligibility_evaluation`，包括总体处置、资格状态、候选药/条件评价、scope evaluations、criterion assessments、proof tree、方案/来源版本、生效期和文书建议；历史行没有该字段时 SHALL 返回 `null`。

#### Scenario: 新 RD04 结构化结果

- **WHEN** RD04 结果包含结构化肿瘤医保资格数据
- **THEN** v2 card SHALL 原样保留全部结构化字段
- **AND** 2C 能据此展示癌症药医保限定条件及逐项状态

#### Scenario: 历史 RD04 无结构化字段

- **WHEN** 历史 RD04 行的 `eligibility_json` 为空
- **THEN** v2 SHALL 返回 `eligibility_evaluation: null`
- **AND** 不得使用当前知识库静默重算历史结果

### Requirement: v2 不输出占位描述

v2 响应 SHALL 清理“暂未描述”占位文本；清理后无内容的值 SHALL 使用与字段类型一致的空值。

#### Scenario: 文本字段含暂未描述

- **WHEN** 任一待输出展示字符串包含“暂未描述”
- **THEN** v2 最终 JSON 中 SHALL 不包含该占位短语
- **AND** 原始持久化数据与 v1 输出 SHALL 不被修改

### Requirement: v2 精确免鉴权范围

v2 的 submit 与 results SHALL 与 v1 一样允许内网系统间免登录调用，但其他 `/api/audit` 路径 MUST 继续受保护。

#### Scenario: 免鉴权路径检查

- **WHEN** 中间件检查 `/api/audit/v2/submit` 或 `/api/audit/v2/results/J66252`
- **THEN** 两条 v2 路径 SHALL 免登录
- **AND** `/api/audit/v2/run`、`/api/audit/run` 等其他路径 SHALL 继续受保护
