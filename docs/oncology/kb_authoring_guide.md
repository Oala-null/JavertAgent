# 肿瘤知识库专家填写与交回说明

本说明面向肿瘤临床、药学、病理和医保审核专家。专家填写材料是两份离线 Excel 审核投影，
不是发布中的知识真相源；填写、校验和交回均不连接 142，也不会自动批准、发布或部署任何知识。
截至 2026-07-22，同一 generated DRAFT 已物化到 142 `知识库_work`，可通过中文审核视图只读
审阅；它仍不是专家批准或运行时 release，实库边界见
[`authoring/142_draft_seed_import_report.md`](authoring/142_draft_seed_import_report.md)。

## 交付物

| 文件 | 审核范围 |
|---|---|
| `outputs/add-oncology-kb-authoring/肿瘤药指南适应证与医保限定条件树KB.xlsx` | 医保支付限定、指南适应证、条件分支/节点、有效期建议和肿瘤精选知识保全 |
| `outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx` | 方案规范名、别名、癌种上下文和组成药品/药物类别 |

每份工作簿都带 `00_使用说明`、`01_批次元数据`、独立专家审核 sheet 和 QA
sheet。来源及行数不要从本页抄录，以 [来源覆盖矩阵](authoring/source_coverage_matrix.md)
中的动态查询为准。

2026-07-21 这版工作簿已把全部肿瘤医保/指南来源 revision 投影为可筛选的来源、分支、
条件节点和专家审核行。建议先在 `08_QA问题` 处理未解析药物概念、药物类别和病理上下文，
再回到 `04_适应证分支` 与 `05_条件节点` 逐条核对；机器 JSON 只用于稳定回导，不要求专家
直接阅读或编辑。

## 开始前

1. 先确认文件名、`template_schema_version`、`export_batch_id` 和
   `source_snapshot_checksum` 与本次交付清单一致；不要把旧版审核结果粘贴到新版机器列。
2. 只编辑黄色、未锁定的专家列。不得删除、改名或解锁 sheet，不得修改稳定 ID、来源
   原文、机器枚举、聚合频次、`source_checksum` 或 `row_checksum`。
3. `GUIDELINE_INDICATION` 是临床应用指导原则，不是法定药品说明书。当前没有产品级
   法定说明书来源，禁止把任何行改为预留值 `NMPA_LABEL`。
4. 工作簿不得出现患者号、姓名、住院号、原始病历、患者级定位信息、run/ownership ID、
   数据库地址、账号、密码、连接串或本机绝对路径。

## 审核决定怎么填

优先在条件树工作簿的 `06_专家审核`、方案工作簿的 `07_专家审核` 逐行填写。
`entity_id` 已把意见稳定关联到原候选，不要改动。支持四种决定：

| `review_decision` | 何时使用 | 必填内容 |
|---|---|---|
| `APPROVE` | 原候选完整、准确，可按原值保留 | `expert_comment`、`reviewer_id`、`reviewed_at` |
| `APPROVE_WITH_EDIT` | 结论可接受，但字段需要明确修订 | 上述三项，加 `expert_value` 和 `evidence_reference` |
| `REJECT` | 来源不支持、候选错误或不应纳入 | 说明拒绝理由、审核人和时间 |
| `UNABLE_TO_DETERMINE` | 现有证据不足或需其他专业确认 | 写清缺什么证据、由谁补充、审核人和时间 |

- `reviewer_id` 填医院分配的审核账号或受控语义化 ID，不填患者标识。
- `reviewed_at` 使用带时区的 ISO 8601，例如 `2026-07-21T14:30:00+08:00`。
- `evidence_reference` 填文档名、页码或稳定锚点，不粘贴患者病历。
- `expert_value` 要指明字段和值，例如
  `operator=IN; expected_value=HER2 IHC 2+或3+`，不要只写“已修改”。
- 同一实体不要在多个 sheet 填互相冲突的决定。后续复核会追加审核事件，不覆盖前次意见。
- 审核 sheet 的只读 `source_checksum` 是本次意见绑定的 typed 内容指纹。回导时它必须与
  branch/node/regimen/alias/context/component 当前内容完全一致；若候选后来被修改，旧意见会
  失效，必须针对新 checksum 重新审核，不能复制旧决定绕过复核。

留空表示“尚未审核”，不是默认认可。`REJECT`、`UNABLE_TO_DETERMINE`、未解决的
`unsupported` 或歧义候选都不能进入 release。

本地 DRAFT 预览即使某个分支在患者证据上求值为 `SATISFIED`，也会强制输出
`REVIEW_REQUIRED / DRAFT_RULE_PREVIEW_ONLY`。这用于提前发现解析、映射和证据接线问题，
不能代替本节的人工审核决定。

## 条件树工作簿

建议按以下顺序查看：

1. `03_来源原文`：核对医保条款或指南原文及页码/稳定锚点。
2. `04_适应证分支`：确认一个来源规则被拆成的 OR 分支是否完整，尤其注意编号、并列
   适应证和“联合或不联合”等句式。
3. `05_条件节点`：逐叶核对疾病、分期、病理标志物、既往治疗、线次、联合用药和时间窗。
   `ALL/ANY` 是树结构，负向语义应由叶子 operator 表达。
4. `08_QA问题`：优先处理 `error` 和 `review`；未知阈值、来源缺口和
   `unsupported` 不能靠猜测改成 approved。
