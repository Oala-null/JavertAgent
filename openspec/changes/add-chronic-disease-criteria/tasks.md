## 1. 通用条件树合同与聚合器

- [x] 1.1 新增 `src/javert/clinical_criteria/contracts.py`，定义四态、观测事实、证据锚点、节点评估与 proof tree 合同
- [x] 1.2 新增 `src/javert/clinical_criteria/evaluator.py`，实现 AND、OR 与 `AT_LEAST_N` 的确定性四态聚合和决定性路径
- [x] 1.3 为 AND/OR/AT_LEAST_N 编写穷举真值表、proof 同构与边界测试
- [ ] 1.4 让 oncology 通过兼容 re-export/适配复用最小通用合同，并证明既有肿瘤 JSON 与金标回归不变

## 2. 慢病知识合同、来源与版本化资产

- [x] 2.1 新增 `src/javert/chronic/contracts.py`，定义 policy、source fragment、criterion node、blocker 与 `ClinicalCriteriaEvaluation`
- [x] 2.2 新增 2025/2020 source manifest，记录文号、物理页、PDF SHA-256、人工复核片段及版本边界
- [x] 2.3 新增条件资产 JSON schema 和 Pydantic 校验，强制 2025 恰好包含 `CD01-CD20`、稳定 ID、单根和完整来源
- [x] 2.4 把反馈 Excel 的 20 病种与 58 个主要节点转录为 `configs/chronic_disease_criteria.json` 的 drafting revisions
- [x] 2.5 为 CD01/CD07/CD09/CD19 写结构化 blocker，禁止借用 2020、LLM 或默认值闭合 2025 歧义
- [x] 2.6 新增构建/校验脚本，计算 fragment/source/asset checksum 并保证相同输入字节级确定
- [x] 2.7 新增 schema、checksum、版本隔离、20 病种完整性、C 类 blocker 与重复构建测试
- [x] 2.8 生成不含患者数据的知识覆盖/needs-review 报告，并登记专家审批所需的外部事项

## 3. 患者事实归一与慢病叶子求值

- [ ] 3.1 新增 `PatientClinicalSnapshot`，只消费 schema manifest 声明的诊断、文书、检验、检查和手术内部契约
- [ ] 3.2 实现诊断别名/编码、主诊标志、否定/疑似与患者级 evidence anchor 归一
- [ ] 3.3 实现实验室数值解析、未舍入比较、版本化单位 allowlist 和不兼容单位 `UNKNOWN`
- [ ] 3.4 实现检查/文书候选 assertion、否定与来源 locator；无锚点候选不得满足叶子
- [ ] 3.5 实现重复观测去重、次数、最小/最大间隔、时间窗边界和缺日期 `UNKNOWN`
- [ ] 3.6 实现 A/B 类所需的 presence、numeric、series、duration、count、categorical 与 exclusion leaf policies
- [ ] 3.7 实现 C 类叶级 shadow 求值但跳过权威根聚合，固定 `qualified=null/REVIEW_REQUIRED`
- [ ] 3.8 增加数值边界、单位、重复导入、时间跨度、否定、缺失、冲突和跨域计数测试

## 4. CD 规则注册与受控执行入口

- [x] 4.1 扩展 Rule/AuditResult 的 ID 校验以接受 `CD\d{2,3}`，并覆盖所有硬编码 ID 正则
- [x] 4.2 修改规则发现、list/show、显式 `--rules`、元数据与 Router index 构建，使 CD 可发现但 drafting 不进默认集合
- [x] 4.3 增加 `rule_kind` 与 `clinical_criteria_ref` 可选字段，保持既有 R/RD YAML round-trip 不变
- [x] 4.4 新增 `configs/rules/CD01-CD20.yaml`，全部保持 drafting，医学逻辑只引用慢病资产而不复制到 prompt
- [x] 4.5 增加 `JAVERT_CHRONIC_DISEASE_CRITERIA=off|shadow|on`，仓库默认 off，并校验非法值 fail fast
- [x] 4.6 重建并核对 Router index，证明 CD drafting 元数据存在但不会被默认路由执行
- [x] 4.7 增加 CD 合法/非法 ID、文件名一致性、显式选择、默认排除及既有 R/RD 回归测试

## 5. Runner、结果投影与双库存储

- [ ] 5.1 新增慢病 runtime，按 `rule_kind` 加载冻结资产、收集事实、求值并构造结构化结果
- [ ] 5.2 实现资格发现投影：QUALIFIED/NOT_QUALIFIED→CLEAN，REVIEW_REQUIRED/BLOCKED→INCONCLUSIVE，且 CD 不进 verdict gate
- [x] 5.3 `AuditResult` 增加可空 `clinical_criteria_evaluation` 并校验不能与肿瘤 eligibility payload 混用
- [x] 5.4 SQLite 幂等增加 nullable `clinical_criteria_json`，实现新旧行 round-trip 且不回填历史
- [x] 5.5 SQL Server 幂等增加 nullable `clinical_criteria_json`，更新写入、读取与精确 pending 同步路径
- [x] 5.6 API/SSE 只增不改地暴露可空结构化字段，并验证旧消费者和严格 DTO 兼容
- [x] 5.7 增加普通 precheck/LLM/verdict gate、RD04 eligibility、旧 SQLite/SQL Server 行的组合回归测试

