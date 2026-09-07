## Context

Hospital Pilot 当前 20 例全部处于 `L2_ASSIGN`，尚无人工审核版本。现有后端支持一级分组、二级分人和单例逐级 recall，但一级不能跨未审核下游阶段重新分组，前端也没有重新分配入口。三个浏览器只刷新发起操作的一端。现有三级病例工作区已能按需读取费用、文书、诊断、手术、检验和检查，但仅支持分页浏览，没有服务端手动查询，也不允许三级在“已提交”页重新打开只读病例。

当前有效演示身份为原21人加 Parker，共22个授权：L1/L2/L3 为 4/2/16。Parker2、Parker3 尚不存在。业务当前态、批量分派、逐例事件、审核版本和访问审计均已落在 `Scriv.scriv`；主要问题是缺少重新分配动作、轻量同步信号和面向 DBA 的可读视图。

## Goals / Non-Goals

**Goals:**

- 一级和二级对 1–20 个尚未人工审核的病例直接选择新目标并原子重新分配。
- 三个独立会话在 1–2 秒内看到角色范围内的任务变化，且不覆盖脏表单。
- 新增 Parker2/L2/TEAM-A 与 Parker3/L3/TEAM-A，保留原21人真实姓名和零任务账号。
- 全部24个演示账号跟随活动轮次滚动续期，并在轮次关闭后撤权、撤销会话。
- 所有三级账号可从本人任务打开“查看完整病案信息”，在六类资料中手动查询。
- 为当前态、操作时间线和账号范围提供三张只读业务视图。
- 让系统原图字段、三角色任务表和一级分派入口达到可直接演示的中文可读性。
- 允许Parker一级账号一键关闭当前演示轮次并恢复20例重新分派，同时完整保留旧轮次历史。

**Non-Goals:**

- 不允许重新分配已产生人工审核版本的病例，不删除或重写任何历史。
- 不给二级跨组分人，不给三级查看他人病例。
- 病案查询不调用 LLM、不创建新证据、不写浏览器存储。
- 不物理重命名或移动既有表，不部署 62。
- 不把普通重新分配放宽到已审核病例，不删除、覆盖或伪造旧审核版本，也不给真实人员账号开放演示轮次回溯。

## Decisions

1. **直接重新分配，历史只追加。** 新增 `L1_REASSIGN_TEAM` 和 `L2_REASSIGN_L3` 批量动作。一级可处理 `L2_ASSIGN/L3_REVIEW/L3_RETURNED` 且 `current_review_revision_id IS NULL` 的病例，提交后进入 `L2_ASSIGN` 并清除旧 L2/L3；二级仅处理本组 `L3_REVIEW/L3_RETURNED` 且未审核的病例，提交后仍为 `L3_REVIEW` 并替换 L3。每批写一条 `workflow_bulk_action`、逐例 `workflow_bulk_item` 和 `workflow_event`，再写 `access_audit`；不创建第二套通用历史表。

2. **复用快照与行版本门禁。** preview 返回选择散列、数量、金额及原目标分布；commit 在同一事务 `UPDLOCK/HOLDLOCK` 重读并比较散列、数量和行版本。任一病例变化整批回滚，原因必填且不超过1000字。

3. **轻量版本轮询而非 SSE。** 新只读端点返回活动轮次最新 `workflow_event.id` 形成的版本。页面可见每1秒、隐藏每10秒轮询；版本未变不读取业务列表。变化时按当前角色刷新。正在编辑的详情保留本地文字、标记过期并禁用提交，数据库 rowversion 仍是最终保护。

4. **账号采用增量 operator。** 不重跑固定21人+Parker的初始 provision。新增幂等 operator 只在现有22人健康、Parker2/Parker3 均缺失时创建二人；若提供0600私有 `parkerPassword` 则分别 hash，若明文已不存在则在数据库事务内复用 Parker 现有 Argon2 hash，不导出明文或 hash。二人绑定 TEAM-A 并授予 `PRIVATE_FIXTURE`。命令先写 0600 备份，stdout 只输出数量和散列。扩展库存为 L1/L2/L3=4/3/17、TEAM-A L2/L3=2/9、TEAM-B=1/8、授权24。

5. **续期由独立维护命令驱动。** `pilot-account-maintenance --once` 由后续日程每天执行；活动轮次存在且授权不足72小时则把全部24人延至 `now+30d`。无活动轮次则撤销全部有效会话并移除演示 realm 授权。每次真实变化写一条 access audit；普通健康检查不写库。

6. **保留真实人员展示。** 所有人员标签继续来自 `user_account.display_name`。L1 组卡显示全部二级和三级；L2 目标列表只显示本组全部 L3，包括零任务账号。Parker2/Parker3 是增量，不替换原21人。

7. **病案查询扩展现有六域接口。** 保留旧 GET 分页兼容，新增 POST query body：domain、page、pageSize、keyword、dateFrom/dateTo、itemCode、itemName、matchMode。后端先按现有 Hub 契约读取当前患者和域，再在服务端过滤、分页；20例规模下不新增搜索索引。查询条件不进 URL，审计只保存 canonical body SHA-256 和结果数。

8. **三级入口与权限。** L3 在本人待审核、退回、已提交页均显示“查看完整病案信息”。待审核/退回可审核，已提交只读。授权仍复用 `PilotScopeConstraint + ActorContext`，所有 L3 都具备相同功能但只能读 `current_l3_user_id` 为自己的病例。

9. **业务视图不替代真相表。** additive migration 创建 `v_case_current_state`、`v_case_operation_timeline`、`v_account_scope`，只做可读投影；写入仍只走原表和服务。

