## ADDED Requirements

### Requirement: 慢病资格结果使用 QUALIFIED 语义展示

Workbench SHALL 将 `clinical_criteria_evaluation.qualification_disposition=QUALIFIED` 展示为“符合慢病认定标准（QUALIFIED）”，并 SHALL 同时显示病种、适用标准版本和 criteria revision。`QUALIFIED` 只表示现有证据满足本次配置的慢病认定条件，MUST NOT 被渲染为违规、红色违规卡、骗保结论、最终临床诊断、专家 gold 或行政审批结果。`NOT_QUALIFIED` SHALL 展示为“不符合当前认定条件”，但同样 MUST NOT 计作违规；`REVIEW_REQUIRED` SHALL 展示为“需补充材料或人工复核”。`execution_status=BLOCKED` 必须明确显示阻断原因，不能伪装成已求值结论。所有 CD 资格发现结果 MUST 从普通违规数量和违规率中排除，并可在 `Chronic_Disease` 独立批次上下文中复核。

#### Scenario: QUALIFIED 不显示为违规

- **WHEN** 某 CD 结果的根节点为 `SATISFIED`、`qualified=true` 且 `qualification_disposition=QUALIFIED`
- **THEN** Workbench 显示“符合慢病认定标准（QUALIFIED）”，不增加违规数量、不使用 VIOLATION 文案或样式，也不称其为专家 gold

#### Scenario: 不满足与待复核文案可区分

- **WHEN** 两条结果分别为 `NOT_QUALIFIED` 和 `REVIEW_REQUIRED`
- **THEN** 前者显示明确未满足的决定性条件，后者显示缺失/冲突/阻断原因和待补材料，不把两者合并成同一个“不合格”状态

#### Scenario: NOT_QUALIFIED 不增加违规统计

- **WHEN** 某 CD 结果为 `qualified=false`、`qualification_disposition=NOT_QUALIFIED`、兼容 verdict=CLEAN
- **THEN** Workbench 显示“不符合当前认定条件”，但不增加 V badge、违规数量或违规率

#### Scenario: BLOCKED 不冒充四态求值

- **WHEN** 某病种 criteria 的 `execution_status=BLOCKED`
- **THEN** 面板显示“标准待专家消歧，暂不自动判定”及阻断原因，`qualified` 保持空值，界面不把 BLOCKED 渲染为第五种叶节点状态

### Requirement: Chronic_Disease 批次在全部结果视图可发现

Workbench SHALL 支持通过 `batch_tag=Chronic_Disease` 发现本次试跑病例。因为 `QUALIFIED` 的兼容旧 verdict 为 `CLEAN`，从试跑批次入口、批次标签或病例卡进入详情时 MUST 使用并保持 `filter=all`，使只有 CLEAN 兼容裁决的资格通过病例不会被默认 `v_and_i` 过滤掉。该例外 MUST 只改变慢病批次入口的导航与可见性，不得改变既有 `v_and_i / v_only / i_only / all` 对其他批次的定义。

#### Scenario: 资格通过病例在 filter all 下可见

- **WHEN** 一个患者仅有 `batch_tag=Chronic_Disease`、`qualification_disposition=QUALIFIED`、兼容 verdict=CLEAN 的 CD 结果
- **THEN** 选择 Chronic_Disease 标签或从其病例卡进入时使用 `filter=all`，侧栏和详情均可看到该患者及慢病资格面板

#### Scenario: 默认违规过滤不被全局放宽

- **WHEN** 用户在非慢病批次继续使用 `filter=v_and_i`
- **THEN** 既有 CLEAN 结果仍按原规则隐藏，系统不因新增慢病入口而把所有 CLEAN 患者加入违规工作队列

#### Scenario: 页面跳转保持慢病可见范围

- **WHEN** 用户已按 `Chronic_Disease` 标签和 `filter=all` 打开一个试跑病例，再切换到同批次另一病例
- **THEN** 导航继续携带 `filter=all` 和批次上下文，不会跳回默认过滤后显示“无结果”

### Requirement: 逐节点证明树与证据锚点可核查

