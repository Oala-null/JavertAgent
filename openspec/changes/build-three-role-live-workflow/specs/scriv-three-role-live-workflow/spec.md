## ADDED Requirements

### Requirement: 一级与二级批量重新分配
系统 SHALL 允许 L1 对 1–20 个未产生人工审核版本的病例选择新审核组，允许 L2 对 1–20 个本组未产生人工审核版本的病例选择本组新 L3，并 MUST 通过预览快照和单事务提交保证整批一致。

#### Scenario: 一级直接重新分组
- **WHEN** L1 选择处于 L2_ASSIGN、L3_REVIEW 或 L3_RETURNED 且尚无 review revision 的病例并确认新团队
- **THEN** 系统将整批置为 L2_ASSIGN、替换团队、清除旧 L2/L3，并保留模型结果和全部历史

#### Scenario: 二级直接重新分人
- **WHEN** L2 选择本组处于 L3_REVIEW 或 L3_RETURNED 且尚无 review revision 的病例并确认本组新 L3
- **THEN** 系统保持整批为 L3_REVIEW、替换当前 L3，且不得改变团队或访问其他组

#### Scenario: 冲突或已审核病例
- **WHEN** 任一病例已产生审核版本、离开允许阶段、行版本变化或不属于操作者范围
- **THEN** 系统拒绝整个批次且不得产生部分更新

### Requirement: 重新分配历史完整留存
系统 MUST 为每次重新分配保存批量主记录、逐例快照、逐例流程事件和访问审计，且 MUST NOT 删除或修改原分配和审核历史。

#### Scenario: 成功重新分配
- **WHEN** 一次重新分配提交成功
- **THEN** `workflow_bulk_action`、`workflow_bulk_item`、`workflow_event` 和 `access_audit` 可通过 request/correlation/round 追溯同一次操作

### Requirement: 三屏自动同步与脏表单保护
系统 SHALL 在页面可见时于2秒内发现活动轮次流程版本变化，按当前角色权限刷新数据；若详情表单已修改，系统 MUST 保留本地文本、标记任务过期并禁用提交。

#### Scenario: 分派跨屏同步
- **WHEN** L1 或 L2 完成分派或重新分配
- **THEN** 旧目标在2秒内失去任务，新目标在2秒内看到任务，且页面显示更新提示

#### Scenario: 脏表单遇到改派
- **WHEN** 用户正在编辑的病例被其他账号改派
- **THEN** 系统不覆盖未保存内容，但禁止按旧行版本提交

### Requirement: 24账号增量生命周期
系统 SHALL 在保留原21人的前提下创建 Parker2/L2/TEAM-A 与 Parker3/L3/TEAM-A；系统优先使用 Parker 私有演示密码分别哈希，明文不存在时 SHALL 只在数据库事务内复用 Parker 现有密码哈希且不得输出，并将账号库存和授权门禁调整为24人。

#### Scenario: 首次增量创建
- **WHEN** 当前22人库存健康且 Parker2/Parker3 均不存在
- **THEN** 单事务创建二人、授权 PRIVATE_FIXTURE、撤销可能的旧会话并写入 operator audit，stdout 不含身份或密码

#### Scenario: 重复运行
- **WHEN** Parker2/Parker3 已以正确角色、团队和授权存在
- **THEN** operator 返回 reused 且不创建重复账号

### Requirement: 活动轮次驱动授权续期
系统 SHALL 对当前活动演示轮次下全部24个账号执行维护：授权不足72小时时延长至当前时间后30天；没有活动轮次时撤销演示授权和有效会话。

#### Scenario: 临近到期自动续期
- **WHEN** ACTIVE round 存在且任一演示授权剩余不足72小时
- **THEN** 系统原子延长全部24个 grant 并记录一次安全审计

#### Scenario: 轮次关闭
- **WHEN** 不存在 ACTIVE round
- **THEN** 系统不再续期并撤销全部演示 grant/session，同时保留账号与历史行

### Requirement: 真实人员完整展示
系统 SHALL 在角色允许的页面使用数据库真实 display name 展示全部 L2/L3，包括零任务人员，并 SHALL NOT 用模拟审核员名称替换原21人。

#### Scenario: 新增账号后查看组内人员
- **WHEN** L1 查看分组图或 L2 查看三级目标
- **THEN** 页面分别显示全部3名 L2、17名 L3，并保持 TEAM-A/TEAM-B 角色范围

