## Context

当前 Diagnosis 信息有两条彼此独立的读取路径：`configs/schema_manifest.yaml` 把 canonical `diagnoses` 数据集落成 `shi_zd` 七列契约；`note_diagnosis` 从 `case_notes` 的诊断子阶段拆分文本并按频次聚合。前者保留诊断名和编码，但消费侧通常不携带 canonical row lineage；后者只返回聚合文本、频次和阶段，拆分后丢失 document identity 与字符 span。两者都可能参与现有审计提示、Router 或工作台，但这些现有消费者不是 Evidence Contract 的实现，也不能被 shadow 结果反向改写。

前置 change `define-clinical-evidence-contract` 将提供 `clinical-evidence-contract` 和 `clinical-ontology-validation` 两项 capability，定义 `SourceArtifact`、`EvidenceItem`、`CandidateAssertion`、`Fact`、`Assertion`、`ProvenanceActivity`、`OntologyRef` 等公共对象及确定性校验。本 change 必须在该 change 已 apply、严格校验和 conformance 验收后开始；它只负责把 Diagnosis 双来源接到公共契约，并验证最小运行闭环，不反向规定公共契约必须提供消息中间件或数据库接口。

本阶段的 shadow 输出没有裁决权。现有 Runner、precheck、verdict gate、Router、`audit_runs`、SQLite/SQL Server 审计双写、SSE 和 2C v3 继续以旧链路为权威。主要使用者是后续实现者、数据/临床评审者和运行 shadow 对账的工程人员，而不是 2C 客户端或医生工作台。

## Goals / Non-Goals

**Goals:**

- 从每条 canonical `shi_zd` 诊断行和每个文书诊断精确 span 产生符合公共契约的 `CandidateAssertion`。
- 让结构化 row、文书 document/section/span、来源版本/checksum 和可选上游 HIS locator 均可追溯，满足 No Naked Facts。
- 用同步进程内协调器依次执行 schema 与 ontology 校验，仅把合法结果追加到独立 shadow ledger。
- 在不覆盖历史的前提下表达同义诊断的多证据支持、显式矛盾和 `UNKNOWN`，并提供可重复的只读对账投影。
- 用稳定幂等键保证相同输入版本重放不重复写入，同时让来源修订形成新版本事件。
- 让数据源、抽取、校验或 shadow store 的技术失败局限在 shadow job，提供无 PHI 诊断且不影响权威审计链。
- 建立完全离线、合成、去标识的 golden harness，覆盖所要求的语义与故障分支。

**Non-Goals:**

- 不让 shadow Fact、Assertion、冲突或 projection 参与任何规则候选、LLM prompt、verdict 或人工审核状态。
- 不修改现有 Diagnosis 工具的公开文本输出语义，不把 `audit_runs` 或 SQL Server 审计结果表改造成 evidence ledger。
- 不扩展 `DataLoader` 公共接口、`schema_manifest` 输出列或 `hub_source` 的 canonical `shi_zd`/`case_notes` schema；extractor 接受显式 SourceArtifact metadata 与现有 canonical rows。
- 不在线回填历史患者，不静默重算旧审计结果，不在本 change 获得生产数据运行或 62 发布授权。
- 不建设通用插件 SDK、跨进程事件平台、图数据库、向量索引、术语在线查询或 OWL reasoner。
- 不把 Diagnosis slice 泛化到手术、检验、收费、用药或完整临床本体。

## Decisions

### D1 — 公共契约是硬前置，shadow 只消费不复制

实现开始前先以 `openspec validate define-clinical-evidence-contract --strict`、该 change 的任务完成状态和公共 conformance tests 三项共同确认依赖。Diagnosis 模块直接导入公共契约对象、canonical serialization、schema validator 和 ontology validator；不得在 slice 内另建相似但不兼容的 `Evidence`/`Fact` 类型或放宽枚举。

公共契约 schema/ontology 版本不受支持时，shadow command 在读取患者数据前快速失败并返回安全错误码；若是单条候选不合法，则该候选进入 rejection 诊断流而不能成为 Fact/Assertion。两种失败都不触发或改变权威审计。