慢病资格面板 SHALL 依据持久化的 `proof_tree` 渲染根节点、逻辑/计数中间节点和叶节点；每个节点 MUST 显示 stable node id、条件摘要、运算符或阈值、四态 `SATISFIED / NOT_SATISFIED / UNKNOWN / CONFLICT`、判定理由及决定性子节点或计数边界。叶节点 SHALL 显示已归一事实、单位、测量/事件时间、来源类型和证据锚点；缺失资料 SHALL 作为明确待补项展示，MUST NOT 从自由文本 reasoning 反向猜测节点状态或证据。

#### Scenario: AND OR 与至少 N 项显示决定路径

- **WHEN** proof tree 包含 AND、OR 或 `at_least_n` 节点并完成求值
- **THEN** Workbench 逐层显示各子节点状态，并标出导致根结论的决定性子节点或实际计数与所需下界

#### Scenario: 数值与时间证据保留上下文

- **WHEN** 某叶节点依据多次检验值和时间跨度得到 `SATISFIED`
- **THEN** 面板显示归一后的数值、单位、各测量日期、要求的次数/时间窗及来源锚点，审核者可区分阈值满足与时间条件满足

#### Scenario: UNKNOWN 显示待补材料而非阴性证据

- **WHEN** 某叶节点因缺少必要检查或文书为 `UNKNOWN`
- **THEN** 面板显示具体缺失材料和 `UNKNOWN`，不得显示“未发现即不满足”或构造不存在的阴性证据

### Requirement: 证据跳转遵守精确定位与访问控制

有精确 locator 的慢病证据 SHALL 复用 Workbench 既有文书、检验、检查、手术或费用来源跳转能力，打开正确来源并定位相应记录；只有来源类型而无精确 locator 时 SHALL 显示“未能精确定位”，MUST NOT 高亮任意文本。证据详情和原始记录访问 MUST 继续受现有登录、权限、审计留痕与高敏感数据限制约束；侧栏和批次汇总不得新增患者原文或敏感事实。

#### Scenario: 精确锚点跳到对应原文

- **WHEN** 审核者点击一个含来源、日期和 locator 的叶节点证据
- **THEN** Workbench 打开正确来源标签、滚动到对应记录并高亮匹配内容，同时保留当前慢病面板上下文

#### Scenario: 无 locator 不伪造命中

- **WHEN** 叶节点证据只有来源类型而没有可验证 locator
- **THEN** Workbench 可打开相应来源标签但显示“未能精确定位”，不得随机高亮或声称已定位

#### Scenario: 无权用户不能展开高敏感证据

- **WHEN** 当前用户无权查看某高敏感病种的原始证据
- **THEN** Workbench 拒绝证据详情访问并留下审计事件，批次列表和错误响应不泄露患者原文或敏感诊断内容

### Requirement: 可空慢病结果不影响既有工作台行为

仅当 run 携带有效 `clinical_criteria_evaluation` 时，Workbench 才 SHALL 渲染慢病资格面板。历史行字段为空、普通 R/RD 结果或既有肿瘤 `eligibility_evaluation` MUST 继续按原卡片和专用面板渲染；专家“认同/驳回”、评论、命中项目和原文查看交互 MUST 保持不变。

#### Scenario: 历史结果无慢病字段仍正常显示

- **WHEN** Workbench 打开一条 `clinical_criteria_evaluation=None` 的历史审核记录
- **THEN** 页面正常渲染既有审核卡且不显示空慢病面板，不报反序列化或模板错误

#### Scenario: 肿瘤资格结果不被误作慢病结果

- **WHEN** RD04 结果仅携带既有 `eligibility_evaluation`
- **THEN** Workbench 继续显示肿瘤专用资格面板，不显示慢病 `QUALIFIED` 标题，也不改变专家审核交互

### Requirement: 不脱敏导入按原始身份显示

显式不脱敏导入 SHALL 将可溯源的原始姓名和就诊号用于侧栏、详情和原文窗口展示。内部关联键 MUST NOT 冒充原始住院号。显示字段 MUST 源自经原文引用校验的导入数据，冲突时明确待核，不修改既有审计或证据关联。普通病例无这些显示字段时 SHALL 保持既有行为。

#### Scenario: 原始显示身份与内部关联键不同

- **WHEN** 不脱敏导入提供已核验的原姓名/原就诊号，并使用独立内部关联键
- **THEN** 界面显示原始身份、接口查询继续使用原有键，患者原文与结果不被重写
