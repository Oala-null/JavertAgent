## ADDED Requirements

### Requirement: Diagnosis shadow 必须以公共 Evidence Contract 为硬前置
系统 MUST 只在 `define-clinical-evidence-contract` 已完成 apply、严格校验和 conformance 验收后实现或启用 Diagnosis shadow；系统 MUST 使用其 `clinical-evidence-contract` 与 `clinical-ontology-validation` 对象、canonical serialization 和 validator，且 MUST NOT 在本 capability 内复制或放宽第二套 Evidence/Fact/Ontology 契约。

#### Scenario: 前置契约尚未验收
- **WHEN** 公共 contract change 未 apply、strict validate 失败、conformance tests 未通过或运行时版本不受支持
- **THEN** Diagnosis shadow 在读取患者数据前以安全错误码拒绝启动
- **AND** 不创建 accepted evidence event
- **AND** 现有审计、Router、存储和 API 继续按原行为运行

#### Scenario: 前置契约已冻结
- **WHEN** 公共契约的 schema、canonical serialization、ontology pack 和 validator 版本均已验收
- **THEN** Diagnosis adapter 直接产生该版本定义的 `CandidateAssertion` 及其 source/evidence/provenance 引用
- **AND** 不通过 slice 私有类型绕过公共验证

### Requirement: Shadow 必须默认关闭且与权威审计链隔离
Diagnosis shadow MUST 默认关闭，并且 MUST 只由显式 shadow command/job 调用；它 MUST 使用独立 store，MUST NOT 接入或修改 Runner、precheck、verdict gate、Router、`persist_one`、`audit_runs`、SQL Server 审计双写、Web SSE EventBus、2C v3 或任何旧审计结果字段。Shadow ledger 和 projection MUST NOT 具有裁决权。

#### Scenario: 运行普通患者审计
- **WHEN** 操作者未显式启用 Diagnosis shadow 而运行现有 CLI、Web 或 2C 审计
- **THEN** 不执行 Diagnosis shadow extractor、validation seam 或 ledger 写入
- **AND** 现有调用顺序、verdict、持久化与响应字段保持不变

#### Scenario: 显式运行 shadow job
- **WHEN** 操作者在已批准的本地或非生产环境显式启动 Diagnosis shadow job
- **THEN** job 只写独立 shadow ledger 和安全摘要
- **AND** 任意 accepted、conflict、unknown coverage 或 failure 结果均不写回权威审计链

### Requirement: 结构化诊断 extractor 必须从 canonical row 产生可定位候选
系统 MUST 提供接受显式 SourceArtifact metadata 与 canonical rows 的纯结构化 Diagnosis extractor，逐条读取 manifest 驱动的 canonical `diagnoses`/`shi_zd` row，并为非空、可解释的诊断产生 `Patient has_diagnosis Disease` `CandidateAssertion`。系统 MUST NOT 为此扩展 `DataLoader` 公共接口、manifest 输出列或 hub canonical schema。每个 locator MUST 至少包含 immutable `SourceArtifact` identity/version/checksum、canonical zero-based row ordinal、deterministic row fingerprint、实际消费的 field 和 field/value checksum；`ba_id`、`ipt_medcas_hmpg_sn` 等业务/合成序号只可作为非权威显示或辅助键，不得冒充上游 HIS primary key 或稳定 lineage identity。上游数据库、表、原始主键和字段 locator 只有在 ingestion 已保存可信映射时才可追加。

#### Scenario: 读取合法 shi_zd 行
- **WHEN** 合成 canonical row 含稳定 SourceArtifact version/checksum、row ordinal/fingerprint、诊断名称和 ontology pack 已声明的诊断编码
- **THEN** extractor 产生一个带 canonical row/field locator 和 value checksum 的 `CandidateAssertion`
- **AND** source、evidence、assertion 与 provenance 引用均可回到该 canonical row

#### Scenario: 上游 HIS locator 不可用
- **WHEN** canonical row 已存在但 ingestion 未保留 `ZYZDLSH`、`ZDXH`、上游表或原始主键
- **THEN** 候选仍以 immutable SourceArtifact version/checksum + canonical row ordinal/fingerprint + field/value checksum 满足 v0.1 lineage
- **AND** 不猜测、拼造或把合成序号标记为上游 HIS locator

#### Scenario: 未知或不合规诊断编码
- **WHEN** row 的编码不属于所声明 system/version 或无法由版本化确定性 mapping 解释
- **THEN** extractor 不把该编码冒充 ICD 或其他标准 `ConceptRef`
- **AND** 该行进入明确的 unmapped/rejected/coverage 结果而非伪造规范化诊断 Fact

