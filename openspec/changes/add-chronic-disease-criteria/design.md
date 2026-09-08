## 当前交付范围（2026-09-08）

用户本次明确授权：吸收反馈工作簿新增的五项专家解释，测试并上线到62，使用一份指定PDF经内部OCR直接进行慢病试跑，结果以“慢病”tag发布到Workbench，不走2C脱敏流水线。该授权覆盖此病例及其内部OCR/LLM处理，不扩展到其他目录文件、数据库候选批跑或243环境。

本次交付是专家反馈修订和单病例shadow闭环。尚未完成整体审批的资产保持needs_review；不得把局部命中或shadow结果写成QUALIFIED。既有5+45严格资格样本计划仍为后续独立阶段，不是本次发布前提；CD10默认关闭且零患者读取。OCR材料保留未人工复核和费用完整性标记，财务/行政页不作为临床证据。原proposal/spec的最终目标保留，任务列表不把未实现部分勾选完成。

## Context

Javert 已有一条可参考但不能直接照搬的结构化链路：肿瘤药资格把已审核知识编译为条件树，用四态证据节点确定性求值，把 proof tree 写入 `eligibility_json`，再由 Workbench 展示。慢病认定与它共享“版本化知识、事实归一、条件树、证明树”的技术形态，但业务语义不同：肿瘤链回答药品支付限定是否满足，慢病链回答普通病例是否具备门诊慢病认定条件。慢病资格发现不是一次“已申报慢病却不合格”的违规审计，不能把不符合条件直接等同于医保违规，也不能占用肿瘤专属字段。

本 change 的两份输入均是扫描 PDF：2025 转发件共 19 个物理页，2020 历史文件共 32 个物理页，文本层不可作为可靠来源。PDF 物理页与页面中印刷页码并不总是一致，文件元数据也不能替代文号、发布日期和正文版本。因此 OCR 只能生成待核候选，所有可执行原文片段必须经人工逐页核对，并同时记录 PDF 物理页、可见印刷页码与文件校验和。

现有代码还有几项必须显式处理的边界：

- `Rule`、`AuditResult` 和若干展示正则当前只接受 `R`/`RD`；规则加载路径以 `R*.yaml` 为主，天然发现不了 `CD*.yaml`。
- 通用 audit verdict 仍是 `CLEAN / VIOLATION / INCONCLUSIVE`；`verdict_gate` 和漂移防护围绕“违规”语义工作，不能修改慢病结构化结论。
- `eligibility_evaluation`、`eligibility_json` 和现有卡片均带肿瘤药语义，复用会造成历史数据、API 和 Workbench 的歧义。
- `configs/schema_manifest.yaml` 已定义诊断、文书、检验、检查和手术等内部数据契约；`src/javert/data/hub_source.py` 是 TB_* 到这些契约的唯一映射来源。慢病实现必须消费这些契约，不能另写一套医院表映射。
- 当前患者主键通常是一次就诊/住院标识。需要跨就诊计数或多年时间跨度的条件，只有在受控数据源能提供经批准的稳定患者关联时才能跨就诊聚合；否则必须保持 `UNKNOWN`。

主要使用者包括慢病认定专家、医保办审核人员、Javert 规则维护者和 Workbench 复核人员。首阶段只做 drafting/shadow 规则和约 50 例内部试验，不进入默认生产规则集。

## Goals / Non-Goals

**Goals:**

