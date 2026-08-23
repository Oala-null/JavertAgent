## 1. 前置契约与不变边界门禁

- [x] 1.1 硬门禁确认 `define-clinical-evidence-contract` 已完整 apply、所有 tasks 与 conformance/golden tests 通过，并运行 `openspec validate define-clinical-evidence-contract --strict`；任一项不满足即停止本 change，不自行复制 contract 类型。
- [x] 1.2 阅读已验收 contract 的实际模块、字段、canonical serialization、schema validator、ontology validator 和版本兼容接口，形成 Diagnosis mapping 表并用 import/round-trip 单测固定；概念设计名与实际接口不一致时只调整本 slice 调用方。
- [x] 1.3 为隔离边界建立先行回归：普通 CLI/Runner、Router、precheck/verdict gate、`persist_one`/`audit_runs`、SQLite/SQL Server 双写、SSE 与 2C v3 fixtures 在 shadow 默认 off 时输出不变，且代码依赖图中这些模块不导入 shadow store/coordinator。
- [x] 1.4 确认实现不新增 Neo4j、Milvus、Kafka、在线 terminology、LLM、网络客户端或插件框架依赖，并为 shadow tests 设置网络、SQL Server、142/243 hub 访问 fail-fast 门禁。

## 2. 合成资产与 Diagnosis terminology

- [x] 2.1 新增固定 contract schema、ontology、producer 和 source versions 的去标识合成 SourceArtifact/`shi_zd`/`case_notes` fixtures；只使用语义化 patient/row/document ID，不含真实患者号、原始病历、凭据或未盐化 run/ownership 标识。
- [x] 2.2 复用公共 Ontology v0.1 中的 Patient/Disease/`has_diagnosis` 与测试 concept，并新增 Diagnosis slice 自有、版本化 deterministic terminology mapping（含“甲状腺乳头状癌”/“PTC”）；不得向 Ontology v0.1 私自加入 alias/disjoint 语义，未知 code 不登记为 ICD fallback。
- [x] 2.3 实现 fixture/报告隐私静态检查，覆盖患者姓名/号、病历原文、数据库字段、凭据和未盐化标识；失败只报告文件与禁止类别，不回显命中值。

## 3. Canonical locator 与双来源 extractor

- [x] 3.1 基于公共 canonical serialization 实现 SourceArtifact locator/fingerprint helper，固定 artifact identity/version/checksum、zero-based row ordinal、deterministic row fingerprint、field/value checksum 与 note `[start,end)` span checksum；`ba_id`/合成序号只进入非权威辅助字段，上游 locator 仅接受已验证 ingestion mapping，且不扩展 `DataLoader`、manifest 输出列或 hub canonical schema。
- [x] 3.2 实现纯结构化 Diagnosis extractor：逐 canonical `shi_zd` row 按固定兼容字段优先级发出一个 `Patient has_diagnosis Disease` CandidateAssertion，并用单测覆盖合法 code、空值、重复兼容列、未知/错 system code 和缺上游 `ZYZDLSH/ZDXH`。
- [x] 3.3 实现保留位置的文书 Diagnosis extractor：复用诊断 section allowlist 与拆分语义，输出 artifact version/checksum、row ordinal/fingerprint、section、end-exclusive char span、excerpt checksum；覆盖多条目、Unicode 索引、前导编号、重复内容不同 ordinal 和缺 `WSLSH`。
- [x] 3.4 保持现有 `note_diagnosis` 面向 Agent 的文本/计数/stages 输出不变，为改动前后相同 fixtures 增加精确兼容断言；禁止把旧聚合输出直接包装成 shadow EvidenceItem。
- [x] 3.5 将两个 extractor 的结果映射到已验收公共 SourceArtifact/EvidenceItem/CandidateAssertion/Provenance/OntologyRef 类型，验证所有 ID、双时间、版本和引用可 round-trip 且满足 No Naked Facts。

## 4. 同步校验与追加式 ledger

