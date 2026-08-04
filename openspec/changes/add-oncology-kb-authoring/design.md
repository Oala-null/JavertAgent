## Context

Javert 当前从 2025 国家医保目录、2025《新型抗肿瘤药物临床应用指导原则》和医院 2026-06 药品总库构建肿瘤药知识。现有聚合资产包含 390 个药品实体、156 条医保限定和 69 条指南适应证兜底，但运行时条件树只有 3 个已批准分支；病理 KB 只有 1 个已批准条目，治疗方案 KB 只有 4 个已批准方案，另有 121 个从病历语料聚合出的待审方案别名候选。第一期的“信息最全”不是只把当前 JSON 改成 Excel，而是以医院产品、国家目录、指南段落、现有肿瘤 JSON 和规则 YAML 的并集为候选宇宙，并对每个来源集合做可解释的纳入/排除对账。

通用药 bulk KB 已覆盖大量药品和规则类型，RD10-RD37 的 28 条精选单药在药品名称与规则类型层面均能找到 bulk 候选，但这只能证明候选所有权重叠，不能证明精选 YAML 中的临床扩展、证据提示、文书缺失处理、别名和金标样例已经迁移。例如某条单药规则可能从基础限定延伸出术后替代治疗语义，这种知识不会因为 bulk 表里存在同药名而自动保留。因此本 change 同时引入跨药品的知识保全台账；肿瘤知识在一期完成结构化和专家审核，其他药品先完成原子化盘点、目标映射和执行状态门禁，后续可沿用同一底座逐批结构化。

现有 `scripts/build_oncology_eligibility_assets.py` 通过代码内手工 manifest 产生少量条件树，适合验证运行时契约，但不适合让医保办、药师、肿瘤专家和病理专家共同维护全量知识。JSON 资产缺少方便人工逐行修改、批注和退回的界面，也不适合作为可查询的审核台账。

本 change 的目标 SQL Server 是 142 上用户指定的 `[知识库_work]`。项目当前写边界只默认允许 `TP_data_hub`，因此 `[知识库_work]` 在 DBA 明确授权、进程环境把它加入 owned database 白名单且预检通过之前必须视为不可写。`sh_yb_platform` 继续只读，`zadig` 继续保存工作台业务结果，不承载知识真相。

当前没有具体厂家法定说明书，所谓“说明书侧”数据全部来自临床应用指导原则。本设计将其明确标为 `GUIDELINE_INDICATION`，并预留 `NMPA_LABEL`，禁止在 UI、Excel、数据库和运行时证据中混称。当前规则的人工维护窗口统一初始化为 `2026-01-01` 至 `2027-12-31`；用户已决定 2026 年以前病例仍应用当前发布规则并正常自动裁决，但必须记录越界并显示时间核查警告。

利益相关方包括知识提取/数据工程人员、肿瘤临床专家、药师、病理专家、医保办、Javert 发布人员以及 142 DBA。知识表不含患者事实；从病历语料挖掘的方案候选只允许输出聚合别名和频次，不得输出患者号或原文。

## Goals / Non-Goals

**Goals:**

- 把全部现有医保限定和指南适应证转成可追溯的来源、分支和条件节点候选，未完成拆分的条目也必须显式出现在待审清单中。
- 以医院药品总库、国家目录、指南、现有肿瘤资产和规则 YAML 的并集建立肿瘤候选全集及来源覆盖矩阵，明确重复、仅单源、排除和待审原因。
- 交付两份适合专家逐行审核且可无损回导的 Excel 工作簿。
- 把 RD10-RD37 的个性化内容拆为可审计知识原子，逐条映射到结构化 KB、词典、求值策略、审核提示或回归用例；执行状态变化不得删除原始知识和迁移证据。
- 允许专家在草稿中手工修改规则生效起止日期，同时防止已发布历史被原地覆盖。
- 在 142 `[知识库_work]` 建立结构化 authoring、审核事件、不可变 release 和 staging 上传模型。
- 保持 Javert 运行时离线：只从批准 release 确定性编译带 schema/checksum 的 JSON，不在患者审计过程中依赖 142 在线查询。
- 对 2026 年以前病例执行当前规则正常裁决，并把回溯应用策略、实际日期和知识版本写入结果。
- 让每次上传、审批、发布、编译和回滚都有稳定 ID、计数对账和可复现报告。

**Non-Goals:**