- 把 2025 版 20 个病种转换为来源可追溯、版本可冻结、结果可重放的显式条件树，同时严格隔离 2020 历史标准。
- 抽出领域中立的 clinical criteria 合同与四态聚合器，使慢病可复用确定性基础能力，而不把慢病代码耦合进肿瘤模块。
- 基于现有诊断、文书、检验、检查和手术契约形成类型化事实；精确处理阈值、单位、次数、去重、时间窗和跨域计数。
- 为 `CD01-CD20` 提供显式 shadow 运行路径、独立结构化持久化和慢病语义的 Workbench 卡片。
- 对 A/B 类可执行部分继续规则化；对再生障碍性贫血、高血压、类风湿关节炎、慢性病毒性肝炎四个 C 类病种保留叶级 shadow 证据，但根级自动决定保持 `BLOCKED`，不让线下消歧阻塞其余病种。
- 先选择 5 例做全链路冒烟，通过门禁后再追加最多 45 例；最终只把系统确定性求值为 `QUALIFIED` 的病例放入 `Chronic_Disease` 批次供专家测试。
- 保持旧结果、旧 API 字段、SQLite/SQL Server 双写和普通规则行为向后兼容，并保证真实患者数据不进入 Git。

**Non-Goals:**

- 不让 Javert 取代法定慢病认定、专家签字或医院最终审批。
- 不用 2020 条文补齐 2025 条文缺失的 AND/OR、阈值、次数或定义，也不把两个版本拼成一棵树。
- 不在本 change 建设完整的慢病知识维护数据库或在线专家协作系统；首阶段使用仓库内可审查、可重复构建的版本化资产。
- 不让 LLM 直接决定叶子状态、聚合状态或最终资格；LLM 最多提出带原文锚点的候选事实。
- 不自动回写病历、不自动提交慢病申报、不改变已有专家审核记录。
- 不把约 50 例扩展为全院生产筛查，不默认处理 HIV 等高敏感病种，也不激活 `CD` 规则进入默认 Router 执行集。
- 不把本 change 的 `NOT_QUALIFIED` 当作医保违规。如果将来审计“已经申报慢病但实际不满足”，应另开 capability，并引入申报事实和独立违规投影。

## Decisions

### 1. 抽取领域中立的 clinical criteria 内核，慢病领域单独装配

新增两层模块：

```text
src/javert/clinical_criteria/
  contracts.py       # CriterionState、Observation、Assessment、ProofNode
  evaluator.py       # AND / OR / AT_LEAST_N 与通用树遍历

src/javert/chronic/
  contracts.py       # 慢病资格结果、来源与阻断合同
  knowledge.py       # 资产校验、版本选择、审核门禁
  facts.py           # 现有数据契约 -> 类型化临床事实
  evaluator.py       # 慢病 leaf policy
  runtime.py         # CD 规则装配与 AuditResult 投影
```

`clinical_criteria` 只知道事实、四态和证明树，不知道药品、慢病或某张医院表。慢病模块负责疾病身份、认定口径和领域 leaf。肿瘤现有 `CriterionState`、`EvidenceAnchor`、`NormalizedFact`、`CriterionAssessment`、`ProofNode` 与基础 AND/OR 聚合逐步改为从通用层导入，并由 `javert.oncology.contracts` 保留兼容 re-export；现有 `eligibility_json` 形状和历史读取不改变。

选择共享最小内核而不是复制肿瘤 evaluator，是为了避免两套真值表日后漂移；不把整个肿瘤 `EligibilityEvaluation` 泛化，是因为其中的 drug、policy scope、医保处置和指南字段并不适用于慢病。

### 2. 四态是证据真值；`BLOCKED` 是根级执行闸，不是第五种叶状态

所有实际求值的叶子和中间节点只允许：

```text
SATISFIED / NOT_SATISFIED / UNKNOWN / CONFLICT
```

- `SATISFIED` 必须有满足时间、单位、来源和比较策略的正向证据。
- `NOT_SATISFIED` 必须有明确、适用且足以否定条件的证据；“未查到”永远不能产生该状态。
- `UNKNOWN` 包括数据缺失、未搜索、单位不明、时间不可用、证据不足或候选锚点无效。
- `CONFLICT` 表示适用证据互相矛盾，且没有经审核的来源优先级可消解。

聚合规则固定为：