### Requirement: 三级查看完整病案信息
系统 SHALL 为所有 L3 本人范围内的待审核、退回和已提交病例提供“查看完整病案信息”入口，展示费用、病历文书、诊断、手术、检验和检查；已提交病例 SHALL 为只读。

#### Scenario: 三级打开本人病例
- **WHEN** L3 从本人任务或已提交列表点击“查看完整病案信息”
- **THEN** 系统打开现有全屏病例工作区并仅返回该 L3 当前负责的病例资料

#### Scenario: 越权病例
- **WHEN** L3 请求其他审核员或其他团队病例
- **THEN** 系统返回不可枚举的拒绝响应并写访问审计

### Requirement: 六域手动查询
系统 SHALL 允许 L3 在当前病例每个资料域使用关键词、日期范围、项目编码和项目名称进行服务端查询和分页；查询 MUST NOT 调用 LLM 或自动生成审核证据。

#### Scenario: 有结果的手动查询
- **WHEN** L3 提交合法查询正文
- **THEN** 系统返回当前病例、当前域内匹配结果，审计仅保存条件 SHA-256 和结果数量

#### Scenario: 查询结果只读
- **WHEN** L3 查看查询结果
- **THEN** 页面不得提供修改原文或直接加入审核证据的操作

### Requirement: 可读业务视图
系统 SHALL 提供病例当前态、病例操作时间线和账号范围三张只读视图，且视图 SHALL 只投影既有真相表。

#### Scenario: DBA 排查三级流程
- **WHEN** DBA 查询三张业务视图
- **THEN** 可分别定位当前责任人、完整操作历史及账号授权期限，无需直接拼接底层表

### Requirement: 当前账号安全退出
系统 SHALL 为所有已登录角色提供“退出登录”，撤销当前服务端会话、删除会话 Cookie，并回到登录页；
系统 MUST NOT 在浏览器保存账号密码或提供绕过登录的角色切换。

#### Scenario: 同一浏览器切换账号
- **WHEN** 用户点击“退出登录”且服务端注销成功
- **THEN** 页面刷新并显示登录表单，上一账号的页面数据不再保留，用户可输入另一账号重新登录

#### Scenario: 其他标签页切换账号
- **WHEN** 同一浏览器的服务端会话已切换为另一账号
- **THEN** 旧标签页在2秒内发现actor或role变化并刷新到新账号工作台，不得继续用旧角色界面发起请求

### Requirement: 正式中文工作台与证据卡
系统 SHALL 在任务页只显示中文业务名称，关键证据 SHALL 使用可展开的中文报告卡展示最小事实；关闭按钮
SHALL 保持正圆。用户可见界面 MUST NOT 使用“演示”“模拟”“未实时运行”等弱化产品属性的措辞。
所有人员展示 SHALL 同时包含数据库真实姓名与工号，包括当前账号、组长、三级审核员和零任务人员。

#### Scenario: 查看关键证据
- **WHEN** 审核员展开一张关键证据卡
- **THEN** 页面以中文字段名显示该证据的最小事实、发生时间和核验状态，并允许有权限的审核员选择该证据

#### Scenario: 生成证据图片
- **WHEN** 系统为最终证据包渲染HIS、RIS或LIS图片
- **THEN** 图片采用院内参考图的灰蓝表头、密集表格和报告分区，并保留非院方原始截图的永久标记

### Requirement: 三角色逐列筛选与正确字段口径
系统 SHALL 为L1、L2、L3首屏的每个业务列提供Excel式筛选和升降序；所有查询 MUST 在现有角色范围内由
服务端执行。费用日期与违规金额 SHALL 独立展示，违规金额的展示、筛选和排序 MUST 使用同一数据表达式。

#### Scenario: 组长筛选待办
- **WHEN** L2在任一列选择文本、日期、金额、阶段、人员或结论条件
- **THEN** 系统只返回本组范围内满足所有列条件的病例，并在翻页后保留筛选状态

#### Scenario: 非法列或越权筛选
- **WHEN** 客户端提交未知排序列、非法方向或超出角色权限的患者/人员条件
- **THEN** 系统返回安全422且不得把客户端字段拼入SQL或扩大数据范围

### Requirement: 三级申诉提交可操作
系统 SHALL 在L3提交前明确校验申诉理由、人工核验证据和偏离说明，并 SHALL 使用与L1相同的Review20 Qwen
结果展示推理、建议结论与申诉草稿；系统 MUST NOT 用固定模板文本替代真实模型结果。

#### Scenario: 缺少必填审核依据
- **WHEN** L3选择申诉但缺少后端规则要求的理由或人工核验证据
- **THEN** 页面显示具体中文提示且不发送无效请求