#### Scenario: 兼容列重复承载同一诊断
- **WHEN** 同一 canonical row 的入院/出院兼容列保存相同编码和名称
- **THEN** extractor 按固定字段优先级产生一个候选并记录实际消费字段
- **AND** 不因列级重复制造两条假多证据

### Requirement: 文书诊断 extractor 必须保留 section 与精确字符 span
系统 MUST 提供与现有聚合格式分离的文书 Diagnosis extractor，复用受控诊断 section allowlist，并为每次诊断文本出现保留 immutable SourceArtifact identity/version/checksum、canonical zero-based document row ordinal、deterministic row fingerprint、section、相对 canonical 内容的 `[start_char,end_char)`、excerpt checksum 和 producer version。当前 canonical 数据没有 `WSLSH` 或其他可信 document primary key 时，系统 MUST 使用上述 canonical locator 定位，MUST NOT 伪造上游 document ID。内容完全相同但 canonical row ordinal 不同的两行 MUST 保留为两个 source occurrence；只有 artifact identity/version、ordinal 与 fingerprint 均相同才属于同一来源重放。现有 `note_diagnosis` 的文本、频次和阶段输出 MUST 保持兼容。

#### Scenario: 同一文书含两个诊断条目
- **WHEN** 合成“出院诊断”section 的 canonical 内容含两个可拆分诊断
- **THEN** extractor 产生两个各自带准确 start/end span 的候选
- **AND** 每个 span 截取的原文与候选 evidence excerpt 一致
- **AND** start 为 inclusive、end 为 exclusive

#### Scenario: canonical 文书没有上游 WSLSH
- **WHEN** 文书 row 只有 canonical source version/checksum、row ordinal/fingerprint、section 和内容
- **THEN** locator 仍能稳定回到该 canonical row 与 span
- **AND** 输出不声称存在未知的 HIS document primary key

#### Scenario: 两个内容相同的 canonical 文书行
- **WHEN** 同一 SourceArtifact version 内两个 row 的内容与 checksum 相同但 row ordinal 不同
- **THEN** extractor 保留两个独立 source occurrence 及各自 locator
- **AND** 不因全文相同把其中一行误判为幂等重放

#### Scenario: 旧 note_diagnosis 调用
- **WHEN** 现有工具调用相同文书 fixture
- **THEN** 其面向 Agent 的诊断文本、计数和 stages 输出与变更前一致
- **AND** shadow 的逐 span 数据不新增到旧工具公开文本中

### Requirement: 同步 validation seam 必须先校验再追加
系统 MUST 使用窄的同步 in-process validation/ledger seam 接收 `CandidateAssertion`，依次执行公共 schema/reference 校验和 ontology entity/predicate/domain/range/ConceptRef/version 校验；只有两级校验均通过的 canonical envelope 才可进入 accepted stream。该 seam MUST NOT 复用 Web SSE EventBus，也 MUST NOT 具有 Kafka、异步投递、网络重试或 eventual-delivery 语义。

#### Scenario: 合法诊断关系通过
- **WHEN** 候选主体类型为 Patient、predicate 为 `has_diagnosis`、对象类型为 Disease，且所有 source/evidence/provenance/version 引用完整
- **THEN** seam 在 schema 与 ontology 校验成功后原子追加 accepted envelope
- **AND** 返回 accepted outcome 与稳定 event/idempotency identity

#### Scenario: 非法关系被拒绝
- **WHEN** 合成候选声明 `Medication has_diagnosis Disease`
- **THEN** ontology domain 校验在 ledger append 前确定性拒绝该候选
- **AND** accepted stream 不含该候选的 Fact 或 Assertion
- **AND** rejection 只记录安全错误码和版本元数据

#### Scenario: schema 引用不完整
- **WHEN** 候选缺少 EvidenceItem、SourceArtifact、ProvenanceActivity、双时间或 ontology version 的强制引用
- **THEN** schema/reference 校验失败
- **AND** ontology validator 和 accepted append 均不执行

### Requirement: Shadow ledger 必须独立、追加式且可回放
系统 MUST 通过窄 `ShadowLedger` repository seam 将 accepted envelope 追加到独立本地 ledger；首个实现 MUST 使用无需外部服务的 SQLite adapter，并将 accepted、validation rejection 与 technical diagnostic 分为独立 append-only stream。系统 MUST NOT 提供原位 update/delete/upsert Fact 的运行路径，projection MUST 能从 ledger 重建且不得成为唯一事实源。

