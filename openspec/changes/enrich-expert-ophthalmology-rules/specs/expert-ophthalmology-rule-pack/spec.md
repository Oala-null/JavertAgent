## ADDED Requirements

### Requirement: Expert signals have traceable ready rules
规则库 SHALL 保留 R319–R322，并新增 R323–R326、RD38，覆盖用户文字与附图问题；MUST 仅保存去标识专家线索，标明非国家清单原始条目及未经病例测试。

#### Scenario: Authoring handoff
- **WHEN** 编写和静态校验完成
- **THEN** 九条规则状态为 ready，生成索引与 YAML 一致，不宣称 validated 或已部署。

### Requirement: Candidate names stay aligned
规则 SHALL 将已知收费别名同时纳入 Router 和预检，AB 合并名称 SHALL 作为两类检查候选；手写规则 MUST 如实标注模板来源为空。

#### Scenario: Alias in fees
- **WHEN** 费用为冲洗结膜囊、移动心电图或眼科AB型超声
- **THEN** 对应规则预检包含这些名称，不因配置遗漏直接判无目标收费。

### Requirement: Quantity decisions depend on pricing and execution
R319/R324 SHALL 核对有效当地目录、净正数量、独立执行事件和侧别，MUST 不将 ST 条数、单侧名称或收费行数直接作为数量上限。

#### Scenario: One order with three charged units
- **WHEN** 一条眼内能量精密治疗 ST 医嘱收取三个单侧单位但实际眼别/执行尚不明
- **THEN** 提示 INCONCLUSIVE 并请求执行记录，不固定推定超收一个或两个单位。

### Requirement: Evidence gaps remain reviewable
R320/R323/R326 SHALL 区分医嘱与实际执行；R322/RD38 SHALL 区分报告/用药存在与临床必要性；R321 SHALL 保留机构设备现场复核。

#### Scenario: Treatment without execution record
- **WHEN** 收费和医嘱存在但无可定位治疗记录
- **THEN** 规则要求 INCONCLUSIVE，不能仅凭医嘱 CLEAN 或仅凭缺记录 VIOLATION。

#### Scenario: Justified combination therapy
- **WHEN** 同期滴眼药联合使用有病情、眼压控制和调整依据
- **THEN** RD38 允许 CLEAN，不因同属降眼压药或药物数目认定过度用药。

### Requirement: Local mutual exclusion needs applicable source
R325 SHALL 核查眼内穿刺及球后/球旁注射的净正收费、当地服务日期有效条款与事件范围；MUST 不把外省条款、同住院或截图直接当作互斥违规证明。

#### Scenario: Unverified local pricing clause
- **WHEN** 两项收费存在但当地互斥条款或时间/侧别范围未核实
- **THEN** 输出待复核；同切口折价不自动生成比例或金额。

### Requirement: Runtime startup and fee identifiers remain valid
全部ready规则 MUST 通过运行时行为映射校验；CSV主文件和overlay中的国家/院内收费编码 SHALL 按字符串保留，不能丢弃前导零。

#### Scenario: Entire ready registry loads
- **WHEN** Runner在实际病例审计前加载规则库
- **THEN** R323及RD38使用已注册行为类别，不因缺映射阻断全部患者审计。

#### Scenario: Numeric codes in both CSV sources
- **WHEN** 主CSV和overlay费用含有全数字且以零开头的国家/院内编码
- **THEN** 审计工具及工作台命中保留完整编码，数量和金额的数值语义不变。

### Requirement: Reviewed PDF imports are complete and separated
PDF人工复核导入 MUST 按来源页区分临床文书和财务页面；费用页明细 SHALL 全量逐行导入，并与独立复核的每页行数及原单总额一致，不能把规则命中切片作为整份病例发布。不同源行同一药品允许重复，源页/行ID不得重复；数字与编码异常 MUST 拒绝。

#### Scenario: Target fee subset masquerades as complete case
- **WHEN** 原单多页费用但发布包仅包含某规则命中的三个项目
- **THEN** 因页行数或总额不一致拒绝发布。

#### Scenario: Financial page in notes
- **WHEN** 来源页为费用/结算页面，或文书含费用总账标题
- **THEN** 校验拒绝，不允许通过更改文书标题绕过。

#### Scenario: Two separate source rows for same drug
- **WHEN** 同药两行的源页/行ID不同，数量和单价不同
- **THEN** 两行均保留，不能按名称合并或误删。
