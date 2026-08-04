## Why

Javert 已有肿瘤医保资格条件树运行时，但 156 条医保限定仅有少量分支完成结构化审批，指南适应证、病理条件和治疗方案也缺少专家可批注、可反复修订、可安全发布的生产流程。现在需要优先把现有文件中信息最完整的肿瘤药候选全集转成可维护知识，并通过受控 Excel 表单和 142 SQL Server `[知识库_work]` 建立从候选、专家审核到不可变发布版本的闭环。

同时，现有 RD10-RD37 精选单药规则虽然与 bulk 知识在“药品 + 规则类型”上有覆盖关系，但其 `prompt_addon`、特殊临床推断、别名、证据提示和回归样例不一定已被 bulk 行完整表达。规则退出默认执行不等于知识可以退役；本 change 必须先建立逐条知识原子清单和迁移覆盖门禁，避免以 `abandoned` 状态静默丢失个性化知识。

## What Changes

- 第一期以肿瘤药为完整建设范围，联合医院 2026-06 药品总库、2025 国家药品目录、2025《新型抗肿瘤药物临床应用指导原则》、现有肿瘤 JSON 资产和规则 YAML 建立候选全集与来源覆盖矩阵；任何来源只要存在的肿瘤产品、限定、指南段落或既有结构化知识都必须进入明确分区。
- 生成两份独立、可回导的专家审核工作簿：`肿瘤药指南适应证与医保限定条件树KB` 和 `肿瘤治疗方案组成KB`；专家只编辑显式审核列，机器主键、来源原文和候选值受保护。
- 将医保限定标为 `INSURANCE_PAYMENT`、现有《新型抗肿瘤药物临床应用指导原则》标为 `GUIDELINE_INDICATION`，预留未来的 `NMPA_LABEL`，禁止把指南冒充法定说明书。
- 将适应证拆成来源、规则、OR 分支和 `ALL/ANY/LEAF` 条件节点，第一期覆盖疾病、人群、标志物、既往治疗、线次、治疗状态、联合用药和资格时间窗口，不覆盖剂量、禁忌症、相互作用和安全性规则。
- 将治疗方案拆成规范名称、癌种上下文、别名和组成药品；给药剂量、途径、给药日、周期和阶段字段在 schema 中预留但一期不参与审批或推理。
- 在草稿层把规则生效起止日期设为可人工修改字段；导入时验证日期合法性和版本重叠，发布后版本不可原地修改，只能基于旧版创建新草稿并重新审批。
- 当前知识统一声明 `2026-01-01` 至 `2027-12-31`；2026 年以前病例仍使用当前已发布规则自动裁决，同时记录越界状态、实际就诊日、规则/来源版本并展示“核查当期指南/医保限定”警告。
- 在 142 `[知识库_work]` 中建立知识库 staging、authoring、review 和 release 表及幂等上传/发布流程；任何写入前必须显式通过 owned database 校验、连接预检和 dry-run，禁止复用 `TP_data_hub` 或 `sh_yb_platform` 的隐式默认值。
- 从已批准 release 确定性编译现有 Javert JSON 资产，保留 schema、checksum、生效期和审核状态闸；未批准、来源不完整或结构不合法的条目不得进入自动裁决。
- 把 RD10-RD37 及后续精选单药规则拆成可追踪的“知识原子”，按 `SOURCE_RULE`、`CLINICAL_EXTENSION`、`EVIDENCE_POLICY`、`NORMALIZATION`、`DOCUMENTATION_GUIDANCE`、`REGRESSION_GOLD` 分类，并映射到条件树、药品/别名字典、求值策略、审核提示或回归用例；仅有同药同类型 bulk 行不得视为迁移完成。
- 取消“RD10-RD37 必须统一 abandoned”的绝对前提：精选规则在默认运行时仍不得与 bulk 重复执行，但只有知识原子 100% 已映射且回归等价时才能标记为 `abandoned`；迁移未完成的规则改为或保持非默认执行的 `drafting` 并显式标记迁移缺口，不能从知识资产和回归集合中消失。
- 固定运行时所有权：RD04 作为肿瘤资格专用 bulk，承载肿瘤医保支付与指南适应证两个 `policy_scope`；R007/RD01/RD02 不再重复承载已迁入 RD04 的肿瘤候选；RD03 继续独立处理禁忌/安全语义，不并入资格树。项目不建设一个混合所有药品、所有语义的超级 RD。
- 增加导入报告、逐行错误回传、发布差异、覆盖率、不变量和回滚清单，保证 Excel、SQL Server 与 Git 中的运行时资产可追溯对账。

## Capabilities

### New Capabilities

- `oncology-kb-expert-authoring`: 两份专家工作簿、稳定行键、结构化条件节点、显式批注字段、导入校验和审核状态机。
- `oncology-kb-lifecycle`: 来源溯源、可人工维护的有效期、版本冲突检测、不可变 release、历史日期应用策略和发布差异。
- `oncology-kb-sqlserver-storage`: 142 `[知识库_work]` 的受控建表、staging 上传、事务性入库、owned database 门禁、对账和回滚。
- `oncology-regimen-authoring`: 治疗方案规范名、别名、癌种上下文、组成药品及未来给药日程字段的维护与审批。
- `drug-curated-knowledge-preservation`: 精选单药规则的知识原子登记、分类、目标映射、覆盖门禁和回归保全，确保规则执行状态变化不造成知识丢失。

### Modified Capabilities

- `drug-audit`: RD04 从批准的知识 release 加载肿瘤医保与指南条件树；通用 bulk 与 RD03 保持分工；精选单药规则只有在知识迁移门禁通过后才能退出为 abandoned，并对 2026 年以前病例执行“当前规则正常裁决 + 时间越界警告”的兼容策略。

## Impact

- 数据资产：现有 `configs/oncology_drug_kb.json`、`oncology_eligibility_rules.json`、`pathology_biomarker_kb.json`、`oncology_regimen_kb.json` 及其构建器将改为从已批准 release 编译或校验。
- 新增工件：两份 `.xlsx` 专家模板、字段字典、导入报告、SQL Server DDL、迁移/上传/发布 CLI 或脚本，以及不含患者数据的示例夹具。
- 数据库：目标为 142 `[知识库_work]`；实施前必须由所有者把该库加入受控 owned database 配置并授予明确写权限，否则所有建表、上传和发布操作 fail closed。
- 运行时：RD04 独占肿瘤资格的医保/指南 scope，R007/RD01/RD02 排除对应肿瘤候选，RD03 保持安全语义独立；旧 `eligibility_json` 不回填，每次新审计记录实际知识 release、来源版本和时间适用性。
- 规则资产：RD10-RD37 不进入默认执行集和 router 可执行索引，但不再按编号整体判定知识已退役；每条规则都要有可审计的迁移状态、目标实体和回归证据。
- 运维与文档：更新知识维护 runbook、部署说明、README、架构说明、专家操作指南和 CHANGES；不在 Git、Excel 示例、日志或上传报告中写入真实患者信息或数据库凭据。