- 一期不采集或声称拥有具体厂家法定说明书，不把指南条目标为 `NMPA_LABEL`。
- 一期不结构化禁忌症、不良反应、药物相互作用、肝肾功能剂量调整或完整药物安全规则。
- 一期不把通用 bulk KB 中全部药品都转换为与肿瘤 KB 同等深度的条件树；非肿瘤药只要求完成精选知识保全台账和迁移门禁，后续按优先级逐批结构化。
- 一期治疗方案只审核名称、别名、癌种上下文和组成药品，不解析或推理剂量、给药日、周期和治疗阶段。
- 不把 Excel 文件本身作为权威知识库，不允许 Excel 直接覆盖发布表。
- 不让未审核候选、来源不完整条目或越权数据库写入进入自动裁决。
- 不在本 change 中自动写回患者病历、回填旧 `eligibility_json` 或改变专家对既有审计结果的认同/驳回语义。
- 不在一期提供公网或普通工作台用户可见的 Web 上传入口；上传由受控运维命令执行。
- 不因 raw bulk 中存在同药同类型记录就认定精选规则知识已迁移，也不为了保留知识而让精选规则与 bulk 在默认执行集重复裁决。

## Decisions

### 1. 两个核心 KB 共用一层概念、来源和审核元数据

核心 KB 分为：

1. `肿瘤药指南适应证与医保限定条件树KB`：医保支付和指南适应证分别建树；
2. `肿瘤治疗方案组成KB`：方案规范名、别名、癌种上下文和组成药品。

两者共用药品概念、具体产品、药品代码、疾病/标志物/药物类别字典、来源文档、来源片段、审核事件和 release。病理标志物继续作为独立运行时资产，但专家工作簿第一期把相关候选作为条件树引用的共享字典和 QA 清单展示，避免让专家在多个文件重复修改同一阈值。

跨药品的精选规则知识原子台账复用同一药品概念、来源、审核和 release 元数据，但它是治理/迁移控制资产，不定义第三套审计语义，也不算第三个业务 KB。肿瘤知识原子直接随本期条件树进入专家审核；非肿瘤知识原子先进入全局迁移台账和 QA 报告，待对应 bulk/结构化 KB 能承接后再发布。

来源类型固定为：

```text
INSURANCE_PAYMENT
GUIDELINE_INDICATION
NMPA_LABEL             # 预留，当前不得使用
```

同一药品的医保树与指南树各自产生状态；“结合”发生在规则关联和结果展示层，不把两段来源文字预先合成一棵 `AND` 树。医保条款写有“按说明书用药”时，当前只能关联指南参考并标记 `label_source_missing=true`，不能伪造法定说明书结论。

选择独立双树而非单一优先级覆盖，是为了区分“指南适应证内但医保支付外”和“指南适应证外”两类完全不同的问题，并为以后接入真正说明书保留无损迁移路径。

### 2. Excel 是受控审核投影，不是数据真相源

每份工作簿包含 `template_schema_version`、`export_batch_id`、`source_snapshot_checksum` 和导出时间。机器列锁定并使用稳定 ID；专家只填写有颜色标识、带数据验证的审核列。Excel 单元格评论可作为阅读辅助，但不得作为唯一的结构化意见来源。

条件树工作簿包含：

| Sheet | 作用 |
|---|---|
| `00_使用说明` | 角色、编辑规则、状态说明和回导步骤 |
| `01_批次元数据` | schema、批次、来源快照、默认有效期和计数 |
| `02_药品产品` | concept、产品、剂型、厂家、批准文号预留、医保码和院内码 |
| `03_来源原文` | 来源文档、片段、页码/锚点、原文和 checksum |
| `04_适应证分支` | 每个医保/指南 OR 分支及分支级有效期 |
| `05_条件节点` | 邻接表形式的 `ALL/ANY/LEAF` 节点和原文 span |
| `06_专家审核` | 对分支、节点和有效期的认可、修订、驳回与理由 |
| `07_术语字典` | 只读的药品、药物类别、癌种、标志物和操作符字典 |
| `08_QA问题` | 未拆分、悬空引用、冲突、缺来源和自动校验结果 |
| `09_肿瘤知识保全` | 肿瘤精选规则知识原子、迁移目标、验证状态和缺口；非肿瘤原子另见全局迁移报告 |

方案工作簿包含：