- `AND`：任一 `NOT_SATISFIED` 即失败；否则 `CONFLICT` 优先于 `UNKNOWN`；全部满足才为 `SATISFIED`。
- `OR`：任一 `SATISFIED` 即满足；否则 `CONFLICT` 优先于 `UNKNOWN`；全部明确失败才为 `NOT_SATISFIED`。
- `AT_LEAST_N`：以已满足数为下界，以 `SATISFIED + UNKNOWN + CONFLICT` 为上界。下界达到 N 即满足，上界低于 N 即失败；其余结果中，能改变结论的冲突优先为 `CONFLICT`，否则为 `UNKNOWN`。证明节点保存 N、上下界和决定性子节点。

慢病顶层结果采用独立 envelope：

```text
execution_status: EVALUATED | BLOCKED
root_state: SATISFIED | NOT_SATISFIED | UNKNOWN | CONFLICT | null
qualified: true | false | null
qualification_disposition: QUALIFIED | NOT_QUALIFIED | REVIEW_REQUIRED
legacy_verdict: CLEAN | INCONCLUSIVE
```

`execution_status=BLOCKED` 时不启动权威根节点聚合，`root_state=null`。系统仍可生成 `shadow_proof_tree` 并保留已实际检查叶子的四态，但 shadow 根不计算资格状态，不得形成自动资格；顶层固定 `qualified=null`、`qualification_disposition=REVIEW_REQUIRED`。`BLOCKED` 由知识生命周期门禁产生，不允许 leaf evaluator 或 LLM 自行设置。

资格与旧 verdict 的确定性投影为：

| 执行状态/根状态 | qualified | qualification_disposition | 兼容 verdict |
|---|---:|---|---|
| `EVALUATED + SATISFIED` | `true` | `QUALIFIED` | `CLEAN` |
| `EVALUATED + NOT_SATISFIED` | `false` | `NOT_QUALIFIED` | `CLEAN` |
| `EVALUATED + UNKNOWN/CONFLICT` | `null` | `REVIEW_REQUIRED` | `INCONCLUSIVE` |
| `BLOCKED` | `null` | `REVIEW_REQUIRED` | `INCONCLUSIVE` |

`QUALIFIED` 与 `NOT_QUALIFIED` 都投影为 `CLEAN` 是有意取舍：当前输入只是普通病例中的资格发现，不存在“已经申报却不合格”的 claim 上下文。两者必须通过结构化 disposition 区分，任何 CD 结果均不得计入违规数、违规率或跨患者违规排行。选择这一投影而不是 `NOT_QUALIFIED -> VIOLATION`，是为了避免把临床资格筛查错误包装成医保违规；未来若增加申报合规审计，应建立新的规则语义和 capability。

### 3. 2025 可执行资产与 2020 历史资产在版本层物理隔离

`configs/chronic_disease_criteria.json` 使用单一 schema，但包含彼此独立的 policy set：

- `hlj-outpatient-chronic-2025`：新认定唯一可执行口径，完整包含 `CD01-CD20`。
- `heihe-outpatient-chronic-2020`：`historical_reference`，只用于版本差异和经授权的历史重建；不能为 2025 树提供节点或默认值。

每个资产至少记录：

```text
schema_version
policy_version
release_id
ordered disease_revision_ids
source_manifest_checksum
asset_checksum
```

每个病种 revision 记录稳定 `CD` ID、canonical disease ID/name、A/B/C 覆盖分类、根节点、适用期、review status、execution status 和 source refs。修改运算符、阈值、单位、时间策略、原文片段或来源 checksum 必须生成新 revision，不能继承旧审批。

每个 source document 记录文号、标题、版本、发布日期/生效期、PDF 文件 SHA-256 和物理页总数。每个病种、内部节点和叶子至少绑定一个 source fragment；fragment 记录 1-based PDF 物理页、可见印刷页码、经人工核对的原文片段、提取方式和片段 checksum。构建器对 canonical JSON（排除自身 checksum）计算校验和；运行时资产不写构建时钟，审计时间放在独立构建报告中，相同审核输入必须生成字节一致的结果。

