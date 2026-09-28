## ADDED Requirements

### Requirement: 原文接口必须支持兼容全量响应与按页签懒加载
`GET /api/patient/{pid}/raw` 无 `tab` 参数时 MUST 保持既有全量字段和兼容语义；带 `tab=notes|fees|labs` 时 MUST 只查询该页签所需数据与最小患者存在性信息。

#### Scenario: 旧客户端请求全量原文
- **WHEN** 客户端不传 `tab` 参数请求 raw 接口
- **THEN** 响应字段、状态语义和 SQL 异常降级行为保持兼容

#### Scenario: 从收费命中跳转原文
- **WHEN** 用户从收费命中打开原文并请求 `tab=fees`
- **THEN** 服务只加载收费页签必需数据和最小元信息
- **AND** 不等待 notes、labs 或其他无关源查询完成

### Requirement: 锚点跳转必须先加载目标页签再定位
工作台 MUST 根据 anchor 类型选择并加载目标 tab；只有目标 tab 成功返回后才执行行定位或高亮，切换到其他 tab 时才按需取数。

#### Scenario: 文书锚点跳转
- **WHEN** 用户点击指向病程记录的锚点
- **THEN** 前端先请求 `tab=notes`
- **AND** 响应成功后打开对应文书并定位锚点
- **AND** 不因收费或检验源变慢阻塞此次跳转

#### Scenario: 锚点已不存在
- **WHEN** 目标 tab 加载成功但对应行或文书锚点不存在
- **THEN** 前端显示“原文已更新或定位失效”的可理解提示
- **AND** 不把该情况显示为 HTTP 502

### Requirement: 每个页签查询必须有早于代理预算的受控截止时间
服务 MUST 为每个 raw tab 数据源设置受控 deadline，且该 deadline MUST 早于已确认的外层代理超时预算；已确认患者存在但页签源超时或暂不可用时，tab 请求 MUST 返回可重试的结构化业务错误，而不是让连接悬挂成无说明 502。

#### Scenario: 页签数据源超时
- **WHEN** 患者存在且目标 tab 的 hub 查询超过应用 deadline
- **THEN** 应用在代理预算前返回可重试响应（HTTP 503）
- **AND** 响应含稳定业务码、中文提示和可重试标记
- **AND** 不包含 SQL、凭据、患者号或原文内容

#### Scenario: 真实患者双 miss
- **WHEN** SQLite/结果库和 hub 均无法确认患者存在
- **THEN** 接口保持既有 HTTP 404 语义
- **AND** 不把真实 miss 伪装成可重试源故障

### Requirement: Raw 缓存必须按患者页签细化且失败可重试
Hub raw source MUST 按 per-patient/per-tab 缓存成功结果；失败、超时和部分响应 MUST NOT 进入成功缓存，后续用户重试 MUST 能重新查询该页签。

#### Scenario: 首次 fees 查询成功
- **WHEN** 同一患者首次 `tab=fees` 查询成功
- **THEN** 后续相同请求可命中 fees 缓存
- **AND** notes/labs 缓存状态不受影响

#### Scenario: 首次查询超时后重试
- **WHEN** 首次 `tab=notes` 超时而第二次源已恢复
- **THEN** 第二次请求重新访问 notes 源并可成功
- **AND** 第一次失败不会形成持久失败缓存

### Requirement: 原文链路必须提供无 PHI 的阶段诊断
服务 MUST 记录 `source`、`tab`、`outcome`、`duration_bucket`、`cache_hit` 和稳定 `error_code`，但 MUST NOT 记录 patient_id、SQL 参数、费用/文书内容、凭据或完整响应。

#### Scenario: 应用内查询失败
- **WHEN** 某个页签查询抛出 SQL、连接或 deadline 异常
- **THEN** 日志可区分失败阶段与错误类别
- **AND** 日志足以与代理访问日志按时间窗口关联
- **AND** 不包含 PHI 或秘密

### Requirement: 502 诊断必须区分应用与代理路径
部署验收 MUST 分别执行服务主机回环直连、客户端显式 `--noproxy` 请求和浏览器路径，并结合 systemd/应用/代理日志判定故障层；系统 MUST NOT 在未定位前仅通过增大 SQL 或代理 timeout 宣称修复。

#### Scenario: 直连成功而浏览器失败
- **WHEN** 回环和 `--noproxy` 请求成功，但浏览器路径仍返回 502
- **THEN** 验收结论将问题归入客户端/代理路径继续调查
- **AND** 不把应用查询标记为已证实根因

#### Scenario: 直连也失败
- **WHEN** 回环直连在受控 deadline 内也失败
- **THEN** 依据阶段诊断进入应用进程、线程/连接池或 hub SQL 分支
- **AND** 修复后重复同一组三段冒烟验证
