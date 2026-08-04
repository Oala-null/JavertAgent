## 1. 冻结一期合同与基线

- [x] 1.1 记录医院药品总库、国家目录、指南、现有肿瘤资产和规则 YAML 的 checksum；动态生成药品实体、医保限定、指南适应证、approved 条件分支、病理候选、方案和别名计数，并建立不手抄漂移数字的基线报告。
- [x] 1.2 定义并测试 `INSURANCE_PAYMENT`、`GUIDELINE_INDICATION`、预留 `NMPA_LABEL` 三种来源类型，确保当前任何条目都不能生成 `NMPA_LABEL`。
- [x] 1.3 定义知识逻辑 ID、revision ID、branch/node ID、source fragment ID、release ID 和 import batch ID 的稳定生成规则，并用重复构建测试证明 ID 稳定。
- [x] 1.4 定义 criterion type、operator、target kind、组合必选性、review decision、revision lifecycle、effective-date basis 和 temporal applicability 枚举及 Pydantic/JSON Schema。
- [x] 1.5 把当前默认有效期冻结为 `2026-01-01` 至 `2027-12-31`、basis 为 `CURRENT_FILE_ASSUMPTION`、历史策略为 `APPLY_CURRENT_RELEASE_WITH_WARNING`，并测试 source document year 与有效期互不覆盖。
- [x] 1.6 建立只含语义化 ID 和合成文本的最小知识夹具，覆盖多 OR 分支、嵌套 ALL/ANY、负向标志物、联合或不联合、药物类别、日期覆盖和方案别名歧义。
- [x] 1.7 为新增模型和枚举建立向后兼容测试，证明现有三条条件树、病理资产、四个方案和旧 `eligibility_json` 仍可读取。

## 2. 全量来源与条件树候选生成

- [x] 2.1 将医保和指南来源读取整理为统一的 `source_document/source_fragment/source_rule` 中间合同，保留文件、页码或稳定锚点、原文和 checksum。
- [x] 2.2 按产品、通用名、剂型、医保码和院内码生成共享药品概念/产品/code crosswalk，测试不把同通用名不同剂型或产品码错误合并。
- [x] 2.3 对全部 156 条医保限定和 69 条指南适应证执行确定性分支切分，保留编号、嵌套编号和原文 span；无法可靠切分的条目标为 `unsupported/needs_review` 而不是丢弃。
- [x] 2.4 实现条件候选生成器，覆盖疾病、组织学、分期、疾病状态、可切除性、人群、标志物、既往治疗、治疗数量/线次、治疗状态、联合用药、手术/放疗/移植适合性和资格时间窗口。
- [x] 2.5 实现联合用药目标解析，区分药品概念、药物类别和方案，并正确表达 `REQUIRED/OPTIONAL/WITH_OR_WITHOUT`。
- [x] 2.6 把现有病理候选与条件叶子通过 marker、method、cancer context、threshold 和 source restriction ID 关联，未知阈值保持待审且不生成 approved 断言。
- [x] 2.7 实现条件树结构 QA：每分支一个根、父节点存在、无环、同层顺序唯一、聚合节点有孩子、叶子无孩子、引用概念存在、来源锚完整。
- [x] 2.8 生成覆盖报告，证明每条医保限定和指南适应证恰好进入 approved/in-review/rejected/unsupported 分区之一，分区总数与来源总数一致。
- [x] 2.9 为复杂代表药品建立快照测试，覆盖帕博利珠单抗、替雷利珠单抗、维迪西妥单抗、维泊妥珠单抗、PARP 抑制剂和需要联合/既往多线/时间窗口的条目。
- [x] 2.10 以医院总库、国家目录、指南、现有肿瘤 JSON 和规则 YAML 取并集生成肿瘤候选宇宙，给每个候选记录来源成员关系、规范化状态和纳入/重复/排除/待审去向。
- [x] 2.11 生成跨来源覆盖矩阵并测试 hospital-only、national-only、guideline-only、asset-only 和 rule-only 候选均不会静默丢失，无法确认肿瘤归属的条目进入 needs-review。
- [x] 2.12 解析 RD10-RD37 的来源限定、prompt addon、expected signal、notes、别名行为和规则专属测试为稳定知识原子，按六类 atom type 归类并保留 source checksum。
- [x] 2.13 为每个知识原子建立 `CONDITION/DICTIONARY/EVALUATOR_POLICY/REVIEW_GUIDANCE/REGRESSION_CASE` 目标映射和 `DISCOVERED/MAPPED/VERIFIED` 状态；测试同药同类型 bulk 行不能单独提升迁移状态。
- [x] 2.14 生成 canonical 知识迁移 manifest 与人审报告，逐规则/atom type 对账 RD10-RD37，重复生成必须 ID、字节和 checksum 一致。
- [x] 2.15 对存在未验证原子的精选规则使用强制状态后退改为 drafting，并写入 migration-pending notes；只有全部原子验证通过的规则可保持 abandoned。
- [x] 2.16 在规则状态/notes 调整后运行 `scripts/build_rule_mapping.py`，验证 YAML、router index、默认执行集和知识迁移 manifest 一致，且 drafting/abandoned 精选规则均不可默认执行。