- [x] 4.1 实现窄的同步 validation/ledger coordinator 与 `accepted/duplicate/rejected/failed` outcome，严格按 schema/reference → ontology domain/range/ConceptRef/version → canonical envelope → append_once 顺序运行；不复用 Web SSE EventBus，不含异步投递/网络重试语义。
- [x] 4.2 为合法 `Patient has_diagnosis Disease`、缺 Evidence/Provenance/双时间、未知 contract/ontology version 和 `Medication has_diagnosis Disease` 增加单测，证明非法 candidate 在 append 前拒绝且 rejection 不持久化原始 payload。
- [x] 4.3 定义仅含 `append_once`、cursor 只读迭代与安全统计的 `ShadowLedger` repository seam；接口不得暴露 Fact/Assertion update、delete 或 upsert。
- [x] 4.4 用 Python 标准库 SQLite 实现独立 shadow adapter，将 accepted、validation rejection、technical diagnostic 分 stream 追加并保证事务原子性；不修改现有审计 SQLite schema、不接 `persist_one`/`audit_runs`/SQL Server，创建目录 0700、文件 0600，并覆盖损坏 ledger row 检测。
- [x] 4.5 实现绑定 contract/ontology/producer/source identity-version-checksum/locator/semantic payload 的稳定幂等键和唯一约束；覆盖相同输入重放不增事件、相同内容不同 row ordinal 保留两 occurrence、source 变化追加独立新版本、只有可信 `revision_of`/撤回 metadata 才建立 supersedes/retracts、并发 append/lock failure 原子性且旧事件不覆盖。
- [x] 4.6 实现连接 context manager、异常 rollback/close 和临时资源 cleanup；证明异常退出不删除/更新已提交 event，synthetic harness 在成功和失败后均清理 0700/0600 临时目录与文件。

## 5. Projection、冲突与覆盖状态

- [x] 5.1 实现从 accepted stream/cursor 0 确定性重建的只读 Diagnosis projection，按 subject/predicate/`ConceptRef.system-code-version`/clinical valid time 聚合展示但保留每条 Assertion、Evidence 与 locator；验证 display 变化不改 identity、相同 display 的不同 code 不合并。
- [x] 5.2 用版本化 alias 证明结构化全称与文书“PTC”形成同一 diagnosis Fact 的两条支持链；alias 缺失或 code system/version 不合法时输出 unmapped/coverage，不做模糊匹配或在线查询。
- [x] 5.3 仅为同一规范化命题、适用时间范围重叠的 `Assertion.value=TRUE/FALSE` 生成 Conflict；Ontology v0.1 不做 disjoint 推断，测试两个普通不同 diagnosis 并存且不自动 conflict，冲突双方都不可静默覆盖。
- [x] 5.4 将可规范化“待排/疑似 X”表达为绑定实际 SourceArtifact、time coverage 与 uncertainty reason 的 `Assertion.value=UNKNOWN`，且不生成 supports/contradicts；无可形成命题的证据只报告 coverage UNKNOWN，均不得创建 Unknown Diagnosis entity 或 FALSE，也不得把技术 failure 合并进临床 UNKNOWN。
- [x] 5.5 删除并重建 projection，比较规范化 Fact/evidence/conflict/coverage snapshot 与重建前一致，且测试证明不读取 `audit_runs`、SQL Server 或 2C 输出补造事实。

## 6. 默认关闭的 shadow job 与故障隔离

- [x] 6.1 增加默认 off 的配置和显式 Diagnosis shadow CLI/job；在验证 contract/ontology 版本、独立 store location、owner 与 retention policy 前不得读取真实患者数据或创建真实 ledger，普通审计命令不得隐式调用它。
- [x] 6.2 让 structured 与 note source batch 独立运行，并在 candidate、source 与 store 层分类失败；单条失败后续候选继续、单源失败另一源继续，最终摘要准确区分 `complete/partial/failed`。
- [x] 6.3 实现安全日志、CLI 和机器可读 summary，只输出 synthetic case ID/安全 token、source kind、版本、outcome 计数、稳定错误码与耗时分桶；用敏感 payload 注入测试证明不输出 patient ID、诊断/文书原文、source locator、SQL、凭据、完整 envelope 或未盐化标识。
- [x] 6.4 分别注入 structured/note source、contract validator、ontology validator 与 SQLite adapter 故障，证明 shadow 返回明确非零/partial 状态、另一来源或后续 candidate 按契约继续，且权威 audit fixture 的 verdict、落库和 API projection 字节级不变。
- [x] 6.5 编写受控 retention policy，明确真实数据 store location、owner、保留期、备份、sealed ledger/segment 的整目标销毁审批与审计；v0.1 不实现自动 retention/delete/compaction，清理不得通过更新/删除单条 Fact 改变历史。