*Alternatives:* 在本 change 先复制一版类型，待 contract 完成后迁移。否：这会让第一个垂直切片成为事实上的第二套契约，并把未来兼容问题固化进 ledger。

### D2 — 以独立 shadow command/job 接线，不在 Runner 中加旁路 hook

新增默认关闭的 Diagnosis shadow application service 和显式 CLI/job 入口，从现有 canonical loaders 读取选定范围并生成 shadow ledger。入口不由 `audit-patient`、Web、SSE 或 2C 请求隐式调用，也不接 `persist_one`；因此即使 shadow 变慢、失败或停用，现有审计调用序列、返回值和数据库事务都逐字保持现状。

服务内部按结构化诊断与文书诊断两个 source batch 独立处理。一个来源失败时，另一来源仍可产生结果；job 最终摘要明确 `complete/partial/failed`，退出码用于 shadow 运维，但不会向主链写 INCONCLUSIVE 或其他 verdict。

*Alternatives:* 在 `Runner` 完成审计后发一条 best-effort 事件。否：即使异常被捕获，仍会把 shadow 的时延、资源竞争和回归风险引入权威路径，也违反“Runner 不改变”的验收边界。

### D3 — 两个窄 adapter 保留 lineage，现有聚合输出不作为证据输入

结构化 adapter 是接收显式 SourceArtifact metadata 与 canonical rows 的纯 extractor，不扩展 `DataLoader` 公共接口、manifest 输出列或 hub canonical schema。它按 manifest 的 `diagnoses`/`shi_zd` 契约读取每条 canonical row。由于现有 canonical 输出已丢失 `ZYZDLSH/ZDXH`，且 generic ETL 生成的 `ipt_medcas_hmpg_sn` 可能随输入顺序变化，权威 locator 至少包含 immutable SourceArtifact identity/version/checksum、canonical dataset/table、该 artifact 内 zero-based row ordinal、deterministic row fingerprint、实际消费 field 及其 value checksum。`ba_id`、`ipt_medcas_hmpg_sn` 只能作非权威显示/辅助键，不能冒充稳定 source primary key。相同行中的入院/出院兼容列按一套确定性优先级归一，不能因为重复承载生成两条无来源差异的候选。若 ETL 已保存可信的上游数据库、表、主键和字段映射，则作为 optional locator 追加；缺少它不得阻止 v0.1 写入。

文书 adapter 复用诊断 section allowlist 和拆分规则，但新增保留位置的抽取结果；每个结果包含 immutable SourceArtifact identity/version/checksum、artifact 内 zero-based row ordinal、deterministic row fingerprint、section、原始 canonical 内容内 end-exclusive `[start_char,end_char)`、excerpt checksum 和 producer version。当前 canonical 输出已丢失 `WSLSH` 等稳定上游 document ID，因而不能拼造 `document_ref`；只有已验证的 ingestion mapping 才能附加上游 document locator。内容完全相同但 ordinal 不同的两行仍是两个 canonical source occurrence；只有 artifact/version、ordinal 与 fingerprint 都相同才是同一来源重放。现有 `note_diagnosis` 聚合文本可继续服务旧调用方，但其 `text/count/stages` 输出因丢失逐条 span，禁止直接提交 shadow ledger。

两个 adapter 都只发出 `Patient has_diagnosis Disease` 形态的 `CandidateAssertion`。结构化编码使用离线 ontology pack 中已声明的 `ConceptRef`；未知 code 不得冒充 ICD 或其他 code system。文书同义词只通过 Diagnosis slice 自有、版本化、确定性的 terminology mapping 规范化；该 mapping 不是 Ontology v0.1 的新增语义。无法确定规范化命题的文本不调用在线服务，也不猜测概念：保留安全的 unmapped/coverage outcome 或 rejection，不能创建名为“Unknown”的 diagnosis entity，具体承载严格服从公共契约。

*Alternatives:* 直接把 `note_diagnosis` 的聚合结果包装为 Evidence。否：无法回答每次出现来自哪个 document/span，多证据和冲突也会在聚合时被提前抹平。

