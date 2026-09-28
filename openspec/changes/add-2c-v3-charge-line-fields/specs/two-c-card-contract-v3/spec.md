## ADDED Requirements

### Requirement: 独立且向后兼容的 2C v3 端点

系统 SHALL 提供 `/api/audit/v3/submit` 与 `/api/audit/v3/results/{SYXH}`，并 MUST 保留 v1/v2 路径、字段和行为。v3 submit SHALL 接受与 v1/v2 相同的患者对象数组，并共享任务队列、running 幂等和 `attempt_id`。

#### Scenario: v3 提交复用现有任务
- **WHEN** 调用方提交合法患者数组到 `/api/audit/v3/submit`
- **THEN** 系统返回 HTTP 202、`accepted/rejected` 和 `attempt_id`
- **AND** 同一患者已有 running 任务时 MUST 复用该任务而不重复入队

#### Scenario: 旧版本保持不变
- **WHEN** v3 发布后调用 v1 或 v2 submit/results
- **THEN** 既有路径和既有响应语义 SHALL 保持不变

### Requirement: v3 返回完整规则卡片和明确进度

v3 顶层 SHALL 返回 `api_version: "3.0"`、状态、结果、attempt、进度、汇总和 `cards[]`。`status=running` 时 cards SHALL 仅表示已完成部分，调用方 MUST 继续轮询；只有 `status=done` 才表示本轮终止。

#### Scenario: running 返回增量卡片
- **WHEN** 本轮共39条规则且当前完成14条
- **THEN** v3 SHALL 返回 `status: running`
- **AND** `progress.total/completed` SHALL 分别为39和14
- **AND** `cards` SHALL 只包含当前已完成部分

#### Scenario: succeeded 返回全量卡片
- **WHEN** 本轮39条规则全部成功完成
- **THEN** v3 SHALL 返回 `status: done`、`outcome: succeeded`
- **AND** `progress.total`、`progress.completed` 和 `cards.length` SHALL 均为39

### Requirement: v3 命中项携带收费明细字段

v3 每个可关联收费行的 `matched_items[]` 元素 SHALL 在 v2 字段基础上追加 `quantity`、`unit_price`、`ordering_department_code`、`ordering_department_name`、`ordering_doctor_id`、`ordering_doctor_name`。数量和单价 SHALL 为 JSON number 或 `null`，其他新增字段 SHALL 为 string。

#### Scenario: 收费行字段齐全
- **WHEN** 命中项关联的费用行具有数量、单价、开单科室和开单医生字段
- **THEN** v3 SHALL 将六个字段从同一费用行投影到同一个 matched item
- **AND** 不得从其他名称、编码或发生时间的费用行拼接字段

#### Scenario: 可选字段缺失
- **WHEN** 命中费用行没有医生或科室信息，或数字不可解析
- **THEN** v3 SHALL 对数字字段返回 `null`
- **AND** 对文本字段返回空字符串
- **AND** SHALL 继续返回该 card 和 matched item

### Requirement: 同项目同时间按收费明细行拆分

v3 SHALL 将同一 `(code,name,occurrence_time)` 下的不同收费源行分别投影为独立 `matched_items` 元素，不得任取第一行或按医生、科室、数量、单价合并。

#### Scenario: 同时刻两条不同开单信息
- **WHEN** 一个 v2 命中项关联到同名、同编码、同发生时间的两条收费行，且医生或科室不同
- **THEN** v3 SHALL 返回两个 matched items
- **AND** 每个元素 SHALL 保留其自身收费行的六个新增字段

#### Scenario: 收费行显示字段相同
- **WHEN** 两条独立收费源行的名称、编码、时间和六个新增展示字段均相同
- **THEN** v3 SHALL 仍返回两个 matched items
- **AND** 不得因展示字段相同而静默丢失收费行

### Requirement: v3 兼容投影与命中项等长

v3 的 `hit_codes[]`、`hit_names[]`、`hit_times[]` SHALL 由展开后的 `matched_items[]` 同一次遍历生成，并 MUST 与其等长、同索引对应。没有实际费用命中的卡片 SHALL 仍然返回，且 matched items 与兼容数组均为空。

#### Scenario: 一项拆成两条收费行
- **WHEN** 一个 v2 matched item 在 v3 展开为两条收费行
- **THEN** v3 的 matched items 和三个兼容数组长度 SHALL 均增加为二
- **AND** 每个兼容数组索引 SHALL 对应同一 matched item

#### Scenario: 无命中项目的 CLEAN 卡片
- **WHEN** 一条 CLEAN 规则没有实际费用命中
- **THEN** v3 SHALL 返回该 card
- **AND** `matched_items`、`hit_codes`、`hit_names`、`hit_times` SHALL 均为空数组

### Requirement: v3 精确免鉴权范围

v3 submit 与 results SHALL 允许内网系统间免登录调用，其他未声明的 `/api/audit/v3` 路径 MUST 继续受保护。

#### Scenario: v3 免鉴权路径检查
- **WHEN** 中间件检查 `/api/audit/v3/submit` 或 `/api/audit/v3/results/{SYXH}`
- **THEN** 两条路径 SHALL 免登录
- **AND** `/api/audit/v3/run` 等其他路径 SHALL 继续受保护
