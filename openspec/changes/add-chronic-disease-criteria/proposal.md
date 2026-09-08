## 当前交付范围（2026-09-08）

用户本次明确授权：吸收反馈工作簿新增的五项专家解释，测试并上线到62，使用一份指定PDF经内部OCR直接进行慢病试跑，结果以“慢病”tag发布到Workbench，不走2C脱敏流水线。该授权覆盖此病例及其内部OCR/LLM处理，不扩展到其他目录文件、数据库候选批跑或243环境。

本次交付是专家反馈修订和单病例shadow闭环。尚未完成整体审批的资产保持needs_review；不得把局部命中或shadow结果写成QUALIFIED。既有5+45严格资格样本计划仍为后续独立阶段，不是本次发布前提；CD10默认关闭且零患者读取。OCR材料保留未人工复核和费用完整性标记，财务/行政页不作为临床证据。原proposal/spec的最终目标保留，任务列表不把未实现部分勾选完成。

## Why

Javert 目前能以药品知识库和肿瘤资格条件树做医保审核，但不能把门诊慢性病认定标准拆成可追溯、可重复求值的临床条件树。黑龙江省 2025 版统一标准已经落地，且本地数据有足够候选用于约 50 例试验；若继续把整段标准交给 LLM，自由解释 AND/OR、阈值、次数和时间窗会产生不可接受的漂移。

## What Changes

- 以黑医保规〔2025〕12 号/黑市医保发〔2025〕50 号为新认定唯一主口径，把 20 个病种编译成版本化慢病条件树；2020 版只保留为历史认定与版本差异证据，不与 2025 条件拼接。
- 新增领域中立的四态临床条件求值：`SATISFIED / NOT_SATISFIED / UNKNOWN / CONFLICT`，支持 AND、OR、至少 N 项、数值阈值、重复测量、时间跨度和跨域计数，并输出逐节点证明树。
- 复用现有诊断、文书、检验、检查和手术数据契约；结构化数值与时间由确定性代码求值，叙述性事实只做候选抽取，缺失绝不自动当作阴性。
- 高血压、类风湿关节炎、慢性病毒性肝炎、再生障碍性贫血等原文不闭合病种继续建立叶子和 shadow 证据，但根节点在专家书面消歧前保持 `BLOCKED`，只输出 `REVIEW_REQUIRED`。
- 新增慢病专用 `CD01-CD20` 规则命名空间和显式试跑入口；规则先保持 drafting/shadow，不进入默认生产执行集。
- 扩展审核结果与 Workbench，显示慢病资格结论、逐条件状态、证据锚点和待补材料；继续保留旧 `CLEAN / VIOLATION / INCONCLUSIVE` 字段供兼容，但不得把“符合慢病标准”伪装成违规。
- 新增去标识的候选筛选与小批量脚本：先跑 5 例冒烟，再按病种和分支覆盖选择约 50 个 `SATISFIED` 病例，以 `Chronic_Disease` 批次写入 Workbench，并执行防混批、同步和去标识 QA。
- 不新增在线依赖，不把患者号、病历原文或未盐化运行标识写入 Git、测试夹具或公开报告。

## Capabilities

### New Capabilities

- `chronic-disease-criteria`: 2025 版 20 病种的版本化条件树、来源页码、校验和、审核状态、歧义阻断和 2020 历史版本边界。
- `chronic-disease-evaluation`: 慢病事实归一、数值/时间/计数求值、四态聚合、证明树和资格结果投影。
- `chronic-disease-pilot`: 去标识候选召回、约 50 例覆盖优先抽样、5+45 分批、`Chronic_Disease` 批次写入和对账。
- `chronic-disease-workbench`: Workbench 的慢病资格结论、逐节点证据、待补材料和批次可发现性。

### Modified Capabilities

- `rule-registry`: 接受并发现 `CD\d{2,3}` 慢病规则命名空间，同时保持既有 `R`/`RD` 规则不变。
- `audit-engine`: 在不改变普通规则、precheck 和 verdict gate 语义的前提下，携带可空的慢病结构化求值，并允许 CD 规则由确定性结果形成兼容裁决。

## Impact

- **配置/知识资产**：新增 `configs/chronic_disease_criteria.json` 及 schema/build/QA 产物；源节点保留 PDF 物理页、原文片段、版本和 review status。
- **运行时**：新增 `src/javert/chronic/` 的合同、加载器、事实归一和 evaluator；CD 规则接入现有 Runner，但不复用肿瘤专属 `eligibility_json`。
- **规则与路由**：新增 `configs/rules/CD01-CD20.yaml`；显式运行可绕开默认 ready 集，后续激活前重建 Router index。
- **存储/API/UI**：SQLite/SQL Server 增加可空的慢病结构化 JSON；旧行保持可读；Workbench 增加领域中立慢病条件面板，现有审核按钮与既有字段不删不改名。
- **数据/隐私**：候选只从受控数据源读取；Git 中只保留聚合统计、合成夹具和盐化/去标识引用。HIV 等高敏感病种真实试跑默认关闭，需显式权限。
- **验证**：20 病种 schema/来源门禁、真值表、数值单位、时间窗、缺失/冲突、旧结果兼容、5 例冒烟、约 50 例批次对账和 Workbench 展示测试。