## 3. 条件树专家 Excel 工作簿

- [x] 3.1 定义 `肿瘤药指南适应证与医保限定条件树KB` 的 workbook schema、固定 sheet 名、列字典、必填规则、枚举、列宽、冻结窗格和模板版本。
- [x] 3.2 实现 `00_使用说明` 与 `01_批次元数据`，明确指南不是法定说明书、专家可编辑列、默认有效期、日期覆盖要求、回导步骤和批次/checksum 计数。
- [x] 3.3 实现药品产品、来源原文、适应证分支和条件节点 sheet，机器列保护、专家列配色、下拉验证、原文换行和一行一节点展示。
- [x] 3.4 实现专家审核 sheet，支持对分支、节点和有效期的 `APPROVE/APPROVE_WITH_EDIT/REJECT/UNABLE_TO_DETERMINE`，并要求 reviewer、时间、意见和必要证据。
- [x] 3.5 实现只读术语字典、QA 和 `09_肿瘤知识保全` sheet，展示未知概念、unsupported 条件、来源缺口、日期冲突、树结构错误、覆盖分区以及肿瘤精选知识原子的目标/验证状态。
- [x] 3.6 为工作簿生成器增加确定性测试和 openpyxl 回读测试，验证 sheet/列/验证规则/保护/稳定 ID/行 checksum/日期未丢失。
- [x] 3.7 对最终工作簿进行渲染或逐 sheet 视觉检查，修正截断、不可读列宽、冻结区域、筛选器和专家编辑提示后再交付。

## 4. 治疗方案组成专家 Excel 工作簿

- [x] 4.1 扩展方案 authoring 合同，定义 regimen revision、alias、cancer context、component、drug class 和 reserved schedule component。
- [x] 4.2 把现有 4 个 approved 方案和 121 个待审别名候选转为去标识候选快照，只保留归一别名、聚合频次、允许的上下文摘要和 corpus checksum。
- [x] 4.3 建立方案组分药品/药物类别 crosswalk，验证 R-CHOP、CHOP、R-GemOx、Pola-R-GemOx 等基线组分及 token 不互相污染。
- [x] 4.4 定义并生成方案主表、别名、方案上下文、方案组分、预留给药字段、专家审核和 QA sheets，复用统一批次和审核元数据。
- [x] 4.5 在预留给药 sheet 中创建 dose/unit/basis/route/days/cycle/max-cycles/phase/sequence 字段，但一期将其标为非必填、非发布、非推理字段。
- [x] 4.6 实现方案别名歧义 QA，确保同一归一别名在上下文无法消歧时保持 ambiguous，不自动选择方案或组分。
- [x] 4.7 为方案工作簿增加确定性、openpyxl 回读、PHI 扫描和视觉检查，证明不含患者号、原始病历、run/ownership ID。