#### Scenario: 追加合法 envelope
- **WHEN** validation seam 提交一个新的合法 envelope
- **THEN** ledger 在单个事务中追加该 envelope 及其版本/幂等元数据
- **AND** 不更新或覆盖任何既有 Source、Evidence、Assertion 或 Fact 事件

#### Scenario: 重建 Diagnosis projection
- **WHEN** 删除可重建 projection 并从 cursor 0 重放 accepted stream
- **THEN** 重建后的规范化 Fact、evidence refs、conflict 与 coverage 摘要与原 projection 一致
- **AND** 不读取 `audit_runs`、SQL Server 或 2C 响应补造事实

#### Scenario: 真实数据 store 权限
- **WHEN** 在获批环境创建可能含患者级 evidence 引用的 ledger
- **THEN** ledger 目录权限为 0700 且数据库文件权限为 0600
- **AND** 路径、凭据和盐只从受控配置/环境注入，不进入仓库、命令参数或日志

#### Scenario: ledger 存在损坏记录
- **WHEN** replay 读取到 checksum/serialization 不合法或 reference 已损坏的 accepted row
- **THEN** projection 停止把该 row 作为临床记录接受并返回稳定 technical failure
- **AND** 不跳过损坏后仍把 projection 报告为完整成功
- **AND** 不修改权威审计结果或原位修复历史 row

### Requirement: 重放必须幂等且来源修订不得覆盖历史
系统 MUST 以公共 canonical serialization 计算稳定幂等键，至少绑定 contract schema version、ontology version、producer/version、SourceArtifact identity/version/checksum、locator 和 candidate semantic payload。完全相同输入版本重放 MUST 返回 duplicate 且不得新增 accepted event；来源 checksum、版本或语义发生变化时 MUST 追加独立新事件，MUST NOT 原位覆盖旧事件。只有可信 ingestion metadata 显式提供 `revision_of` 或撤回语义时，系统才可按公共契约表达 supersedes/retracts；`derived_from` 只用于 Inference，系统 MUST NOT 从内容变化自行推断版本关系。

#### Scenario: 同一输入重放两次
- **WHEN** 对同一个 fixture、source version、ontology version 和 producer version 连续运行两次 shadow job
- **THEN** 第二次只增加 duplicate outcome 计数
- **AND** accepted ledger event 数、事件内容和 projection 均保持不变

#### Scenario: 文书内容发生修订
- **WHEN** 相同 logical document 的 SourceArtifact version/checksum 与诊断 span 内容发生变化
- **THEN** 系统追加新版本 source/evidence/assertion 事件
- **AND** 旧版本仍可回放
- **AND** 若没有可信 `revision_of`/撤回 metadata，则不猜测新旧 Assertion 关系；若存在则按公共 contract 显式记录而非覆盖旧行

### Requirement: Projection 必须保留多证据、严格冲突边界与 UNKNOWN coverage
Diagnosis projection MUST 只使用 Diagnosis slice 自有、版本化、确定性的 terminology mapping 将同义文本映射到相同 `ConceptRef.system/code/version`，并 MUST 保留每条独立 Assertion 和 Evidence 引用；该 mapping MUST NOT 扩张 Ontology v0.1 语义，`ConceptRef.display` MUST 只用于展示，MUST NOT 覆盖 identity 或合并不同 code。只有同一规范化命题、适用时间范围重叠的 `Assertion.value=TRUE/FALSE` 才可生成 Conflict；Ontology v0.1 不提供 disjoint 推断，不同诊断、未知映射或技术失败 MUST NOT 自动标为临床冲突。`UNKNOWN` MUST 是绑定实际检查 SourceArtifact、时间 coverage 和 uncertainty reason 的 Assertion/coverage 状态，MUST NOT 创建名为“Unknown”的诊断实体，也 MUST NOT 把缺失证据投影为 FALSE；UNKNOWN Assertion MUST NOT 伪造 `supports`/`contradicts`。

#### Scenario: 结构化全称与文书同义词
- **WHEN** `shi_zd` 声明“甲状腺乳头状癌”且文书 span 声明由同一 terminology version 映射的“PTC”
- **THEN** projection 展示同一规范化 diagnosis Fact 的两条独立支持链
- **AND** ledger 保留两条 Assertion、两个 EvidenceItem 和各自 locator