运行时只有 schema、20 病种完整性、来源、checksum、适用期和 expert approval 全部通过的 2025 revision 才可自动求值。扫描 OCR、反馈 Excel 和构建中间文件只是 authoring 输入，不是运行时真相源。选择一个多 policy-set 的版本化资产而不是把 2020/2025 混成一张疾病表，是为了让版本选择成为显式门禁，同时保留可重放的历史差异。

### 4. A/B/C 是自动化覆盖级别；C 类永远 fail closed

反馈 Excel 中的 A/B/C 可行性结论作为 authoring 计划进入资产元数据，但不替代四态求值：

- A 类：现有结构化数据能覆盖主要事实，可完整确定性求值。
- B 类：可以建立完整树，但部分叶依赖叙述性候选、跨期数据或院内映射；证据不足自然落 `UNKNOWN`/`REVIEW_REQUIRED`。
- C 类：2025 原文逻辑本身不闭合。再生障碍性贫血、高血压、类风湿关节炎、慢性病毒性肝炎的 revision 必须设置 `execution_status=BLOCKED`。

C 类 entry 同时保存 `block_reason_code`、未决问题、受影响节点、所需审批角色和 resolution reference。事实归一与叶级 shadow 可以继续运行，Workbench 也可展示“现有证据看起来满足哪些叶子”，但不启动根节点聚合，`root_state` 保持 `null`，顶层不能给 `QUALIFIED` 或 `NOT_QUALIFIED`。不允许借用 2020 口径、常识、LLM 推断或本地默认关闭歧义。

专家书面消歧后必须创建新 revision，把书面解释作为受控 review evidence 关联到受影响节点，再完整走 source/schema/checksum/approval 门禁。这样线下工作只阻断四个 C 类根决定，不影响其余病种的规则化、候选筛选和 Workbench 试跑。

### 5. 先归一事实，再由 leaf policy 做确定性判断

慢病运行时从 `schema_manifest` 声明的内部契约一次性读取当前患者的诊断、文书、检验、检查和手术，形成不可变 `PatientClinicalSnapshot`。事实模型至少包含：

```text
fact_type / concept_id
raw_value / normalized_value
raw_unit / canonical_unit / conversion_id
assertion / polarity / certainty
observation_time / service_time / date_basis
source_domain / source_row_key / evidence_anchor
extraction_method / normalizer_version
deduplication_key / uncertainty_reason
```

具体原则如下：

1. 诊断编码和名称按版本化 alias/编码集合归一，保留主诊标志和原编码，不把诊断命中直接当作全部认定条件已满足。
2. 检验数值使用未舍入的十进制比较；只有 allowlist 中维度兼容、版本明确的单位转换可以参与阈值判断。缺单位、未知单位和非数值结果均为 `UNKNOWN`。
3. 检查/病理/文书先产出带原文 span、否定、时间和来源的候选 assertion。LLM 可提出候选，但没有患者级锚点或不能由确定性 resolver 验证的候选不能满足 leaf。
4. 重复测量按 specimen/report/event identity 去重，防止同一结果在结构化表和文书副本中被算两次；日期不可靠时不推断时间跨度。
5. 时间窗显式声明 anchor、日期字段、边界是否包含、最小/最大间隔。只有完整适用证据明确落在窗口外时才能 `NOT_SATISFIED`；仅缺第二次记录是 `UNKNOWN`。
6. 跨域计数先分别形成可追溯观察，再由 `AT_LEAST_N` 或专用 count leaf 聚合；不能因同义字段重复增加计数。
7. 跨就诊聚合只有在受控源提供经批准的稳定关联键时启用；一期不能安全关联的条件保持 `UNKNOWN`，pilot 优先选择单次可形成闭环证据的病例。

选择复用内部契约而不是直接在慢病模块查询 TB_*，是为了保持换院可移植性，并让 onboarding 数据可用性直接解释哪些 leaf 会落 `UNKNOWN`。

### 6. `CD01-CD20` 通过类型化规则接线，但 drafting/shadow 不进默认执行集

`Rule` 增加向后兼容的可选字段，例如：

```text
rule_kind: chronic_disease_qualification
clinical_criteria_ref: hlj-outpatient-chronic-2025/CD01
```

