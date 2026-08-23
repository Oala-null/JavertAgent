## Why

Javert 已经在审计结果、肿瘤资格和知识维护中保存了局部证据与溯源信息，但缺少跨领域稳定的公共契约，无法一致回答“事实是什么、由谁声明、来自哪里、何时有效、按哪个语义版本解释”。现有 oncology 报告又明确属于 `historical_unpaired`，不能证明旧架构与新架构谁更好。现在需要同时冻结 Evidence Contract 与可复用的 Evaluation Contract，让后续 change 能在同一不可变输入上，以可持久化、可解释且不能被单一总分掩盖的指标比较两条架构。

## What Changes

- 定义 versioned `SourceArtifact`、`EvidenceItem`、`EntityRef`、`Fact`、`Assertion`、`ProvenanceActivity`、`OntologyRef`、`Inference`、`Conflict` 和 `ReviewAction` 公共契约，并明确 Observed、Inferred、Knowledge 三类声明来源的边界；同一 proposition 不因来源不同重复建 Fact。
- 建立 No Naked Facts 不变量：任何可提交事实都必须通过 Assertion 关联证据、来源、溯源活动、Ontology 版本和时间语义；候选声明不得绕过验证直接成为事实。
- 分离临床有效时间与系统记录时间，明确 `UNKNOWN` 不等于 `FALSE`，支持 `supports`、`contradicts`、`derived_from`、`supersedes` 和 `retracts` 关系及可审计状态迁移。
- 将相对 immutable SourceArtifact version 的 canonical ingestion row ordinal、deterministic row fingerprint、field 和 checksum 设为强制 lineage 边界；上游 HIS 数据库、表、原始主键和字段 locator 仅作为可选非权威增强，文书证据可附加 section 与 end-exclusive 字符 span。
- 定义 Ontology v0.1：带版本的实体类型、predicate domain/range、`ConceptRef`、最小 `is_a` 层级和确定性校验；不引入图数据库、向量数据库、消息总线、OWL reasoner 或插件体系。
- 要求现有 oncology contract 提供引用式 conformance adapter，保持 `eligibility_json` 和 proof tree 为权威且无需 whole-payload round-trip；本 change 不接入 runner、Router、gate、存储或 API，也不改变任何现有 verdict。
- 定义 versioned `EvaluationPlan`、`EvaluationCase`、`ArmObservation`、`ExpertAdjudication`、`MetricResult`、`AcceptanceGate` 和 `EvaluationReport`，固定 A/B 单元、输入快照、两个 arm、知识/模型/代码版本、重复次数、专家参考和验收 profile。
- 建立 oncology 同输入 paired A/B：A 为 legacy oncology verdict，B 为同一次 `shadow` 运行中的结构化 eligibility/proof 结果；历史旧裁决继续标记为 unpaired，只能作探索性背景。
- 持久化 canonical evaluation plan/observations/adjudications/report/manifest 及 checksum；同时输出面向专家的逐病例 outcome、决定性证据、UNKNOWN/CONFLICT、版本与差异解释，不输出 PHI。
- 用分层门禁替代单一总分：数据有效性、安全关键错误、正确自动化率、证据/溯源完整性、可重放性和专家可理解性必须分别达标，性能成本不得抵消临床安全失败。
- 建立仅含去标识合成数据的 golden cases，覆盖多证据支持、显式冲突、缺失信息、推理 lineage、非法关系拒绝和肿瘤契约映射。

## Capabilities

### New Capabilities

- `clinical-evidence-contract`: 公共证据对象、事实与声明边界、双时间语义、lineage、证据关系、冲突/复核以及 No Naked Facts 不变量。
- `clinical-ontology-validation`: Ontology v0.1 的版本化类型与 predicate 约束、`ConceptRef`、最小 `is_a` 推理和确定性验证行为。
- `clinical-evidence-evaluation`: 可复用、版本化、可持久化的 paired A/B 计划、观测、专家裁定、指标、置信区间、分层验收门禁和 oncology 解释性差异报告。

### Modified Capabilities

无。

## Impact

- 新增公共契约与评测契约 schema、序列化/校验边界、合成 golden fixtures、paired metric evaluator 和 conformance 测试；具体代码落点由 design 与 apply 阶段确定。
- 现有 `src/javert/audit/result.py`、`src/javert/oncology/contracts.py`、oncology authoring provenance 和 `configs/schema_manifest.yaml` 作为映射输入，不在本 change 中改写其运行时行为。
- 不新增外部服务或基础设施依赖，不建设 Evidence Store/Index adapter、消息传输、插件 SDK 或全量数据建图。
- 扩展现有 oncology shadow 报告能力以生成严格的 same-input paired evaluation；不把现有 `historical_unpaired` 报告改写成已配对结果，也不自动授权生产切换。
- 后续 `emit-diagnosis-evidence-shadow` change 将消费本契约；本 change 自身不写患者 evidence ledger、不回填历史结果、不触达生产数据。