| Sheet | 作用 |
|---|---|
| `00_使用说明` | 方案审核边界和缩写歧义提示 |
| `01_批次元数据` | schema、批次、来源快照和计数 |
| `02_方案主表` | regimen ID、规范名和版本 |
| `03_方案别名` | 原始别名、归一别名、语言/类型和歧义状态 |
| `04_方案上下文` | 癌种、组织学和适用场景，不由方案名反推诊断 |
| `05_方案组分` | 药品概念/药物类别、token、必选性和作用角色 |
| `06_预留给药字段` | 剂量、单位、途径、给药日、周期、阶段；一期留空且不参与发布 |
| `07_专家审核` | 对方案、别名、上下文和组分的结构化决定与意见 |
| `08_QA问题` | 别名冲突、未知组分、重复方案和来源缺失 |

审核列至少包括：

```text
review_decision = APPROVE | APPROVE_WITH_EDIT | REJECT | UNABLE_TO_DETERMINE
expert_value
expert_comment
evidence_reference
reviewer_id
reviewed_at
```

回导程序不依赖单元格顺序、样式或公式结果，只按受支持 sheet 名、列名、稳定行 ID 和 schema version 读取。缺 sheet、改机器列、未知枚举或旧模板不得“尽量导入”，而要逐行报错并停止正式入库。

选择 Excel 是因为专家易用且便于离线流转；选择“投影”而非“真相源”是为了避免复制粘贴、排序、公式和多人版本覆盖破坏审计链。

### 3. 条件树采用邻接表和类型化叶子

一个来源规则可有多个 OR 分支；每个分支恰有一个根节点。节点结构为：

```text
node_id
branch_revision_id
parent_node_id          # root 为 NULL
sibling_order
node_kind               # ALL | ANY | LEAF
criterion_type
operator
target_kind             # CONCEPT | CLASS | REGIMEN | VALUE
target_concept_id
expected_text
expected_number
expected_unit
expected_values_json
evidence_window_json
missing_policy
source_fragment_id
source_text
source_span_start/end
```

一期 `criterion_type` 至少支持：

```text
diagnosis
histology
stage
disease_status
resectability
biomarker
age
sex
menopausal_status
prior_therapy
therapy_count
line_of_therapy
treatment_status
combination_requirement
surgery_status
radiotherapy_status
transplant_eligibility
time_window
clinician_assessment
unsupported
```

负向条件由叶子的 `operator` 表达，不增加 `NOT` 树节点。联合用药目标可以是具体药品、药物类别或已批准方案，并用 `REQUIRED | OPTIONAL | WITH_OR_WITHOUT` 表示必选性，避免把“联合 A 和 B，联合或不联合 C”误编为三个必选条件。

导入 QA 必须检查：每个分支一个根、父节点存在、无环、同层顺序唯一、聚合节点有子节点、叶子无子节点、每个叶子有类型/操作符/来源锚、引用概念存在、所有来源限定至少映射一个批准或待审分支。`unsupported` 可以进入待审库，但不得进入发布 release。

选择邻接表而非 Excel 单元格中的整段 JSON，是为了让专家能逐叶审核，也便于 SQL 查询、差异比较和局部修订；发布器再重建嵌套 AST。

### 4. 规则日期在 revision 上可编辑，release 发布后不可变

有效期字段属于规则/方案 revision，而非全局常量：

```text
effective_from               # inclusive
effective_to                 # inclusive
effective_date_basis         # SOURCE_EXPLICIT | CURRENT_FILE_ASSUMPTION | EXPERT_OVERRIDE
date_override_reason
date_review_comment
historical_application_policy
```

当前候选默认：

```text
effective_from = 2026-01-01
effective_to = 2027-12-31
effective_date_basis = CURRENT_FILE_ASSUMPTION
historical_application_policy = APPLY_CURRENT_RELEASE_WITH_WARNING
```

专家可以在 `DRAFT` 或 `CHANGES_REQUESTED` revision 中修改起止日期。只要日期与导出默认值不同，就必须提供 `date_override_reason`，并把 basis 改为 `EXPERT_OVERRIDE`。导入时验证起始日不晚于结束日；同一 `logical_rule_id` 的可发布 revisions 不得存在有效期重叠。不同适应证分支可共享同一规则版本和有效期，不把正常 OR 分支误报为冲突。

状态流转为：

```text
DRAFT -> IN_REVIEW -> APPROVED -> RELEASED -> RETIRED
             |             |
             v             v
CHANGES_REQUESTED       新建 revision，不原地修改
             |
             v
          REJECTED
```