普通规则默认 `rule_kind=audit`，现有 YAML 无需修改。`Rule.rule_id`、`AuditResult.rule_id`、规则加载器、CLI 校验、公开文本清理、Workbench 元数据和测试统一接受 `CD\d{2,3}`；不能只修一处 regex。规则发现显式扫描 `R*.yaml`、`RD*.yaml` 和 `CD*.yaml`，并保持重复 ID 检查。

每个 `configs/rules/CD01-CD20.yaml` 保持 `status=drafting`，只携带召回关键词/编码、显示信息和 criteria ref；医学逻辑全部来自版本化资产，不能在 YAML prompt 中再复制一份。Runner 按 `rule_kind` 调用慢病 runtime，先加载受审资产，再归一事实、求值和投影；结构化路径不调用 LLM 最终裁决，也不经过只针对违规的 `precheck`、`verdict_gate` 或普通漂移防护。

新增 `JAVERT_CHRONIC_DISEASE_CRITERIA=off|shadow|on`，仓库默认 `off`。一期试跑要求显式 `shadow` 和显式 `--rules CDxx`/pilot 命令；`on` 仅预留，未经另一次上线审批不得把 CD 加入默认 ready/Router 集合。显式试跑仍校验 asset release、rule ref 和执行模式，不能靠 `--rules` 绕开知识审核门禁。

修改 ID 支持、状态、关键词或 Router 资产后按项目约束运行 `scripts/build_rule_mapping.py`，并验证 YAML 与 `data/router/javert_rules_index.json` 状态一致。由于 CD 是资格发现，普通违规 Router 和违规统计必须显式排除 `rule_kind=chronic_disease_qualification`，而不是依赖 ID 前缀的偶然行为。

### 7. 结构化结果使用独立 `clinical_criteria_json`

`AuditResult` 增加可空 `clinical_criteria_evaluation: ClinicalCriteriaEvaluation | None`；SQLite `audit_runs` 新增 `clinical_criteria_json TEXT NULL`，SQL Server `javert_audit_runs` 新增 `clinical_criteria_json NVARCHAR(MAX) NULL`。不得复用或嵌套进肿瘤 `eligibility_evaluation`/`eligibility_json`。

结构化结果至少冻结：

```text
schema_version / domain
rule_id / disease_id / disease_name
policy_version / release_id / disease_revision_id / asset_checksum
execution_status / evaluation_mode
root_state / qualified / qualification_disposition / legacy_verdict
proof_tree / shadow_proof_tree
missing_items / conflict_items / blocking_reasons / data_quality_flags
normalizer_version / evaluator_version / evaluated_at
```

`AuditResult` 写入前校验结构化 disposition 与兼容 verdict 一致，且同一 run 不能同时携带肿瘤 `eligibility_evaluation` 和慢病 `clinical_criteria_evaluation`。CD run 必须有慢病结构化 payload，包括 C 类 `BLOCKED`；知识加载或技术失败不得伪造临床 proof，应返回清晰的技术失败 `INCONCLUSIVE` 并阻止进入 pilot 合格池。

SQLite/SQL Server writer、reader、立即双写、pending 精确重试和 Workbench query 同步扩展该可空列。旧行读取为 `None`，不回填、不重判；旧列不删除，历史 `eligibility_json` 不转换。第一期 JSON 不建索引，常用统计只从受控 pilot 报告读取；等真实查询模式稳定后再决定是否物化列。

对内 AuditResult API、SSE 和 Workbench 接口新增同形状的可空字段，只加不删不改名；历史行返回 `null` 或兼容省略。pilot 阶段不主动扩大 2C v1/v2/v3 的业务契约；若共享序列化路径使 2C 实际暴露该字段，则必须同步所有相关 2C 契约文档并确认严格 DTO 允许新增/未知字段，不能出现同名字段在不同接口中形状不一致。

### 8. Workbench 以“资格评估”呈现，不复用违规卡片措辞