## 6. Workbench 慢病资格面板

- [x] 6.1 扩展 Workbench 模型与查询，安全解析 `clinical_criteria_json`，空值和损坏值 fail closed
- [x] 6.2 新增“门诊慢性病认定条件评估”面板，显示 disposition、版本、revision、blocker、逐节点四态和缺失材料
- [x] 6.3 实现数值/单位/测量日期、决定分支、AT_LEAST_N 上下界和 patient-scoped 证据锚点展示
- [x] 6.4 让 CD 结果排除于普通违规 badge、违规率和违规工作队列，同时保留旧审核/评论交互
- [x] 6.5 让 `Chronic_Disease` 批次入口使用并保持 `filter=all`，不全局放宽其他批次过滤语义
- [x] 6.6 增加 QUALIFIED/NOT_QUALIFIED/REVIEW_REQUIRED/BLOCKED、历史空字段、RD04 专用面板与证据跳转测试

## 7. 候选召回与 5+45 试验脚本

- [ ] 7.1 新增只读候选召回，按病种诊断/别名宽召回但不把 candidate 当 qualification 或 gold
- [ ] 7.2 新增覆盖优先的确定性抽样器，只选择 EVALUATED+SATISFIED，按病种、OR 分支、计数、阈值和时间窗扩展覆盖
- [ ] 7.3 新增 0700/0600 私有 manifest、秘密盐化病例引用、异常清理和 Git/日志 PHI 扫描
- [ ] 7.4 新增 `Chronic_Disease` tag 的 ownership/manifest/criteria checksum 防混批与稳定 replay key
- [ ] 7.5 实现 Phase 1 五例冒烟门禁；任一求值、结构化回读、隐私、同步或 Workbench 检查失败即阻止扩批
- [ ] 7.6 实现 Phase 2 最多追加 45 个唯一病例，不重复 Phase 1，不用 UNKNOWN/CONFLICT/NOT_QUALIFIED/BLOCKED 凑数
- [ ] 7.7 实现仅限本 pilot run ID 的精确同步与 SQLite/SQL Server 逐项对账，禁止全历史 pending 扫描
- [ ] 7.8 默认排除 HIV 等高敏感真实病例；增加显式启用、权限、用途审计与合成夹具测试

## 8. 真实数据 dry-run、Workbench 试跑与 QA

- [ ] 8.1 在只读源运行聚合 dry-run，记录各病种 candidate、四态、QUALIFIED 数、缺失原因和覆盖缺口，不输出患者号
- [ ] 8.2 冻结本次 source snapshot、criteria revision/checksum、selection manifest 与 batch ownership
- [ ] 8.3 先运行 5 个 QUALIFIED 病例并验证本地回读、142 同步、Workbench `filter=all`、证据锚点和违规统计增量为零
- [ ] 8.4 五例门禁通过后追加最多 45 例；若严格 QUALIFIED 不足 50，如实报告实际数而不降标准
- [ ] 8.5 对账预期/实到/唯一 patient-rule、重复、额外行、结构化 JSON、disposition、病种分支与 sync state
- [ ] 8.6 生成只含去标识引用和聚合统计的 pilot QA 报告，并确认 Git、日志、测试输出无患者号/原文/salt/run ownership

## 9. 文档、严格校验与交付

- [x] 9.1 更新 README、架构文档、慢病维护/试跑/回滚 runbook 与 Workbench 用户指南
- [x] 9.2 若共享序列化使 2C 暴露新字段，按“只加不删不改名”同步全部 v1/v2/v3 对接文档并验证 DTO
- [x] 9.3 运行直接相关测试、受影响模块组合测试、端到端命令和全量非慢测试，记录原始 pass/skip/fail/error
- [ ] 9.4 运行知识资产、Router index、SQLite/SQL Server 迁移、Workbench 与 PHI 门禁的严格 QA
- [x] 9.5 运行 `openspec validate add-chronic-disease-criteria --strict`，确保 tasks 状态与真实验证一致

## 10. 2026-09-08 专家反馈与单PDF shadow交付

- [x] 10.1 记录五项专家解释的工作簿checksum与单元格定位，生成新revision，保留未解决门禁
- [x] 10.2 接通CD专用Runner、候选证据校验和shadow proof；off零患者读取，未审批不自动认定
- [x] 10.3 接通nullable双库存储、API/SSE、工作台慢病候选标记与“慢病”tag
- [x] 10.4 验证私有单病例清单、临床页隔离、幂等追加及限定run双库对账
- [ ] 10.5 定向/组合测试与OpenSpec strict通过，提交推送隔离分支
- [ ] 10.6 从已提交HEAD发布62，核对环境、迁移、服务、v3、HEAD与受控clean
- [ ] 10.7 完成授权单PDF OCR、19条慢病shadow+CD10关闭状态，发布工作台并核验原文与tag
- [ ] 10.8 记录聚合QA，清理含PHI临时材料；不归档本change