## 7. Golden harness 与确定性门禁

- [x] 7.1 建立完整 golden case 集，至少覆盖结构化+文书同义诊断、多证据、同命题正反矛盾、两个不同诊断不冲突、UNKNOWN coverage、非法关系、未知 code、幂等重放、source 修订和技术失败隔离。
- [x] 7.2 实现离线 golden runner，在无网络、无 LLM、无 SQL Server/hub 的环境运行全部 cases，并生成仅含 case ID、稳定 outcome/error code 与计数的安全报告；任何外部访问尝试必须快速失败。
- [x] 7.3 对每个 case 至少运行两次，规范化去除允许的运行时元数据后比较 ledger events、projection、conflict/coverage 与报告；随机 ID、输入顺序或时间戳导致差异时门禁失败。
- [x] 7.4 运行一个完全合成的端到端 shadow command，验证 extract → contract/schema → ontology → append_once → replay projection 的闭环，并确认第二次命令只报告 duplicate、accepted ledger 不增长。

## 8. 可复用 Diagnosis A/B conformance

- [x] 8.1 使用已验收的 Evaluation Contract 建立 immutable CONFORMANCE plan：A=旧 `note_diagnosis`/canonical diagnosis observation，B=新逐 row/span shadow observation；固定相同 SourceArtifact snapshot、producer/version、重复次数和 plan checksum。
- [x] 8.2 从 golden harness 持久化 canonical cases、A/B observations、report 和 manifest，计算 pair completeness、occurrence coverage、grounding、locator/provenance、UNKNOWN/Conflict、旧文本兼容和 repeat stability；不生成 scalar score。
- [x] 8.3 将 false-V/false-C、专家 outcome agreement、clinical harm loss 和 PROMOTION 标为 `not_applicable`，测试任何把 Diagnosis extraction conformance 冒充 oncology 临床效果的请求都 fail closed。
- [x] 8.4 覆盖缺 arm、snapshot drift、producer/version drift、B locator 提升、旧输出兼容、重复运行和技术失败 case delta；相同输入的 report/manifest digest 必须稳定。

## 9. 回归、文档与严格验收

- [x] 9.1 先运行 locator/extractor/coordinator/ledger/projection/golden/A-B conformance 定向测试，再运行 diagnosis loader/tool、Router、audit/gate/store/Web/2C 受影响模块组合测试；原样记录 collected/pass/skip/fail/error 与既有债务排除项。
- [x] 9.2 用 git diff/依赖断言确认未改规则状态、关键词、模板、Router index、现有 audit SQLite/SQL Server schema、SSE/2C 字段和生产配置；若实现意外触及任一边界，停止并另立 change，而不是扩大本 change。
- [x] 9.3 更新 README、`docs/how_javert_works.md`、`docs/CHANGES.md` 与相关运维/隐私文档，说明 contract 前置、默认 off、独立 ledger、locator 可信边界、UNKNOWN/Conflict 语义、A/B conformance、safe summary、retention 和“shadow 无裁决权”；显式检查 deployment runbook 与 2C 契约无需修改，不手抄运行时库存数字。
- [x] 9.4 运行 `openspec status --change emit-diagnosis-evidence-shadow` 与 `openspec validate emit-diagnosis-evidence-shadow --strict`，逐条核对 specs 场景、tasks 勾选和真实验证记录；本 change 完成不等于获得 62/生产启用或让 evidence 参与裁决的授权。