`clinical_criteria_evaluation` 存在时，Workbench 卡片主标题固定为“门诊慢性病认定条件评估”，主要状态显示：

- `QUALIFIED`：系统初判符合认定条件；
- `NOT_QUALIFIED`：现有明确证据不满足认定条件；
- `REVIEW_REQUIRED`：资料不足或证据冲突，需人工复核；
- `execution_status=BLOCKED`：标准存在待专家消歧，当前禁止自动认定。

卡片展示疾病/政策/revision、逐节点四态、决定性分支、阈值与时间计算、来源物理页、患者证据锚点、缺失材料和数据质量提示。C 类必须把 blocker 放在卡片首屏，shadow proof 明确标“仅证据预览，不构成认定结论”。证据跳转复用既有 patient-scoped anchor 机制，不在 JSON 中复制整篇病历。

普通 `CLEAN/VIOLATION/INCONCLUSIVE` badge、违规标题和违规统计不能覆盖慢病主语义。底层 legacy verdict 与现有审核字段继续保留，现有审核按钮/API 不删除、不改名；UI 通过上下文说明“认同/驳回的是本次系统初判”。`Chronic_Disease` 批次即使全部 legacy `CLEAN`，也必须在侧栏和批次筛选中可发现，不能被默认“仅风险”过滤隐藏。

选择专门卡片而不是套用肿瘤卡片，是因为慢病按 disease/patient 求值，不存在 drug/policy scope；选择保留 legacy 字段而不是新建第二套审核系统，是为了让一期试验继续使用现有 Workbench 生命周期，同时避免破坏旧消费者。

### 9. Pilot 采用宽召回、严格确认和同一批次的 5+45 两相提交

候选脚本分三层：

1. 召回层按病案首页诊断编码/名称和已审核 aliases 找候选；召回只决定“值得求值”，不代表符合。
2. 求值层对候选运行冻结的 criteria release；只允许 `execution_status=EVALUATED`、`root_state=SATISFIED`、`qualified=true` 且无技术/来源门禁错误的病例进入最终池。C 类 `BLOCKED` 和高敏感病种默认排除。
3. 抽样层按病种、替代分支、证据域和时间/计数能力做覆盖优先的确定性排序。先覆盖可执行病种，再覆盖不同 OR/AT_LEAST_N 分支，最后补足数量；不能用重复患者或较弱结果硬凑 50。

试验目标是 5+最多 45，而不是未经检查一次写满：

- Phase 1 选 5 个尽量来自不同病种/事实形态的 `QUALIFIED` 病例，写入后验证本地回读、142 精确同步、Workbench 卡片、证据跳转、批次可见性和 PHI 扫描。
- 只有 Phase 1 全部门禁通过，Phase 2 才从同一冻结候选池追加最多 45 个不重复病例。若合格池不足，报告实际数量和覆盖缺口，绝不降标准补数。

全部结果使用精确 `batch_tag=Chronic_Disease`。为允许 5+45 安全追加而又防止混批，每条 JSON 同时记录 `pilot_id`、`phase`、criteria release/checksum、source snapshot fingerprint 和 selection manifest checksum。Phase 1 的 batch ownership 预检必须发生在读取患者正文和写入结果之前；若该 tag 已存在未知 ownership，立即停止。Phase 2 只允许在现有行全部属于同一 pilot、同一 release/checksum、数量与 Phase 1 manifest 一致且新 run key 完全不重叠时追加。重跑使用稳定 replay/selection key；不得覆盖既有结果，也不得把其他任务写进同一 tag。

同步只处理本次明确 run ID，不调用无范围的 `sync-to-mssql --pending-only`。完成后对账本地行数、142 行数、唯一 patient/rule 数、各 disposition、各病种/分支覆盖、sync state 和失败清单。患者 ID、未盐化 ownership/replay key 和原始病历不写入 Git 报告；仓库只保存聚合 QA 和合成夹具。

### 10. 隐私、安全和敏感病种默认 fail closed