进入 `APPROVED` 后内容冻结；修改日期、树、来源、方案组分或审核结论都要从该 revision 克隆出新 revision，并记录 `supersedes_revision_id`。release 只链接 approved revision，发布后 checksum 和计数不可变。

选择 revision + release，而不是在一张表上覆盖 `start_date/end_date`，是为了既满足人工维护，又保证历史审计结果能定位到当时实际使用的知识。

### 5. 历史日期采用非对称时间策略

运行时对服务日期作三段处理：

```text
service_date < 2026-01-01:
  使用执行时 active release 正常自动裁决
  temporal_applicability = BEFORE_EFFECTIVE_WINDOW
  effective_date_enforced = false
  添加“核查当期指南/医保限定是否适用”警告

2026-01-01 <= service_date <= 2027-12-31:
  正常按生效期选择并裁决
  temporal_applicability = IN_WINDOW

service_date > active release 的 effective_to:
  不静默沿用过期知识
  REVIEW_REQUIRED，并提示发布新版本或人工确认
```

历史回溯并不改写知识元数据。每次结果必须记录 `release_id`、`rule_revision_id`、来源版本、实际 service date、声明窗口、时间策略和警告。对同一历史病例用新 release 重跑可能得到新结论，这是用户选择“使用当前规则”的自然结果；旧结果不回填，仍可由其 release ID 复现。

该策略不能简单等同于全局关闭生效期闸，因为全局关闭会同时让 2027 年以后继续使用过期规则。本 change 将历史 fallback 与未来过期处理分开。

### 6. 治疗方案只发布组分，预留日程结构

方案 authoring 采用：

```text
regimen_revision
regimen_alias
regimen_context
regimen_component
regimen_schedule_component   # schema 预留，一期 release 不读取
```

组分引用 `drug_concept_id` 或 `drug_class_id`，并记录 token、`REQUIRED/OPTIONAL/WITH_OR_WITHOUT`、作用角色和来源。别名按 Unicode、大小写、空格和连字符归一，但保留原始写法；同一归一别名在多个已批准方案间冲突且癌种上下文不能消歧时，必须保持 ambiguous，不能发布为无条件推理。

`regimen_schedule_component` 预留 `dose_value/dose_unit/dose_basis/route/administration_days/cycle_length_days/max_cycles/treatment_phase/sequence_no`。一期 Excel 可以展示空列，但导入器不要求专家填写，发布器也不把它们用于方案解析。

选择预留独立日程表，而不是把空列塞进组分主表，是为了将来一个药品在同一方案内多日给药时可以一对多扩展，同时保持一期模型简单。

### 7. `[知识库_work]` 使用 staging、revision、review、release 四层

目标限定为 SQL Server database `[知识库_work]`，业务 schema 为 `[kb]`，上传暂存 schema 为 `[kb_stg]`。核心表为：

```text
[kb_stg].[import_batch]
[kb_stg].[import_row]

[kb].[source_document]
[kb].[source_fragment]
[kb].[drug_concept]
[kb].[drug_product]
[kb].[drug_code_xref]
[kb].[curated_knowledge_atom]
[kb].[curated_knowledge_mapping]
[kb].[eligibility_rule_revision]
[kb].[eligibility_branch]
[kb].[condition_node]
[kb].[regimen_revision]
[kb].[regimen_alias]
[kb].[regimen_context]
[kb].[regimen_component]
[kb].[regimen_schedule_component]
[kb].[review_event]
[kb].[knowledge_release]
[kb].[release_item]
```

`import_batch` 记录工作簿 checksum、模板版本、上传者、源文件名的安全 basename、行计数、状态和错误摘要；`import_row` 记录 sheet、Excel 行号、稳定行 ID、规范化 payload、行 checksum 和校验状态。正式 authoring 表使用 typed columns，不能只把整个工作簿永久保存成 JSON blob。

同一 `workbook_sha256 + template_schema_version` 重复上传返回原批次，不重复创建 revision。一个批次只有在所有机器列和引用通过验证后，才在单事务内物化；任何一行失败则整个批次保持 `VALIDATION_FAILED`，正式表零写入。成功后对 staging 行数、各正式表新增/复用计数、逻辑主键和 checksum 做对账。

Excel 原文件不存进数据库；如需运维留档，保存到工作区外受控目录并限制权限。数据库只保存结构化知识、文件 checksum 和安全文件名，不保存凭据、患者原文或客户端绝对路径。

