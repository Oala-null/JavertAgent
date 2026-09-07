## ADDED Requirements

### Requirement: 审核意见是唯一指控输入
系统 SHALL 使用目标病例当前激活且可追溯的同类项目映射中的违规内容和审核意见作为指控输入，并 SHALL NOT 调用 Javert 规则或重新判定违规成立与否。

#### Scenario: 读取已给出的同类意见
- **WHEN** Review20 病例存在唯一 ACTIVE/SOURCE_EXACT 同类意见映射
- **THEN** 系统按省级或市级来源契约提取有效违规描述和审核意见传给影子分析，并记录来源散列而非把它声明为该患者原始下发意见

#### Scenario: 映射不唯一或缺失
- **WHEN** 病例没有且仅有一个合格的当前映射
- **THEN** 系统在模型调用前对该病例 fail closed，且不自行挑选另一条意见

### Requirement: 只读当前病例证据
系统 SHALL 只读取目标病例当前 Primary 已关联的最多四张最小证据卡，并 SHALL 验证每张证据属于当前病例且来自相同数据域。

#### Scenario: 当前病例有证据卡
- **WHEN** 当前 Primary 关联了有效证据搜索结果
- **THEN** 系统仅以 E1–E4 标签向模型提供字段白名单内的去标识化事实

#### Scenario: 没有可用证据
- **WHEN** 当前 Primary 没有关联证据或归属校验失败
- **THEN** 系统不生成 APPEAL_DRAFT，并返回人工复核或无支持结论

### Requirement: 私有模型与最小披露
系统 MUST 在请求前验证模型端点为 loopback 或解析后全部为私有地址，MUST 使用字段白名单并限制自由文本长度。系统默认 MUST 移除已知患者标识；只有显式未脱敏开关和用户针对具体私有端点的授权同时成立时才可保留标识。

#### Scenario: 私有端点通过校验
- **WHEN** 端点 URL 合法且所有解析地址属于允许的私有范围
- **THEN** 系统允许发送仅含审核意见和最小证据的请求，并按显式模式决定是否脱敏

#### Scenario: 非私有或不可判定端点
- **WHEN** 端点为公网地址、携带 URL 凭据、解析到公网地址或无法验证
- **THEN** 系统在发送任何病例内容前终止运行

### Requirement: 逐句证据引用的结构化输出
系统 SHALL 要求模型返回 `APPEAL_DRAFT`、`MANUAL_REVIEW` 或 `NO_SUPPORT`，以及逐条 claim、原子事实编号、缺失证据和不确定性；系统 MUST 将事实编号确定性绑定到当前病例证据标签和原文短摘，最终申诉理由 SHALL 只在 APPEAL_DRAFT 时由通过引用校验的 claim 生成且不超过 1000 个字符。

#### Scenario: 形成申诉草稿
- **WHEN** 模型返回至少一个事实 claim，且每个 claim 只引用当前病例允许的 E/F 原子事实编号
- **THEN** 系统生成 APPEAL_DRAFT，并由这些 claim 确定性拼接申诉理由

#### Scenario: 引用越界或输出无效
- **WHEN** 模型引用其他病例、未知 E 标签、输出无效 JSON、空 claim 或超长内容
- **THEN** 系统拒绝该输出且不得把它当作可用草稿

#### Scenario: 证据不足
- **WHEN** 证据不能支持针对给定审核意见的申诉
- **THEN** 系统返回 MANUAL_REVIEW 或 NO_SUPPORT，并明确缺失证据，不编造政策或病例事实

### Requirement: 影子运行零业务回写
系统 MUST NOT 新增或修改 Primary、人工审核修订、artifact、证据、分析运行、工作流事件、任务或分派数据。

#### Scenario: 二十例影子运行完成
- **WHEN** 系统对当前活动轮次四类各 5 例完成或尝试分析
- **THEN** 运行前后的关键业务指针和表计数散列完全一致，否则整次运行失败

### Requirement: 私有可审计结果与安全摘要
系统 SHALL 把详细结果写入 Git 与 Web static root 之外的私有目录，目录权限 SHALL 为 0700、文件权限 SHALL 为 0600，并 SHALL 只向 stdout 输出无 PHI 摘要。

#### Scenario: 保存全部二十例结果
- **WHEN** 影子运行结束
- **THEN** 私有结果包含四类各五例的输入散列、模型元数据、结构化结论、引用映射和验证状态，而 stdout 不包含患者标识、审核意见、证据原文或申诉理由

### Requirement: 本地工作台同表展示
系统 SHALL 在 hospital-pilot 首页同一张紧凑表的最前面展示当前活动轮次全部 20 例及已验证模型结果，并 SHALL 随后继续展示完整院方原始清单；业务文案 SHALL 使用中文且不得直接展示内部案例别名、独立分区或置顶标签。

#### Scenario: 精确绑定二十例模型结果
- **WHEN** 配置了有效的 0600 私有影子结果文件
- **THEN** 系统以标准化病例 UUID 精确绑定 20/20 结果，显示模型推理与建议结论，且不修改数据库 Primary 或人工结论

#### Scenario: 紧凑查看完整推理
- **WHEN** 推理正文超过单元格两行可见范围
- **THEN** 系统保持表格缩略，并允许用户通过鼠标悬停或键盘聚焦查看完整推理文本

#### Scenario: 简化完整表关闭按钮
- **WHEN** 用户打开完整院方审核表弹窗
- **THEN** 右上角仅显示无圆环强调的普通小叉关闭按钮