真实候选筛选只读受控数据源，数据库凭据、salt 和 owned database 声明只从环境或受控 env 文件读取，不进入命令参数、manifest、日志或提交。含 PHI 的临时目录使用 `0700`、文件使用 `0600`，正常和异常退出都清理；日志只输出规则、计数、阶段和去标识错误码。

pilot 的私有 selection manifest 只保存在受控运行目录，Git 中只保留合成 patient ID、聚合覆盖率和 checksum。Evidence anchor 采用当前系统内可回到来源的最小 locator/短 span，不复制整份患者文书。高敏感病种（至少包括 HIV 相关条目）在候选层默认禁用；只有明确授权、受控运行环境和访问审计同时满足才可单独试跑，并不得与普通 `Chronic_Disease` 批次混在一起。

选择默认排除而不是事后脱敏，是因为候选名单本身即可泄露敏感诊断；选择 salted fingerprint 而不是明文 ID manifest，是为了既能做幂等/ownership 对账，又不把患者身份扩散到报告。

### 11. 验证分为知识、算法、接线和真实试验四层

实现门禁至少包括：

- 知识层：2025 恰好 `CD01-CD20`、ID 稳定、单根、无跨版本节点、物理页/片段/checksum 完整、审批和 C 类 blocker 合法、重复构建字节一致。
- 算法层：AND/OR/AT_LEAST_N 全真值表，数值边界和单位转换，重复结果去重，时间窗边界，缺失不作阴性，冲突不静默覆盖，blocked shadow 不形成资格。
- 接线层：`CD` ID 在 Rule/AuditResult/loader/CLI/UI 全链路可读，drafting 不进默认集合，普通 `R/RD` 回归不变；SQLite/SQL Server 新旧行、双写/重试、API/SSE 只加字段、Workbench 卡片均通过。
- 试验层：5 例门禁后才允许 45 例；最终池 100% `QUALIFIED`，无 C 类/未授权高敏感病例、无重复 ownership、两端计数一致、Workbench 可发现且普通违规统计增量为 0。

选择分层门禁而不是只跑端到端，是为了能区分“标准编错、事实取错、聚合错、落库错和展示错”；任何一层失败都不得用下一层成功掩盖。

## Risks / Trade-offs

- [扫描 OCR 误字会改变阈值或逻辑] → OCR 只产候选；逐页人工复核原文，记录物理页、片段 checksum 与 PDF checksum，checksum 改变即阻断 release。
- [A/B/C 可行性被误当成患者结论] → 覆盖分类只控制自动化能力；患者仍逐节点四态求值，A/B 也可因缺数据落 `REVIEW_REQUIRED`。
- [C 类 shadow 看起来“全满足”而被误认定] → `execution_status=BLOCKED` 位于最终投影之前；Workbench 首屏显示 blocker，API 固定 qualified=null，持久化校验拒绝任何自动资格。
- [同名检验、单位或时间解析造成边界误判] → 只用版本化 allowlist、未舍入数值和显式日期策略；未知单位/日期 fail closed 为 `UNKNOWN`。
- [一次就诊数据无法证明多年慢病史] → 不擅自跨患者键拼接；无稳定关联或历史跨度不足时保持 `UNKNOWN`，pilot 不拿弱证据凑数。
- [LLM 候选抽取漂移] → LLM 不写状态；候选必须带患者锚点并由确定性 resolver 验证，重复运行以结构化 proof checksum 检查稳定性。
- [复用旧 verdict 导致“合规/违规”语义污染] → QUALIFIED/NOT_QUALIFIED 均映射 CLEAN，CD 卡片和统计按 `rule_kind` 分流；任何 CD run 排除于违规 KPI。
- [独立 JSON 增加存储与 API 复杂度] → 使用单个 nullable blob、旧行不回填、写前强校验和成对迁移；暂不引入关系表和索引。
- [5+45 追加造成同 tag 混批或重复] → 冻结 pilot manifest、release/checksum 和 source fingerprint，Phase 2 验证既有 ownership 后才能追加，范围外同步一律禁止。
- [约 50 例病种分布不均] → 使用覆盖优先配额并报告空缺；样本用于工程/展示验证，不声称临床敏感度、特异度或总体准确率。
- [高敏感诊断泄露] → 默认从普通 pilot 排除，私有 manifest 权限收紧，Git/日志只保留去标识聚合；授权试跑另建隔离批次。
- [共享 clinical criteria 内核影响已上线肿瘤链] → 只抽取最小兼容类型并保留 oncology re-export；先跑全部 oncology 合同/历史 JSON 回读回归，再接入慢病。