### 8. 142 写入必须双重指定目标库并 fail closed

所有建表和上传命令必须显式传 `--database 知识库_work`；主机、端口、用户、密码和 driver 只从环境或 mode 0600 env 文件读取，不进入命令参数、日志或 manifest。连接后首个写操作之前必须同时验证：

1. 请求库名精确等于 `[知识库_work]`；
2. 库名已列入 `JAVERT_OWNED_DBS`；
3. `SELECT DB_NAME()` 精确返回 `知识库_work`；
4. 当前 principal 具有所需 schema/table 权限；
5. dry-run 已给出目标主机的去敏标识、数据库、schema、预计行数和零 PHI 声明。

DDL 脚本使用 `sqlcmd -d 知识库_work -v KB_DATABASE=知识库_work -b`，并在第一条 DDL 前比较 `DB_NAME()` 与变量。标识符用 SQL Server 方括号安全引用，数据值全部参数化。`--database` 必填且没有默认值；未授权库必须在创建连接前拒绝。

虽然用户指定了 142 `[知识库_work]`，现有仓库规则尚未授权它。实现可先完成本地 DDL、单元测试和模拟上传；只有 DBA 创建/授权数据库且运维环境显式设置 `JAVERT_OWNED_DBS=TP_data_hub,知识库_work`（或更窄的对应环境值）后才能执行真实写入。授权不得扩大到 `sh_yb_platform`。

选择独立数据库而非放入 `TP_data_hub`，是为了隔离知识作者权限和临床数据中台；选择复用 owned database 门禁模式，是为了不创造第二套更弱的写安全实现。

### 9. 上传和发布是两个不同的操作

受控流程为：

```text
导出现有候选
  -> 生成两份 Excel
  -> 专家离线审核
  -> 本地 validate（零数据库写）
  -> 142 preflight + dry-run
  -> upload 到 staging
  -> 单事务 materialize 为 draft/review revisions
  -> 审批并冻结 approved revisions
  -> 创建 release candidate
  -> 编译 canonical JSON + checksum + coverage report
  -> 测试与 shadow
  -> publish release
  -> 部署 JSON；运行时仍离线加载
```

上传不等于批准，批准不等于发布，发布不等于部署。每一步都有独立状态、操作者、时间和报告。发布前要求：

- 所有 release item 为 approved；
- 来源、有效期和逻辑主键完整；
- 条件树结构合法且不含 `unsupported`；
- 方案别名/组分无未解决歧义；
- 现有 156 条医保限定和 69 条指南适应证均被分区为 approved 或显式 needs-review，不允许静默丢失；
- 本 release 涉及的肿瘤精选知识原子全部达到 `VERIFIED`；非肿瘤原子至少已登记、缺口明确且对应规则为 `drafting`，已经进入 `MAPPED` 的原子必须有明确迁移目标；任何精选规则只有在全部原子 `VERIFIED` 后才可变为 `abandoned`；
- JSON 重复编译字节一致，checksum 正确；
- 新旧 release diff 明确列出增加、修改、失效和有效期变化；
- 专项测试、历史日期测试、RD04 肿瘤双 scope ownership、精选知识迁移和 shadow 门禁通过。

运行时 JSON 的 metadata 增加 `release_id`、`source_snapshot_checksum` 和构建时间；每个 entry 保留 revision ID、来源引用、审核状态和有效期。数据库是 authoring 真相源，Git 中 JSON 是经审批的部署快照，两者必须按 release manifest 对账。

### 10. 审核事件追加写，发布采用最小职责分离

`review_event` 是 append-only，记录 entity type、logical/revision ID、决策、结构化修订值、意见、证据、reviewer、时间和前一事件。状态由事件投影，不允许用 UPDATE 删除反对意见。

一期至少要求一个具名领域审核人把 revision 置为 approved，并由不同的发布操作者创建/publish release；具体是否要求医保办、药师、病理和肿瘤专家联合会签由运维配置决定，不硬编码为固定人数。有效期发生人工覆盖时必须有专门日期审核意见。

选择最小职责分离，是为了在不阻塞一期专家流程的同时，防止同一上传动作自动进入生产。

### 11. 回滚按 release 指针执行，不删除知识

发布器保留上一个已发布 release。回滚只切换部署 JSON/active release 指针并重启或热加载运行时，不删除新 revision、审核事件或 staging 批次。历史审计结果继续引用实际使用的 release，不用回滚后的知识重写。

