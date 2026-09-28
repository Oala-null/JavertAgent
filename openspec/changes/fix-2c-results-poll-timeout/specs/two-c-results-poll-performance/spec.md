## ADDED Requirements

### Requirement: 增量复用已完成卡片
系统 SHALL 在同一 attempt 内按 API 版本和 `run_id` 复用已生成的 v2/v3 卡片，使后续 running 轮询只投影新完成的 run，终态重复轮询不重新解析已有 run 的命中结果。

#### Scenario: running 进度增加
- **WHEN** 同一 attempt 的第一次查询已返回部分 cards，之后有新的 run 完成
- **THEN** 下一次查询只为新增 run 构建卡片，并复用此前 cards

#### Scenario: 终态重复查询
- **WHEN** 调用方对同一已完成 attempt 重复查询相同 API 版本
- **THEN** 系统从缓存组装等价响应，不再次执行已有 run 的命中解析

### Requirement: 版本与 attempt 隔离
系统 MUST 使用 `api_version + projection_scope + run_id` 作为缓存隔离边界：正常任务的 scope 为 `attempt_id`，服务重启后的历史回放为 run ID 快照的不可逆摘要；系统 SHALL 保持 v1/v2/v3 现有字段、类型、排序和卡片内容不变。

#### Scenario: v3 不污染 v2
- **WHEN** 同一 attempt 先查询 v3 再查询 v2
- **THEN** v2 matched items 不包含 v3 专属的数量、单价、科室或医生字段

#### Scenario: 重新提交产生新 attempt
- **WHEN** 已完成患者重新提交并获得新的 `attempt_id`
- **THEN** 新 attempt 不读取旧 attempt 的缓存卡片

#### Scenario: 服务重启后的历史回放
- **WHEN** 进程内 attempt 状态已丢失但 SQLite 中存在该患者的最新 run 快照
- **THEN** 首次查询重建卡片，后续查询按该快照摘要复用且不暴露摘要

### Requirement: 并发单飞与有界缓存
系统 SHALL 串行化同一 attempt 的缓存缺失构建，允许不同 attempt 并行，并 MUST 对进程内卡片缓存设置固定上限。

#### Scenario: 同一 attempt 并发轮询
- **WHEN** 两个请求同时查询同一 attempt 且卡片尚未缓存
- **THEN** 每个 run 的昂贵投影最多执行一次，两个请求返回结构一致

#### Scenario: 缓存达到上限
- **WHEN** 缓存卡片数超过配置的固定上限
- **THEN** 系统淘汰最久未使用的卡片，且后续查询仍可从持久化 run 正确重建

### Requirement: 可验证且不泄露的性能观测
系统 SHALL 记录 v2/v3 响应的缓存命中数、未命中数和构建耗时，但 MUST NOT 在该日志中记录患者号、attempt/run 标识或业务原文。

#### Scenario: 部署后重复轮询
- **WHEN** 终态结果连续查询两次
- **THEN** 第二次日志显示已有卡片命中缓存，且日志只包含匿名数量和耗时指标