## Migration Plan

1. 冻结反馈 Excel 的逐病种、逐条件 A/B/C 结论；把需专家消歧项登记为 blocker，但不等待 C 类回复即可开始 A/B 类编译。
2. 建立 source manifest、2025/2020 独立 policy set、20 个疾病 revision 和 schema；人工核对扫描页，生成 checksum/覆盖/待审核报告。此时不接 Runner。
3. 抽取 `clinical_criteria` 最小共享合同和聚合器，先让现有 oncology import/re-export 与历史 `eligibility_json` 回归全绿，再实现慢病事实归一和 leaf evaluator。
4. 增加 `clinical_criteria_evaluation`、SQLite/SQL Server 可空列以及 writer/reader/sync/API 合同；先用合成结果验证旧行兼容和双写，不回填历史数据。
5. 增加 `CD01-CD20` drafting YAML、规则 ID/loader/CLI/Router 接线和慢病 Workbench 卡片。重建 Router index，但确认 CD 不进入默认执行集、违规统计和普通规则路由。
6. 在只读数据源上运行候选 dry-run，冻结 criteria release、source snapshot 和私有 selection manifest；报告各病种候选、四态和数据缺口，不写 Workbench。
7. 显式 shadow 执行 Phase 1 的 5 例，以 `Chronic_Disease` 写本地和 142，完成全链路/隐私/展示门禁。任一失败即停止扩批。
8. Phase 1 通过后，按同一 manifest 追加最多 45 例；对明确 run ID 做精确同步和双端对账，交专家在 Workbench 复核。
9. 本 change 完成时仍保持仓库默认 `off`、规则 `drafting`。是否解锁 C 类、扩大样本或转 ready/on 需要新的专家审批、验收报告和部署变更。

回滚分为三层：

- 运行回滚：关闭 `JAVERT_CHRONIC_DISEASE_CRITERIA`，停止 pilot/默认发现；普通规则不受影响。
- 代码/资产回滚：恢复上一个提交和上一个已审核 criteria release；保留历史 JSON 与 revision 引用，不用新知识重写旧结果。
- pilot 数据处置：默认保留已写结果作为审计证据并从 Workbench 筛选中隐藏。若因隐私或测试清理必须删除，只能先备份、验证 `batch_tag + pilot_id + run_id` 精确 ownership，并在用户明确授权后删除两端对应行；不得按宽泛 tag 或历史 pending 范围操作。私有临时文件无论成功失败均清理。

## Open Questions

- 四个 C 类病种各自未闭合连接词/适用范围的书面解释由哪一科室、哪一角色最终批准，resolution evidence 保存在哪里？
- 2025 标准的正式生效日期、废止/过渡期和历史认定是否有补充文件；在未取得前，运行时只按当前已审核主口径，不自行推断。
- 本地数据是否提供可依法使用的跨就诊稳定患者关联键，以及允许的回溯窗口；若没有，哪些跨期 leaf 永久保持人工复核？
- 检验项目代码、单位别名和检查术语的医院级映射由检验科/信息科谁维护，首批 release 的审批频率如何？
- `Chronic_Disease` 试验是否需要包含未满足/资料不足的对照病例；当前用户目标只选择约 50 个 `QUALIFIED`，对照集应另建独立 tag，不能混入本批。
- 哪些病种应按高敏感数据治理，授权、审计和隔离批次的具体责任人是谁？在答案明确前至少 HIV 相关病例默认关闭。