## 5. 本地回导、审核和版本生命周期

- [x] 5.1 实现两个 workbook parser，只按 template schema、固定 sheet/列名和稳定 ID 读取，不依赖样式、行序或公式缓存。
- [x] 5.2 实现本地 `validate`，覆盖必需 sheet/列、枚举、日期、机器列 checksum、交叉引用、树不变量、方案歧义、重复键和隐私扫描，且不建立数据库连接。
- [x] 5.3 生成确定性的逐行错误报告，包含安全文件名、sheet、Excel 行号、稳定 ID、错误码和不泄露单元格原文的摘要。
- [x] 5.4 实现有效期校验：inclusive 起止、起始不晚于结束、同 logical rule 的可发布 revision 不重叠、正常 OR 分支共享日期不误报。
- [x] 5.5 实现日期人工覆盖规则：只允许 DRAFT/CHANGES_REQUESTED 修改；与导出默认不同则强制 `EXPERT_OVERRIDE`、override reason 和日期审核意见。
- [x] 5.6 实现 revision 状态机和 `supersedes_revision_id` 克隆，禁止原地修改 APPROVED/RELEASED/RETIRED 内容。
- [x] 5.7 实现 append-only review event 投影，保留历次认可、修订、驳回和无法判断，验证后续事件不删除前序意见。
- [x] 5.8 完成两类工作簿 export→import→re-export round-trip 测试，除允许变化的批次元数据外 canonical row payload 完全一致。

## 6. `[知识库_work]` SQL Server schema 与写安全

- [x] 6.1 编写 `[kb_stg]` 与 `[kb]` 的幂等 DDL，覆盖 import batch/row、来源、药品概念/产品/code、curated knowledge atom/mapping、规则 revision/branch/node、方案 revision/alias/context/component/schedule、review event、release 和 release item。
- [x] 6.2 为 DDL 增加主外键、唯一键、状态/枚举/date CHECK、append-only/immutability 所需约束和索引，并验证重复执行不删除或重建现有数据。
- [x] 6.3 复用并抽取现有 owned database guard，使所有知识写命令在连接前校验显式 `--database` 与 `JAVERT_OWNED_DBS`，未知库测试证明零连接尝试。
- [x] 6.4 增加连接后 `SELECT DB_NAME()` 精确校验和 principal/schema 权限预检，目标不一致时在第一条 DDL/DML 前失败。
- [x] 6.5 为 SQL DDL 增加 `sqlcmd -d` 与 `KB_DATABASE` 双重一致性校验，编写错库、缺变量和非 owned 库的回归测试。
- [x] 6.6 实现数据库 schema 版本表和幂等迁移入口，所有 mutating 命令要求 `--database 知识库_work` 且无默认数据库。
- [x] 6.7 使用临时 SQL Server、测试容器或存储适配器测试外键失败、事务回滚、日期重叠、重复 checksum 和 immutable revision，不连接真实 142。
- [x] 6.8 增加日志/异常脱敏测试，确保密码、连接 URL、env 内容、绝对客户端路径、Excel 原文和患者信息不出现在输出。

## 7. 受控上传与事务性 materialization

