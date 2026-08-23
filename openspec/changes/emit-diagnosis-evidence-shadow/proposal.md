## Why

Javert 已能从结构化诊断和临床文书读取信息，但这些结果尚未用统一 Evidence Contract 旁路记录，因而无法在不影响现有裁决的前提下验证来源定位、多证据、冲突、未知和本体类型约束。需要以 Diagnosis 为首个最小垂直切片，在公共契约落地后建立可重放、可对账的 shadow 运行证据。

## What Changes

- **前置依赖**：`define-clinical-evidence-contract` 必须先完成 apply、严格校验和契约验收；本 change 只消费其版本化 Source、Evidence、CandidateAssertion、Fact/Assertion、Provenance、Ontology 与关系语义，不在本 change 重新定义或绕过公共契约。
- 从 canonical ingestion 的结构化 diagnoses/`shi_zd` 行，以及临床文书的诊断 section/字符 span，确定性地产生带稳定来源定位的诊断 `CandidateAssertion`。
- 新增最小进程内 evidence bus/store seam：候选先经公共 schema 校验和 ontology domain/range 校验，合法事件才追加写入独立 shadow ledger；技术失败被隔离、可安全诊断且不得影响主审计链。
- 以稳定幂等键支持同一来源版本的重放去重，同时保留多来源证据、同义诊断规范化、矛盾和 `UNKNOWN`，不以静默覆盖方式合并事实。
- 新增仅含合成、去标识数据的 golden cases 与 shadow 对账输出，覆盖结构化/文书同义诊断、多证据、冲突、缺失未知、非法关系、幂等重放和技术失败隔离。
- 复用前置 change 的 `clinical-evidence-evaluation`：在相同 synthetic SourceArtifact 上把 A=现有 `note_diagnosis`/canonical diagnosis 输出、B=新逐 span/row Evidence shadow 输出，持久化 arm observations、证据完整性、稳定性和 case delta；此处只做 extraction/evidence conformance，不把它冒充 oncology 临床效果评测。
- 现有 Runner、verdict、precheck/verdict gate、Router、SQLite/SQL Server 审计结果双写和 2C v3 API 均保持不变；旧审计结果继续是唯一权威输出，shadow ledger 不参与临床或医保裁决。
- 不引入 Neo4j、Milvus、Kafka、外部在线服务、完整插件体系或通用事件平台。

## Capabilities

### New Capabilities

- `diagnosis-evidence-shadow`: 定义 Diagnosis 垂直切片的双来源候选生成、source locator、契约/ontology 校验、进程内 shadow 传递、append-only ledger、幂等重放、故障隔离和 golden-case 对账行为。

### Modified Capabilities

无。此 change 不修改现有审计、路由、存储双写或对外 API 的需求语义。

## Impact

- **前置契约**：实现必须固定到已验收的 `define-clinical-evidence-contract` schema 与 ontology 版本；契约不兼容或未启用时 shadow 路径 fail closed 为停用/失败记录，主链保持可用。
- **数据接入**：复用 `configs/schema_manifest.yaml` 与 `src/javert/data/hub_source.py` 的 canonical ingestion 语义；结构化 locator 至少到 canonical row/字段，已存在上游 HIS 表、主键信息时可作为可选增强，不能把它作为 v0.1 的硬依赖。
- **文书抽取**：扩展现有文书诊断读取能力，使每个候选保留 document、section、`start_char`、`end_char` 和来源版本/checksum；不把仅有聚合文本或频次的结果当可提交证据。
- **运行时与存储**：新增独立的 in-process shadow service 与本地 append-only ledger/projection seam；不复用或修改 `audit_runs` 作为事实账本，不改变 SQLite/SQL Server 审计结果 schema 与同步行为。
- **验证与隐私**：新增离线、无网络、无生产数据库的合成 fixtures、对账命令/报告和故障注入测试；Git、日志与报告不得包含真实患者标识、病历原文、凭据或未盐化运行标识。
- **可复用评测**：输出符合公共 Evaluation Contract 的 canonical plan/cases/observations/report，使用 CONFORMANCE profile；same-input paired completeness、grounding、locator、provenance 和 repeat stability 必须分别达标，不生成单一总分。
- **兼容性**：不新增或变更 2C/SSE 公共字段，不改变规则状态、模板或 Router index；后续若要让 evidence 影响裁决，必须另立 change 并重新验收。