失败批次可以标记 `ABORTED`，但不得物理删除审计记录；staging payload 按保留策略清理时仍保留批次 checksum、计数和错误摘要。

### 12. 第一期先做完整肿瘤候选宇宙，通用药沿同一底座渐进建设

肿瘤候选全集按以下五类输入取并集，而不是以任一单表为准：

1. 医院 2026-06 药品总库中的肿瘤相关产品和院内码；
2. 2025 国家药品目录中的肿瘤相关药品、医保码和支付限定；
3. 2025《新型抗肿瘤药物临床应用指导原则》中的药品章节、适应证、联合条件和人群条件；
4. 现有肿瘤 drug/eligibility/pathology/regimen JSON 资产；
5. 规则 YAML 中与肿瘤药相关的来源片段和精选知识原子。

每个候选保留 `source_membership`、规范化决策、纳入/排除原因和 crosswalk 置信状态。仅医院有、仅国家目录有、仅指南有、仅旧资产有的条目都必须在覆盖矩阵中出现；无法自动判定是否属于肿瘤的候选进入 `needs_review`，不得静默排除。药品实体总数不是固定验收常量，构建报告必须从当次 source checksum 动态生成。

通用药未来复用相同的 source/concept/condition/review/release 模型，但一期不为全部通用药生成专家条件树。这样既不把所有药品语义塞进一个超级 RD，也不会为肿瘤项目另造一套无法扩展的孤立数据模型。

### 13. 执行状态与知识迁移状态分离

每条精选单药规则先解析为一个或多个不可静默删除的知识原子：

```text
atom_id
source_rule_id
source_field_or_test
atom_type                 # SOURCE_RULE | CLINICAL_EXTENSION | EVIDENCE_POLICY |
                          # NORMALIZATION | DOCUMENTATION_GUIDANCE | REGRESSION_GOLD
canonical_payload
source_checksum
migration_status          # DISCOVERED | MAPPED | VERIFIED
target_kind               # CONDITION | DICTIONARY | EVALUATOR_POLICY |
                          # REVIEW_GUIDANCE | REGRESSION_CASE
target_id
verification_evidence
```

“bulk 有同药 + 同规则类型”只满足候选所有权检查，不提升 `migration_status`。只有原子已经落到明确目标、目标参与发布或测试、旧规则代表性输入与新路径的预期语义一致，才能标记 `VERIFIED`。无法结构化但仍有价值的提示可以先映射到审核指导或回归用例，不能丢弃或伪装成来源条款。

规则执行状态按以下门禁处理：

```text
全部知识原子 VERIFIED:
  精选规则 MAY 标记 abandoned
  不进入默认 audit-patient 与 router 可执行集合
  原 YAML、知识台账和回归用例继续保留

存在 DISCOVERED/MAPPED 未验证原子:
  精选规则 MUST 为 drafting（必要时从 abandoned --force 后退）
  notes 明确 knowledge_migration_pending 和缺口报告
  同样不进入默认执行集合，但不得宣称已由 bulk 完整取代
```

`--rules RDxx` 只作为迁移期间的显式对照/复核路径，不构成生产双跑。状态或关键词变化后必须重建 router index，并验证 YAML、索引和知识迁移台账一致。

运行时按语义而不是按文件规模分工：RD04 独占肿瘤资格的 `INSURANCE_PAYMENT` 与 `GUIDELINE_INDICATION`；R007/RD01/RD02 排除已迁入 RD04 的肿瘤候选；RD03 保留禁忌/安全语义，不能与资格条件树合并。非肿瘤 bulk 继续承载通用药候选，待个性化原子逐步迁入。

## Risks / Trade-offs

