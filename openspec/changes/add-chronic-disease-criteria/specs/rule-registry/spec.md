## ADDED Requirements

### Requirement: CD 慢病规则命名空间

规则合同 SHALL 接受 `CD\d{2,3}` 作为慢病认定规则命名空间，并与既有 `R\d{3}`、`RD\d{2,3}` 保持互斥。首批 20 个病种 MUST 使用 `CD01` 至 `CD20`，每条规则仍是一份 `configs/rules/<rule_id>.yaml`，文件名 stem MUST 与 YAML 内 `rule_id` 完全一致；非法格式、文件名不一致或跨命名空间重复 ID MUST 在加载时显式失败。

#### Scenario: 合法 CD 规则可加载

- **WHEN** `configs/rules/CD01.yaml` 含 `rule_id: CD01` 及其必填字段
- **THEN** `load_rule` 返回 `rule_id=CD01` 的 Rule，且不会与 `R001` 或 `RD01` 冲突

#### Scenario: 文件名与 rule id 不一致被拒绝

- **WHEN** 文件 `configs/rules/CD01.yaml` 内声明 `rule_id: CD02`
- **THEN** registry 抛出包含文件名与声明 ID 的校验错误，不静默按任一 ID 注册

#### Scenario: 非法慢病 ID 被拒绝

- **WHEN** YAML 使用 `C01`、`CD1` 或 `CD-01` 作为 rule_id
- **THEN** Rule 校验失败并说明允许的 CD 格式为 `CD\d{2,3}`

### Requirement: CD 规则可发现且仅显式进入试跑

规则目录扫描、`load_all`、`javert list`、按 ID 查询/显示、显式 `--rules` 选择以及 Router index 构建 SHALL 发现 CD YAML，MUST NOT 继续依赖只能匹配 `R*.yaml` 的扫描方式而漏掉 `CD*.yaml`。首批 `CD01-CD20` SHALL 保持 `status=drafting` 并只通过慢病试跑入口或显式 ID 选择执行；默认按 ready/priority 的生产执行集和 Router 可执行集合 MUST 排除这些 drafting 规则。Router index 可记录其元数据和非激活状态，但在规则正式转为可执行状态并重建 index 前 MUST NOT 自动路由到 CD 规则。

#### Scenario: list 与显式选择发现 CD 规则

- **WHEN** `CD01.yaml` 存在且状态为 drafting，操作者运行规则列表或以 `--rules CD01` 显式启动受控试跑
- **THEN** 列表包含 CD01，显式选择可精确解析该规则，不报告“未知规则”

#### Scenario: drafting CD 不进入默认执行集

- **WHEN** 操作者运行默认 `audit-patient` 或启用 Router 但没有显式请求 CD 规则
- **THEN** `CD01-CD20` 均不进入执行集合，即使患者诊断或关键词与某慢病匹配

#### Scenario: Router index 记录但不激活 drafting CD

- **WHEN** 规则映射构建器扫描到 `status=drafting` 的 CD YAML
- **THEN** 生成 index 保留其 rule_id、status、病种和触发元数据，但默认 Router 不把它返回为可执行规则

### Requirement: 既有 R 与 RD 规则行为保持不变

增加 CD 命名空间 MUST NOT 修改既有 `R\d{3}` 或 `RD\d{2,3}` 的字段校验、状态机、加载顺序、默认状态/priority 过滤、显式选择、Router 排名或规则文件内容。现有 R/RD YAML、历史结果和 API 中的 rule_id MUST 无迁移即可读取；规则库存和 Router index 的数量 SHALL 通过实时扫描计算，不得用新增 CD 后的硬编码总数替代。

#### Scenario: 既有 R 与 RD 回归不变

- **WHEN** 在同一目录加入 CD YAML 后重新加载全部规则并执行既有 R191、RD04 回归
- **THEN** R191 与 RD04 的 Rule 字段、状态、选择路径和执行行为与加入 CD 前一致

#### Scenario: 历史 R RD 结果无需迁移

- **WHEN** store 或 API 读取 rule_id 为 R191 或 RD04 的历史审核结果
- **THEN** 结果按原 ID 正常加载和展示，不要求改名、补前缀或增加慢病字段

#### Scenario: 库存统计来自当前工作树

- **WHEN** `javert list` 或 Router 构建报告同时包含 R、RD 和 CD 规则
- **THEN** 各命名空间及状态计数由实际加载文件计算，R/RD 原有计数不被覆盖或误归入 CD