#### Scenario: 合法三级结论
- **WHEN** L3补齐理由、所需证据和偏离说明并提交
- **THEN** 系统追加审核版本、进入L2_REVIEW并从三级待办中移除

### Requirement: 全屏院内系统证据画面
系统 SHALL 保留关键证据缩略卡，并允许点击打开全屏HIS/RIS/LIS证据PNG；该PNG SHALL 与最终证据包共用
服务端模板，并用黄色框标示关键日期、时间、金额、结果或结论值。

#### Scenario: 打开本人证据原图
- **WHEN** 有权限的审核员从证据卡点击查看系统原图
- **THEN** 系统返回no-store的PNG并在全屏弹窗展示，且不得调用LLM或修改业务数据

### Requirement: 标签页级独立会话
系统 SHALL 允许同一浏览器的多个标签页分别登录不同账号；刷新任一标签页 SHALL 回到登录页，不得自动采用
其他标签页账号。会话token MUST 继续保存在HttpOnly cookie且不得暴露给JavaScript。

#### Scenario: 三标签页三账号
- **WHEN** 三个标签页分别登录L1、L2、L3并继续请求
- **THEN** 每个标签页只解析自身tab绑定cookie，任一标签页登录、退出或刷新不应把其他标签页切成同一账号

### Requirement: 中文系统原图与可读任务表
系统 SHALL 将全屏HIS/RIS/LIS证据画面的固定技术字段显示为中文业务列，不改变源值、黄色关键值框或永久来源标记。
三角色任务表 SHALL 为疑点摘要保留可读宽度，中文不得逐字断行；超长摘要最多显示两行并可悬停查看全文。

#### Scenario: 查看费用系统原图
- **WHEN** 审核员打开费用类证据的系统原图
- **THEN** 金额、发生时间、医保项目编码、院内项目编码和项目名称均以中文列名显示，关键金额与时间仍被黄色框标出

#### Scenario: 查看三角色任务表
- **WHEN** L1、L2或L3打开当前任务列表
- **THEN** 疑点列以横向中文摘要展示，不因是否存在复选框而被压成一字一行

### Requirement: 一级分派单一入口
Hospital Pilot的L1导航 SHALL 只保留一个名为“分派二级”的流程分派入口，并在该页面同时展示20例流程、实时指标、
批量分派/重新分配操作和当前任务表；L2与L3导航 SHALL 保持各自职责入口不变。

#### Scenario: 一级进入分派二级
- **WHEN** L1点击“分派二级”
- **THEN** 用户无需在两个标签页之间切换即可查看全流程并完成批量分组

### Requirement: Parker演示轮次一键回溯
系统 SHALL 仅允许Hospital Pilot中的Parker/L1将当前固定20例轮次一键恢复到待一级分派。系统 MUST 在单事务中保存
旧轮次逐例最终投影、追加20条回溯流程事件和一次安全审计、关闭旧轮次并新建ACTIVE轮次；旧分派、审核版本、证据、
对齐和产物 MUST NOT 删除或改写。普通重新分配与逐级撤回的生产语义 SHALL 保持不变。

#### Scenario: 三个Parker账号完成操作后回溯
- **WHEN** 当前轮次只包含Parker、Parker2、Parker3执行的分派或审核操作，且Parker/L1确认回溯当前workflow version
- **THEN** 20例全部进入L1_ASSIGN并清空当前团队、L2、L3和审核版本指针，旧轮次可完整重放，新轮次可重新分派

#### Scenario: 回溯后再次审核
- **WHEN** 新轮次对同一病例再次提交三级审核
- **THEN** 新revision_no从该病例全历史最大值继续递增，且新轮次首条revision的supersedes_revision_id为空

#### Scenario: 越权或并发变化
- **WHEN** 非Parker/L1调用、轮次含非Parker人工操作、成员或来源漂移、存在运行中任务，或确认后workflow version变化
- **THEN** 系统整笔拒绝且不得产生部分回溯、部分事件或新轮次

#### Scenario: 交互式回溯及时完成
- **WHEN** Parker/L1通过页面提交已校验的当前20例回溯
- **THEN** HTTP事务只读取并锁定固定私有轮次数据，不同步扫描PRODUCTION全量对象；POST完成后按钮立即恢复，版本与列表在后台同步

#### Scenario: 运维重置继续校验production隔离
- **WHEN** 运维人员通过无HTTP request id的reset operator关闭并重建轮次
- **THEN** 系统仍在操作前后计算完整production摘要，任何漂移使整个事务回滚