#### Scenario: 同一命题正反声明
- **WHEN** 结构化来源 affirm 某规范化 diagnosis，而文书 evidence 明确 negate 同一 subject/time/concept 命题
- **THEN** projection 产生可追溯 Conflict 并保留双方证据
- **AND** 不按 source 优先级静默删除任一声明

#### Scenario: 患者有两个不同诊断
- **WHEN** 两个 accepted Assertion 指向两个不同、且未由 terminology mapping 归一为同一命题的 diagnosis concept
- **THEN** projection 将它们显示为两个并存诊断
- **AND** 不因 concept 不同自动生成 Conflict

#### Scenario: 文书只表达疑似诊断
- **WHEN** 实际检查的文书 source/time coverage 只表达可规范化命题“疑似 X/待排 X”且不足以声明 TRUE 或 FALSE
- **THEN** projection 保留该 proposition 与 `Assertion.value=UNKNOWN`、source/time coverage 和 uncertainty reason
- **AND** UNKNOWN Assertion 不生成 supports、contradicts 或 Conflict
- **AND** 不创建 Unknown Diagnosis concept

#### Scenario: 没有可形成命题的诊断证据
- **WHEN** 两个 source 在已记录的扫描范围内均无可规范化 diagnosis proposition
- **THEN** summary 报告 diagnosis coverage 为 UNKNOWN 并保留可追溯扫描范围与原因
- **AND** 不创建 Unknown Diagnosis concept 或 TRUE/FALSE Assertion

### Requirement: Shadow 技术失败必须隔离且与临床 UNKNOWN 分离
系统 MUST 在 structured source、note source、contract validation、ontology validation 和 ledger adapter 边界分类技术失败。单条失败 MUST NOT 阻止同 batch 后续合法候选；一个 source 失败 MUST NOT 阻止另一 source，但 run MUST 标记 `partial/failed` 而非完整成功。技术 failure MUST NOT 被投影为临床 UNKNOWN/Conflict，也 MUST NOT 修改任何权威 verdict 或结果行。

#### Scenario: 文书源不可用而结构化源可用
- **WHEN** note source 抛出受控读取异常且 `shi_zd` fixture 可正常处理
- **THEN** structured candidates 仍被验证和追加
- **AND** run 摘要标记 partial 与安全 source error code
- **AND** 不把 note 技术失败解释为患者无诊断

#### Scenario: ledger 写入失败
- **WHEN** 独立 shadow SQLite adapter 注入写入异常
- **THEN** shadow command 返回非零或 partial/failed outcome
- **AND** 现有 audit fixture 的 verdict、持久化内容和 API projection 字节级不变
- **AND** 不通过 `persist_one` 或 SQL Server 兜底写入 shadow payload

#### Scenario: 并发追加遇到锁冲突
- **WHEN** 两个 shadow writer 并发提交相同或不同 envelope 并触发 SQLite lock/busy failure
- **THEN** 已提交事务保持完整，相同幂等键至多存在一个 accepted event
- **AND** 未提交 writer 返回稳定 technical failure/duplicate 而不写部分 bundle
- **AND** 该故障不影响 audit SQLite 或 SQL Server 事务

#### Scenario: 安全诊断输出
- **WHEN** extractor、validator 或 store 失败并生成日志/CLI/报告
- **THEN** 输出只含 synthetic case ID 或安全 token、source kind、版本、计数、稳定错误码和耗时分桶
- **AND** 不含患者号、诊断/文书原文、source locator、span excerpt、SQL、凭据、完整 envelope 或未盐化 run/ownership ID

### Requirement: Golden harness 必须离线覆盖首个垂直切片
系统 MUST 提供只使用合成、去标识 fixtures 的确定性 golden harness，并 MUST 覆盖结构化与文书同义诊断、多证据、同一命题矛盾、UNKNOWN coverage、非法关系、幂等重放以及 source/validator/store 技术失败隔离。Harness MUST 禁止访问 LLM、网络、SQL Server、142/243 hub 或任何生产数据库；相同输入重复运行的 canonical ledger/projection MUST 一致。

#### Scenario: 运行完整 golden suite
- **WHEN** 在无网络、无生产配置的测试环境执行 Diagnosis shadow golden harness
- **THEN** 所有必需语义和故障案例均被收集并给出稳定 pass/fail 与安全错误码
- **AND** 规范化结果不包含时间戳、随机 ID 或运行顺序造成的漂移