### D4 — “bus” 是同步校验管线，不是消息基础设施

最小同步 in-process validation/ledger seam 是一个 application coordinator，概念接口为 `submit(CandidateAssertion) -> ShadowAppendOutcome`。实现使用 prerequisite change 已冻结的实际类型和函数名，不另行要求 contract change 提供 bus API，也不复用 Web SSE `EventBus`。每个候选按固定顺序执行：

1. 验证来源、Evidence、Assertion、Provenance、双时间和版本字段的 schema/引用完整性；
2. 验证 subject/object entity type、predicate domain/range、`ConceptRef` 与 ontology version；
3. 构造 canonical accepted envelope 与幂等键；
4. 通过 `ShadowLedger` seam 原子 `append_once`；
5. 返回 `accepted/duplicate/rejected/failed`，供安全计数和对账。

schema 或 ontology rejection 只记录稳定错误码、生产者版本和安全 source token，不把未校验 payload 当 Fact 写入 accepted stream。ontology 示例 `Medication has_diagnosis Disease` 必须在 append 前确定性拒绝。

*Alternatives:* Kafka topic、异步队列或通用 event dispatcher。否：当前只有一个进程内生产者和一个本地消费者，外部传输不会增加 v0.1 的语义验证价值。

### D5 — 独立 SQLite append-only ledger + 可替换 repository seam

定义一个窄 `ShadowLedger` repository seam，仅承担 `append_once(envelope)`、按 stream/cursor 只读迭代和安全统计；不暴露 update/delete/upsert Fact。首个 adapter 使用 Python 标准库 SQLite 和独立数据库文件，不修改现有审计 SQLite schema，也不接 SQL Server。数据库目录使用 0700、文件使用 0600；连接地址和任何盐只来自受控配置/环境，不写入仓库、命令参数或日志。

accepted envelope、validation rejection 和 technical diagnostic 是分离的 append-only stream。后两者只保存安全元数据，不持久化未验证的病历 payload。projection 由 ledger events 重建且可丢弃重建，不是新的事实源。v0.1 不实现运行时自动 retention/delete/compaction；实现任务必须产出受控 retention policy，明确 owner、location、保留期、备份和 sealed ledger/segment 的整目标销毁流程。异常退出必须 rollback/关闭连接与清理临时资源，但不得删除或更新已提交 ledger event 来伪装清理。

幂等键使用公共契约 canonical serialization，至少绑定 contract schema version、ontology version、producer/version、SourceArtifact identity/version/checksum、locator、candidate semantic payload。相同输入版本重放命中唯一约束并返回 `duplicate`，不新增事件；source checksum、版本或语义 payload 变化时只保证追加独立新事件。仅当可信 ingestion metadata 显式提供 `revision_of`/撤回语义时，才可建立 `supersedes`/`retracts`；`derived_from` 只用于公共契约定义的 Inference，系统不得从内容相似或变化自行猜测版本关系，也不得原位修改旧行。

*Alternatives:* JSONL。否：它足够简单，但并发 append、唯一幂等约束、事务提交和 cursor 对账都需要重新实现；SQLite 已在标准库中且不引入服务依赖。直接复用 `audit_runs` 也被否决，因为其生命周期和权威语义完全不同。

### D6 — ledger 保留每次 Assertion，projection 才聚合多证据与冲突

accepted stream 在 ledger 生命周期内不可变地保留各来源独立的 SourceArtifact、EvidenceItem、Assertion 和 Provenance，直到按获批 retention policy 对 sealed ledger/segment 执行整目标销毁。只读 Diagnosis projection 按公共契约的语义 identity（subject、`has_diagnosis`、`ConceptRef.system/code/version`、适用 clinical valid time）分组，`ConceptRef.display` 仅用于展示、不能合并不同 code identity：同一 identity 的结构化全称与文书同义词显示为一个 Fact 的两条独立支持链，而不是丢弃“重复”来源。