10. **复用现有注销契约。** 顶栏按钮调用已有 CSRF 保护的 `POST /api/scriv/v2/auth/logout`；成功后整页刷新，
    由服务端已删除的 HttpOnly cookie 自然回到登录页，同时清空前端内存中的上一账号数据。不增加客户端账号切换器或凭据存储。

11. **同步信号携带当前角色身份。** `workflow-version`在原版本号之外只增加当前actor public id和role；任一标签页发现
    服务端会话与页面会话不一致即整页刷新。所有跨事务传播的领域异常必须可写traceback，避免回滚过程把403/409遮成500。

12. **院内工作台与导出共用视觉口径。** 任务导航只显示中文业务名称，不显示case UUID；关键证据采用可展开的
    报告卡、中文字段名和灰蓝表头。导出HIS/RIS/LIS模板保留固定尺寸和永久“系统生成、非院方原始截图”标记，
    但视觉层级对齐指定院内参考图，不再使用“演示/模拟”作为用户界面文案。

13. **筛选使用固定业务列契约。** 三角色表头复用轻量原生筛选浮层；文本、日期范围、违规金额范围和枚举多选
    均由服务端在既有role/realm/pilot scope之后参数化执行。排序仅使用静态SQL映射和asc/desc枚举；操作列不筛选。
    违规金额展示、筛选和排序统一使用同一表达式，费用日期独立成列。

14. **AI建议同源且审核闸不放松。** Review20 shadow overlay按角色授权范围投影到list与detail，一级和三级读取
    同一推理、处置和申诉草稿。三级表单在发请求前校验理由、人工核验证据和偏离说明；不自动勾选证据。

15. **全屏证据预览与最终导出同源。** 关键证据弹窗请求服务端按该病例和证据生成的PNG；服务端复用最终
    Chromium HIS/RIS/LIS renderer、scope授权和no-store响应。黄色框由模板按日期、时间、金额、结果与结论字段确定性绘制。

16. **标签页会话隔离。** 每个页面加载时只在内存生成tab UUID，并随请求头发送；服务端把HttpOnly cookie名称
    与tab UUID绑定，只读取当前tab cookie。token不返回JavaScript、不写storage/URL；刷新产生新tab UUID并回登录页。

17. **系统原图只显示中文业务列。** HIS/RIS/LIS renderer在固定字段契约层映射中文标签，不改源值、不在前端重画，
    继续复用最终证据包模板、黄色关键值框和永久非院方原始截图标记。

18. **一级分派入口合并，表格用显式列宽。** Hospital Pilot的L1只保留`assignment`导航并显示为“分派二级”，
    同页组合流程图、指标、分派面板和任务表；L2/L3导航不变。任务表不再用`:first-child`推断选择列，选择列和疑点列
    使用显式class，疑点列固定可读宽度并最多两行，悬停可看全文。

19. **演示回溯复用轮次重置。** 不扩普通reassign/recall，也不新增迁移；仅Hospital Pilot的Parker/L1可调用
    `PilotRoundService.reset`。单事务锁定当前轮次20例、校验workflow version和运行中任务，保存逐例最终投影，追加
    `PILOT_ROUND_RESET`事件与安全审计，关闭旧轮次、清空当前分派/审核指针并新建ACTIVE轮次。旧event、revision、
    evidence selection、alignment、artifact和模型指针不删不改。跨轮次新审核的`revision_no`从病例全历史MAX继续递增，
    但新轮次第一条revision不声明supersede旧轮次结论。

20. **交互式回溯不得同步扫描全库production摘要。** API路径已经逐例校验固定20例均属于
    `PRIVATE_FIXTURE/FIXTURE_PRIVATE/SECTION13_REVIEW20`，全部DML只使用这组已锁定的item id和旧轮次id；因此HTTP事务
    不重复运行运维级`capture_production_isolation`。完整production前后摘要继续保留在无request id的operator/CLI重置门禁。
    前端在reset POST返回后立即释放操作busy，版本和列表刷新作为后置同步，避免已提交成功却持续显示“正在回溯”。

## Risks / Trade-offs

- [轮询增加数据库请求] → 只查活动轮次最大事件 ID，版本不变不刷新重查询，隐藏页降到10秒。
- [直接改派与并发审核冲突] → 行锁、rowversion、current revision 为空三重门禁，整批事务回滚。
- [账号自动延期造成永久权限] → 只在 ACTIVE round 下续期，最长滚动30天；轮次关闭立即撤权撤会话。
- [病案搜索词含 PHI] → 只进 POST body，不记录原文；审计仅存散列和结果数。
- [服务端内存过滤规模上升] → 当前只支持固定20例；扩到生产全量时再下推 SQL 索引，不提前建设。
- [一键回溯误伤真实审核] → 后端同时限制HOSPITAL_PILOT、Parker/L1、固定20例活动轮次和乐观版本；普通L1及非Parker历史一律拒绝。
- [回溯后版本号复用] → revision_no按病例全历史MAX递增，当前指针仍从新轮次重新建立。
- [运维级全库摘要拖慢交互事务] → API依赖已锁定20例的强scope合同，完整摘要留在离线operator；提交与后置读取分离显示。

## Migration Plan

1. 在测试库验证 additive migration 重复执行保护、约束和三张视图。
2. 部署代码前在 Scriv 自有库备份24账号相关行与当前20例指针，应用 migration。
3. 运行 Parker2/Parker3 operator，再校验24账号库存、授权、登录和健康。
4. 本地启动三独立会话，先验证重新分配和自动同步，再完成一例 L3→L2→L1。
5. 失败时停止本地服务；代码回退不删除 additive schema，账号可停用并撤销 grant/session，历史保留。
6. 一键回溯只在本地用Parker三账号完成一次完整前进→回溯→再次审核闭环；未再次明确授权前不在142主动执行回溯。