- [x] 7.1 增加 `oncology-kb` 命令组或等价运维入口，至少提供 `export`、`validate`、`preflight`、`upload --dry-run`、`upload`、`materialize`、`release-build`、`release-publish` 和 `release-rollback`。
- [x] 7.2 实现 preflight/dry-run，只做 owned target、`DB_NAME()`、权限、schema version 和预计计数检查，不创建 import batch 或写任何表。
- [x] 7.3 实现 staging upload，保存 workbook SHA-256、template version、安全 basename、上传者、计数和逐行 canonical payload/checksum，不保存 Excel 二进制和绝对路径。
- [x] 7.4 实现 `workbook_sha256 + template_schema_version` 幂等键，重复上传返回既有 batch ID，测试不重复创建 staging 行或 revision。
- [x] 7.5 实现服务端二次校验，使本地验证通过但服务端引用/日期/状态不合法的批次保持 VALIDATION_FAILED，正式表零写入。
- [x] 7.6 实现单事务 materialization，把完整有效批次写成 draft/in-review revisions；任一来源、树节点、方案组件或 review event 失败时全部回滚。
- [x] 7.7 生成 staging→authoring 对账报告，逐实体列出 staged/inserted/reused/updated-draft/rejected 数，任何计数或 checksum 不一致使批次失败。
- [x] 7.8 测试上传、materialize、approve、release 和 deploy 状态彼此独立，证明 upload 不会自动批准或发布知识。

## 8. Release 构建、JSON 编译和运行时接线

- [x] 8.1 实现 release candidate 构建器，只接受 approved immutable revisions，并要求领域审核人与发布操作者不同。
- [x] 8.2 实现 release 阻断检查：unsupported/needs-review/ambiguous/来源缺失/概念缺失/日期重叠/未来窗口缺失条目以及未 VERIFIED 的肿瘤精选知识原子不得进入发布。
- [x] 8.3 从 release 确定性编译 `oncology_drug_kb.json`、`oncology_eligibility_rules.json`、`pathology_biomarker_kb.json` 和 `oncology_regimen_kb.json`，写入 release/revision/source/date metadata 和 asset checksum。
- [x] 8.4 生成相对上一 release 的 diff/coverage manifest，列出新增、修改、退役、来源变化、有效期变化、approved/待审分区和精选知识迁移状态；非肿瘤 pending 必须显示为 drafting backlog，不能标成已退役，并验证重复编译字节一致。
- [x] 8.5 扩展运行时加载校验，拒绝非 published release、schema/checksum 不匹配、unsupported 条件和未审核条目；验证 142 离线时本地 JSON 审计仍正常。
- [x] 8.6 实现医保支付与指南适应证双 policy scope 的来源/状态关联，保持同 patient/drug/scope 唯一性并防止指南被显示成说明书。
- [x] 8.7 实现非对称日期选择：2026 年以前按 active release 自动裁决并记录 BEFORE_EFFECTIVE_WINDOW/警告；2026—2027 正常；最后有效期以后无新版则 REVIEW_REQUIRED。
- [x] 8.8 扩展 `eligibility_json`、API/SSE 和工作台为只加字段，显示 release、revision、policy scope、声明窗口、实际日期和时间警告，同时验证旧行/旧消费者兼容。
- [x] 8.9 更新 RD04/R007/RD01/RD02/RD03 ownership 和 router index：RD04 独占肿瘤医保/指南资格，R007/RD01/RD02 排除对应肿瘤候选，RD03 保持安全语义；RD10-RD37 按迁移门禁分别为 drafting 或 abandoned 且都不默认执行。
- [x] 8.10 实现 release rollback，切回上一发布 JSON/active 指针且不删除 revision、review/import history，并增加回滚后旧审计可读测试。

## 9. 首批专家文件与 142 受控落库