只有同一规范化命题、适用时间范围重叠的 TRUE/FALSE Assertion，才能以 `contradicts`/`Conflict` 显式并列；Ontology v0.1 不定义 disjoint 推断，两个不同诊断不能仅因 concept 不同就自动判冲突，也不按“结构化优先”静默覆盖。文书写明“待排/疑似 X”且 X 可规范化时，保留 proposition Fact 和绑定实际 SourceArtifact、检查时间范围与 uncertainty reason 的 `Assertion.value=UNKNOWN`，但不得标记 supports/contradicts；完全没有可形成命题的诊断证据时，对账摘要只能报告 coverage `UNKNOWN` 和可追溯扫描范围，不得制造 Unknown Diagnosis entity 或 TRUE/FALSE Assertion。此 projection 不做医学优先级裁决；ontology `is_a` 结果只能用 `Assertion.origin=INFERRED` 和 Inference lineage 表达，不能冒充 `origin=OBSERVED`。

*Alternatives:* 以 `(patient, concept)` upsert 一张当前事实表。否：会抹掉来源级声明、时间、冲突和修订，无法证明 append-only 回放。

### D7 — 故障边界与可观察性只暴露安全元数据

shadow service 在 source adapter、contract validator、ontology validator 和 ledger adapter 四个边界分别捕获已分类异常；进程级取消和资源耗尽仍向 shadow command 传播为非零退出。一个 candidate 的数据错误不能终止同 batch 其余候选，一个 source 的读取错误不能阻止另一 source，但任何 partial run 都必须显式标记，不能报告为完整成功。

日志/CLI/报告只包含 synthetic case ID 或不可逆安全 token、source kind、contract/ontology/producer version、`accepted/duplicate/rejected/failed` 计数、稳定错误码和耗时分桶；不输出 patient ID、诊断原文、document excerpt、SQL、凭据、完整 candidate/envelope 或未盐化 run/ownership ID。shadow 失败不得写 verdict、修改审计结果、触发 Router 或发布 2C/SSE 字段。

*Alternatives:* best-effort 全部吞错。否：主链虽然不受影响，但 shadow 差异会被误当作无问题，失去探索阶段的可信度。

### D8 — golden harness 固定语义，不使用真实病例或在线依赖

合成 fixtures 使用语义化患者/文书/row ID，并固定 contract schema、ontology pack、producer 和 source versions。harness 至少包含：

- `shi_zd` 的“甲状腺乳头状癌”与文书同义词“PTC”映射到同一 concept，保留两个 evidence；
- 结构化 positive 与文书 explicit negative 形成 Conflict；
- 文书“待排/疑似”或证据缺失保持 `UNKNOWN`，不得变为 FALSE；
- 直接注入 `Medication has_diagnosis Disease` 被 ontology validator 拒绝且 accepted stream 无记录；
- 同一 batch 重放两次，第二次只增加 duplicate 计数，ledger 内容和 projection 不变；
- 分别注入 source、validator 与 store 技术失败，证明另一 source/后续 candidate 可继续且权威 audit fixture 字节级不变。

测试禁止访问网络、LLM、SQL Server、142/243 hub 或任何生产数据库；规范化后的 ledger/projection 在相同 fixture 上必须确定一致。

*Alternatives:* 先抽取少量真实患者做金标。否：这会违反 Git/QA 隐私边界，也会让第一版契约门禁依赖易漂移的生产数据。

### D9 — Diagnosis 复用同一 Evaluation Contract，但不冒充临床效果 A/B

前置 change 的 Evaluation Contract 是跨领域公共测量边界。本 slice 用它构造一个相同 SourceArtifact 的 paired conformance：A 是现有 `note_diagnosis` 聚合文本及 canonical `shi_zd` 可见字段形成的 legacy observation，B 是新 extractor/validator/ledger/projection observation。主要指标是 pair/input completeness、逐 occurrence grounding、精确 locator/span、provenance/version 完整、UNKNOWN/Conflict 语义、重复运行 digest 稳定和技术失败隔离；旧工具文本兼容性作为独立 gate。