5. `09_肿瘤知识保全`：确认 RD10–RD37 原规则中的临床扩展、证据策略、归一化和金标用例
   是否有可验证目标。`MAPPED` 只表示已有映射，不等于 `VERIFIED`。

精选知识若需改目标，第一次选择 `APPROVE_WITH_EDIT`，在黄色结构化列中填写目标类型、目标
ID、`MAPPED` 和证据锚；回导会先落实映射但不会直接验证。随后必须保留同一目标，使用新的
`APPROVE` 决定和新的审核时间，系统核验数据库中已 materialize 的映射后才会升为
`VERIFIED`。一次编辑直接填 `VERIFIED` 会被拒绝。

如果建议修改生效期，不要解锁并覆盖机器日期列。在 `04_适应证分支` 填写黄色的
`date_override_reason` 和 `date_review_comment`，并在对应 `06_专家审核` 行选择
`APPROVE_WITH_EDIT`；`expert_value` 明确写出 `effective_from`、`effective_to` 和
`effective_date_basis=EXPERT_OVERRIDE`。起止日均为 inclusive，起始日不能晚于结束日。

## 方案工作簿

一期只审核方案身份、别名、癌种上下文和组成，不审核剂量、给药日、周期或治疗阶段：

1. 在 `02_方案主表` 核对规范名，在 `03_方案别名` 核对原始/归一写法。
2. 缩写只有在癌种上下文能唯一消歧时才能关联方案；不能消歧时选择
   `UNABLE_TO_DETERMINE`，不得任选一个方案。
3. 在 `04_方案上下文` 核对癌种、组织学和临床场景；不能从方案名反推患者诊断。
4. 在 `05_方案组分` 区分具体药品概念与药物类别，并保留 `REQUIRED`、`OPTIONAL`、
   `WITH_OR_WITHOUT` 的原意。
   药物类别不是自由文本：须同时审核条件树工作簿 `06_专家审核` 中的 `drug_class` 行；
   规范名或 match terms 不完整、未知、DRAFT 或未绑定当前 checksum 的类别不能批准和发布。
5. `06_预留给药字段` 一期不参与发布或推理，即使已有信息也不要把它当作发布批准依据。
6. 从临床文本聚合出的别名候选只提供归一别名、聚合频次和语料 checksum；不得追查、
   补录或转交患者级原文。

## 交回前本地校验

在仓库根目录执行；命令只读取工作簿并生成安全的逐行错误报告，不读取数据库配置：

```bash
.venv/bin/javert oncology-kb validate \
  outputs/add-oncology-kb-authoring/肿瘤药指南适应证与医保限定条件树KB.xlsx \
  --kind eligibility

.venv/bin/javert oncology-kb validate \
  outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx \
  --kind regimen
```

只有两条命令都返回 `valid=True`、`errors=0` 才能交给知识管理员。错误报告只给出安全
文件名、sheet、Excel 行号、稳定 ID、错误码和脱敏摘要；不要用截图或聊天消息代替修正后的
完整工作簿。

交回前再人工确认：

- 下拉值没有被手输成近似词；所有已填决定都带审核人、时间和意见；
- `APPROVE_WITH_EDIT` 有明确新值和证据锚点；日期变更有原因和日期审核意见；
- QA 中的阻断项没有被隐藏或删除；文件不含 PHI、凭据、连接信息和绝对路径；
- 文件保存到工作区外的受控目录，目录权限 0700、文件权限 0600，并按医院制度流转。

本地校验通过仍只表示“文件结构可回导”。后续上传、materialize、领域批准、release、
发布和部署是相互独立的受控步骤；在 DBA 对 142 `[知识库_work]`、备份策略、最小权限和
owned database 白名单给出明确批准前，必须停在本地工件阶段。

## 交回后的职责边界

专家交回工作簿后不自行执行发布命令。后续受控链路为：知识管理员上传并 materialize，
领域专家的 append-only 最新事件全部解决后，知识管理员逐个运行 `oncology-kb approve`
投影 typed revision 的 `APPROVED` 状态；`APPROVE_WITH_EDIT` 必须先落实编辑并再次审核，
不能直接批准。随后发布管理员运行只读 `release-authority`，把 source、curated、pathology
三个 checksum pin 记录到 authoring 库之外的审批单。

独立发布授权通过后，与领域审核人分离的 release operator 才能按
`release-build → release-publish` 执行。build 只在知识库登记确定性 `CANDIDATE`，不会把
candidate 写到本地 release 目录；publish 会用相同三个 pin 重建校验，且只有 publish 才写
不可变 `PUBLISHED` 四资产 bundle 和切换 active pointer。`release-rollback` 也只能切回已
校验的历史 published bundle，保留全部 revision、bundle 和事件；publish/rollback 的跨数据库
与本地指针失败会执行补偿，原参数重试前仍会重新校验。

完整参数、顺序和故障处理见 [`operations.md`](operations.md)。截至 2026-07-22，真实 142
已建立 authoring schema、23 个必需触发器与 6 个中文审核视图；generated DRAFT 的两次历史
失败物化均已全量回滚，最终修正版已完成 validate/preflight/服务端校验和 DRAFT 物化。专家批准、生产
publish/rollback 和 paired shadow 仍待外部门禁，详见
[`authoring/142_draft_seed_import_report.md`](authoring/142_draft_seed_import_report.md)。