- [x] 9.1 用当前来源并集生成两份正式专家审核工作簿、肿瘤来源覆盖矩阵、全局精选知识迁移报告和各自 QA 报告，核对医保、指南、医院/国家产品、既有资产、规则原子、病理、已批准方案和别名候选计数。
- [x] 9.2 对交付工作簿做最终视觉 QA、公式/下拉/保护回读和 PHI/凭据扫描，并向专家提供一页式填写说明。
- [ ] 9.3 获取并记录 142 `[知识库_work]` 已由 DBA 创建、备份策略已确定、最小权限 principal 已授权及该库可加入 `JAVERT_OWNED_DBS` 的明确批准；未满足时不得执行后续真实写入任务。
- [ ] 9.4 在批准后用 `sqlcmd -d 知识库_work -v KB_DATABASE=知识库_work -b` 执行幂等 DDL，并核验 `DB_NAME()`、schema、表、约束、索引和权限实际状态。
- [ ] 9.5 对专家回传工作簿先运行离线 validate，修复全部模板、结构、日期、引用和审核字段错误，不在验证阶段连接 142。
- [x] 9.6 对 142 运行 preflight 和 `upload --dry-run`，保存不含凭据/原文的目标、checksum、计数和零 PHI 报告，经人工确认后再写。
- [ ] 9.7 先上传并 materialize 一个可回滚小批次，核对 staging、authoring、review event、日期字段和幂等重复上传，再处理剩余批次。
- [ ] 9.8 上传全部已验证工作簿，逐批完成 source/branch/node/regimen/component 对账；任何批次不一致则回滚该事务并停止后续批次。
- [ ] 9.9 在专家审批完成后创建首个 release candidate，编译 JSON、运行 shadow/回归并取得单独发布授权；不得把“已上传”当成“已发布”。
- [ ] 9.10 演练上一 release 回滚、staging 失败批次保留和数据库备份恢复，记录实际命令、计数与恢复时间但不记录凭据。

> 2026-07-22 技术事实（不计作上述外部门禁完成）：用户明确指定并重新授权 142
> `知识库_work` 后，最终 DDL 连续幂等执行两次，23 个触发器与中文视图均核验通过；两个
> 历史 generated DRAFT 探针 batch 均确认 typed 事务完整回滚且失败证据保留。最终修正版已通过
> validate/preflight/服务端校验，并以两个独立 batch 成功 MATERIALIZED 为待审 DRAFT；重复上传
> 均复用 batch。`review_event=0`、release=0，详见
> `docs/oncology/authoring/142_draft_seed_import_report.md`。

## 10. 验证、文档和交付门禁

- [x] 10.1 运行新增模型、候选并集/覆盖矩阵、知识原子提取/映射、workbook export/import、日期、review lifecycle、release compiler 和 runtime 的直接单元测试。
- [x] 10.2 运行 SQL Server 写安全、DDL 幂等、staging、事务回滚、幂等上传、对账和脱敏组合测试，报告原始 collected/pass/skip/fail/error。
- [x] 10.3 运行 oncology 专项组合测试和现有三例金标，增加 2025 历史自动裁决告警、2026 窗口内、2028 无新版 fail-closed 三类时间金标。
- [x] 10.4 运行一个完整离线端到端：五类来源并集→肿瘤候选/精选知识原子→两份 Excel→模拟专家修改→回导→本地 store/release→四个 JSON→RD04 双 scope 求值，并验证通用 bulk/RD03 无所有权冲突。
- [ ] 10.5 在获授权环境运行小批量 shadow，对比旧/新裁决、双 scope、时间警告、未知/冲突率和专家一致率，不用历史非配对结果冒充同输入性能。
- [x] 10.6 更新 README、`docs/how_javert_works.md`、`docs/oncology/operations.md`、`docs/oncology/qa_report.md`、专家维护指南、142 部署 runbook、2C/API 契约（如新增返回字段）和 CHANGES。
- [x] 10.7 更新命令/工具/资产库存的动态生成说明，避免在 AGENTS.md 或长期文档手抄新的易漂移数字。
- [x] 10.8 运行受影响模块测试、Web API/SSE 回归、存储旧行兼容测试和一个端到端 CLI；全量测试若受既有债务影响，按原始与排除后口径分别报告。
- [x] 10.9 运行 `openspec validate add-oncology-kb-authoring --strict`，逐项核对 proposal/design/specs/tasks 与真实实现、测试、Excel、数据库和运维事实一致后，才把 tasks 标记完成。