由于 A 没有独立临床裁决，Diagnosis 报告不得计算或宣称 oncology false-V/false-C、专家一致率或生产 promotion。它固定使用 `CONFORMANCE` profile，并把不适用指标保存为 `not_applicable` 而非 0。输出 canonical evaluation plan/cases/arm observations/report/manifest，引用 shadow ledger digest；同一 fixture 的 A/B observation 共享 source snapshot checksum，缺任一 arm 或 snapshot 漂移即 INVALID。

此复用证明评测标准可从 oncology 迁移到新 evidence slice，同时保持语义诚实：oncology same-run paired A/B 回答“决策是否更安全、更有用”，Diagnosis paired conformance 回答“证据是否更可追溯、更稳定”，二者使用同一持久化合同但不同 acceptance profile。

## Risks / Trade-offs

- [当前 `case_notes` 与 `shi_zd` 丢失部分上游 row/document primary key] → 强制使用 immutable SourceArtifact version/checksum + row ordinal + deterministic fingerprint + field/span checksum；只有可信映射存在时追加 HIS locator，并把缺失上游 locator 显式计入 lineage coverage。
- [本地同义词/ConceptRef mapping 覆盖有限] → 未映射即 `UNKNOWN`/rejection，不调用在线服务、不做模糊猜测；扩大 terminology pack 另行评审并版本化。
- [SQLite ledger 含患者级证据引用] → 独立路径、0700/0600、最小 excerpt、无 payload 日志；真实数据运行必须另有数据治理与环境授权。
- [独立 job 与主审计运行时间不同步] → SourceArtifact checksum/version 与双时间进入幂等键和对账摘要；不声称 shadow 与某次 verdict 是同一事务快照。
- [append-only 数据增长] → v0.1 只保存 Diagnosis slice 并提供只读统计，不实现自动 retention/delete/compaction；在真实数据启用前形成经批准的 owner/location/保留期/备份/整段销毁 policy，删除不得伪装成普通更新。
- [contract change 后续发生不兼容修订] → ledger envelope 固定 schema/ontology version，loader 拒绝未知版本；迁移必须以新事件/新 adapter version 完成，不原地改写历史。
- [shadow 技术失败被误解成临床 UNKNOWN] → operational `failed/partial` 与 epistemic `UNKNOWN` 使用分离类型和计数，projection 不用技术失败合成临床事实。

## Migration Plan

1. 先 apply 并验收 `define-clinical-evidence-contract`；运行其全部 conformance/golden tests 和 `openspec validate ... --strict`，确认对象、canonical serialization、validator 与 ontology pack 版本。
2. 增加 Diagnosis adapter、保留 span 的抽取单元和公共 contract mapping；先用合成 fixtures 证明 locator、同义词、UNKNOWN 与非法关系行为。
3. 实现同步 coordinator、`ShadowLedger` seam、独立 SQLite adapter 和 append-only/idempotency 测试；确认没有改动 `audit_runs` 或 SQL Server schema。
4. 增加默认关闭的显式 shadow CLI/job、只读 projection、safe summary、异常资源关闭与故障注入；形成真实数据启用前强制要求的受控 retention policy，运行 golden harness 两次并比较规范化输出。
5. 生成符合公共 Evaluation Contract 的 Diagnosis A/B conformance 包，验证同一 source snapshot、两臂完整、grounding/locator/provenance/stability gates 和 `not_applicable` 指标语义。
6. 运行受影响的既有 diagnosis tool/loader/Router/audit/Web/API 回归，证明旧输出与契约不变；再执行一个完全合成的端到端 shadow command。
7. 默认不在 62 或生产定时任务启用。若后续获独立授权，仅以 shadow job 运行并对账，不接裁决；任何让 evidence 影响 verdict 的动作必须新建 OpenSpec change。

回滚只需停用/移除 shadow command 调度并回退应用代码；权威审计无需数据回滚。独立 ledger 保留为不可变审计工件或按另行批准的安全 retention 流程处理，不通过 drop/update 冒充回滚。

## Open Questions

无阻塞性产品问题。apply 时必须以已验收的 `define-clinical-evidence-contract` 实际模块路径、字段名、canonical serialization 和 validator 接口替换本文概念名；如果前置 change 尚未冻结这些接口，本 change 保持 blocked，不自行猜测第二套接口。