- [把指南误当说明书] → 全链路使用 `GUIDELINE_INDICATION`，`NMPA_LABEL` 当前禁止激活，UI/Excel 显示来源名称和免责声明。
- [专家在 Excel 中排序、删列或复制旧模板] → 稳定行 ID、schema version、机器列 checksum、必需 sheet/列验证和逐行错误报告；失败批次零正式写入。
- [人工修改有效期破坏历史] → 只允许改 draft revision，日期覆盖必须有理由，approved/released 不可变并检查同逻辑规则有效期重叠。
- [2026 年以前自动裁决被误解为历史事实] → 保存 `BEFORE_EFFECTIVE_WINDOW`、active release、声明窗口和显著警告；报告明确标识为当前知识回溯应用。
- [全局关闭日期闸导致 2027 后继续使用旧规则] → 前窗口 fallback 与后窗口过期分支分开实现，未来过期默认 REVIEW_REQUIRED。
- [治疗方案缩写歧义误推药品] → 癌种上下文、最长匹配、显式组分优先和 ambiguous 状态；未解决歧义不得发布为确定性映射。
- [142 目标库误配或越权] → 必填数据库参数、owned whitelist、连接前拒绝、`DB_NAME()` 二次核验、DDL 双参数和 dry-run；不沿用当前 TP_data_hub 授权。
- [关系库与 Git JSON 漂移] → release manifest、确定性编译、checksum 和双向计数/ID 对账；部署只接受已发布 manifest。
- [Excel 数据量和专家负担过大] → 按药品/癌种/来源分批导出，但 stable ID 和 release 可以跨批次合并；QA 优先展示高风险、未拆分和高频候选。
- [复杂限定无法由一期类型表达] → 以 `unsupported` 显式进入待审，不允许 LLM 猜测或静默发布；后续以新 criterion schema version 扩展。
- [raw bulk 覆盖掩盖精选规则语义损失] → 逐字段/测试拆分知识原子，要求目标映射与等价回归；迁移未完成的规则保持 drafting 而不是直接 abandoned。
- [为保留个性化规则造成重复裁决] → 把知识保全与执行状态分开；drafting/abandoned 都不进入默认执行与 router 可执行集合，生产所有权仍由 bulk 唯一承担。
- [患者语料泄漏] → 只输出聚合方案别名和频次，不输出 patient ID、ownership ID、原始病历 span 或未盐化标识。

## Migration Plan

1. 冻结一期字段字典、来源类型、criterion/operator 枚举、两个 Excel schema 和当前默认有效期策略。
2. 从医院总库、国家目录、指南、现有 JSON 和规则 YAML 生成肿瘤候选并集与来源覆盖矩阵，证明每个候选均有纳入、重复、排除或待审去向。
3. 解析 RD10-RD37 的 prompt、notes、expected signal 和既有测试为知识原子，建立目标映射；对未达到 VERIFIED 的规则从 abandoned 后退到 drafting，并重建/核对 router index。
4. 生成两份专家工作簿及去标识小样，完成导出→专家列修改→回导 round-trip 测试。
5. 实现本地解析、结构校验、日期重叠检测、树不变量、方案引用检查和逐行错误报告；此阶段不连接 142。
6. 编写 `[知识库_work]` DDL、迁移和 owned database 安全预检，在本地/临时 SQL Server 或 mock 上验证幂等性、事务回滚和非 owned 库连接前拒绝。
7. 由 DBA 创建或确认 142 `[知识库_work]`、`[kb]`/`[kb_stg]` schema、最小权限账号和备份策略；运维显式把库加入 `JAVERT_OWNED_DBS`。没有书面授权则停在本地工件。
8. 对第一份专家工作簿运行 validate、dry-run 和小批量 staging 上传；对账后再上传剩余批次，不直接创建 release。
9. 由领域专家处理 `CHANGES_REQUESTED/UNABLE_TO_DETERMINE`，补齐来源、树和有效期；所有修改形成新 revision/review event。
10. 创建首个 release candidate，编译四个肿瘤运行时资产和 coverage/diff manifest，验证 deterministic checksum、旧行兼容、精选知识覆盖和历史日期行为。
11. 在 shadow 环境运行 RD04 肿瘤双 scope、通用 bulk、RD03 和方案 resolver 回归；确认无重复所有权、2026 年以前正常裁决且有警告、2026—2027 正常、2027 后 fail closed。
12. 获得生产授权后 publish release、部署 JSON 和重启服务；检查实际进程加载的 release ID、schema/checksum、SQL/Hub 健康及工作台时间警告。
13. 回滚时恢复上一 release 的 JSON/active 指针并重启，不删除数据库 revision、审核事件、知识原子或新字段。

## Open Questions

- 142 `[知识库_work]` 是否已经由 DBA 创建，以及将授予哪个最小权限 principal；这决定真实上传任务何时可以解除门禁，但不阻塞本地实现。
- 一期每类条目的具名终审角色由谁承担，以及是否需要医保限定、病理阈值和治疗方案分别配置不同审批组。
- 填写后的专家工作簿由哪个受控目录保存、保留多久；Git 只应保存空模板、去标识样例和发布后的结构化资产。