#### Scenario: fixture 含禁止标识或原始病历
- **WHEN** fixture 静态检查发现真实患者号、姓名、原始病历、数据库凭据或未盐化 run/ownership 标识
- **THEN** harness 在回显字段值前失败并返回非零
- **AND** 禁止内容不写入报告、snapshot 或 ledger fixture

### Requirement: Shadow 数据生命周期必须明确且异常退出可清理
系统 MUST 对 synthetic harness 临时目录使用 0700、临时文件使用 0600，并在成功与异常退出时关闭连接、回滚未提交事务和清理临时资源。v0.1 MUST NOT 实现自动 retention/delete/compaction。真实患者 shadow MUST 保持 disabled，直到受控环境形成并明确批准 store location、owner、retention period、备份和 sealed ledger/segment 整目标销毁流程；系统 MUST NOT 以更新或删除单条 Fact 伪装 append-only history cleanup。

#### Scenario: Golden harness 成功或失败退出
- **WHEN** harness 创建临时 ledger 后正常完成或因注入异常终止
- **THEN** 临时 PHI-classified 目录和文件均被清理
- **AND** 仅保留不含患者原文的安全汇总

#### Scenario: 未配置真实数据 retention
- **WHEN** 操作者尝试对真实患者 source 启用 shadow，但未声明经批准的 location、owner 和 retention policy
- **THEN** command fail closed 且不读取患者数据、不创建 ledger
- **AND** 普通审计服务继续运行

#### Scenario: 运行中异常退出
- **WHEN** shadow job 在事务中因抽取、验证或 store 异常退出
- **THEN** 未提交事务被回滚且连接和临时资源被关闭
- **AND** 已提交的 ledger event 不被删除或更新

#### Scenario: 到期 ledger 需要销毁
- **WHEN** 经批准的 retention policy 要求销毁 sealed ledger/segment
- **THEN** 只能通过独立、可审计的生命周期操作处理整个批准目标
- **AND** 运行时不得对单条历史 Fact/Assertion 执行 update/delete 来改变语义历史

### Requirement: Diagnosis shadow 必须复用公共 Evaluation Contract
系统 MUST 为合成 Diagnosis golden cohort 生成公共 `clinical-evidence-evaluation` 定义的 versioned plan、cases、A/B arm observations、metric/gate results、report 和 manifest。A MUST 表示现有 `note_diagnosis`/canonical diagnosis 可见输出，B MUST 表示新逐 row/span Evidence shadow；两个 arm MUST 引用同一 immutable SourceArtifact version/checksum。报告 MUST 使用 CONFORMANCE profile，MUST NOT 私建另一套指标或单一 aggregate score。

#### Scenario: 同一 fixture 形成完整 A/B
- **WHEN** A 与 B 读取同一个 synthetic `shi_zd`/`case_notes` SourceArtifact snapshot
- **THEN** evaluation package 记录两个 arm、相同 input digest、各自 producer/version 和原始 repetition
- **AND** 分别计算 pair completeness、grounding、locator/provenance completeness、UNKNOWN/Conflict 语义和 canonical repeat stability

#### Scenario: 任一 arm 或 snapshot 不一致
- **WHEN** A 缺失、B 缺失或两个 observation 的 SourceArtifact checksum 不同
- **THEN** evaluation report 为 INVALID
- **AND** 不以历史输出或另一 fixture 补齐配对

### Requirement: Diagnosis A/B 必须限制为 evidence conformance 解释
Diagnosis paired report SHALL compare extraction and evidence-accounting behavior, old tool compatibility, source occurrence coverage, precise locator/span, deterministic terminology mapping, UNKNOWN/Conflict representation, replay stability, and failure isolation. Because A has no independent clinical eligibility verdict, oncology decision metrics such as false VIOLATION, false CLEAN, expert outcome agreement, harm-weighted clinical loss, and PROMOTION eligibility SHALL be `not_applicable` and SHALL NOT be reported as zero or PASS.

#### Scenario: B 增加精确 span
- **WHEN** A 只保留聚合 diagnosis text/count/stages 而 B 保留同一出现位置的 SourceArtifact/row/section/span/checksum
- **THEN** case delta 解释 B 新增的 grounding 与 locator 能力，同时证明旧工具文本输出未变化

#### Scenario: 使用 Diagnosis conformance 宣称临床优效
- **WHEN** 调用方尝试从 Diagnosis extraction A/B 推导 oncology 临床准确率或生产 promotion
- **THEN** evaluator 拒绝该 profile/metric 组合并返回稳定的 `METRIC_NOT_APPLICABLE` issue
