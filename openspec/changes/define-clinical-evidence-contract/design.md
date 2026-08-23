## Context

Javert 当前存在三组相邻但不等价的模型：`audit.result.Evidence` 保存面向最终审计结果的轻量摘录；`oncology.contracts` 保存肿瘤资格的 EvidenceAnchor、NormalizedFact、四态条件和 proof tree；oncology authoring 模型保存版本化来源、fragment 与 review event。它们已经证明证据、事实、版本和复核都是必要概念，但任一组都不适合作为跨领域公共契约：前者过薄，后两者有明确领域所有权。

本 change 只建立一个离线、确定性、可序列化的公共 contract 与 Ontology v0.1 validator。首个消费者将是后续 diagnosis shadow change；本 change 不承担 evidence bus 传输、ledger 持久化或现有审计接线。约束包括 Python 3.11、项目已安装 Pydantic v2、Git 中不得出现 PHI，以及现有 oncology payload、verdict 和 proof tree 必须保持 authority。

## Goals / Non-Goals

**Goals:**

- 冻结 v0.1 公共对象、稳定引用、双时间语义、No Naked Facts 和 append-only 关系语义。
- 用一个确定性入口完成 schema、引用、lineage、不变量与 ontology 校验，失败时不返回可提交的部分结果。
- 用最小 OntologyPack 表达 versioned entity type、predicate domain/range、ConceptRef 和有限 `is_a`。
- 为现有 oncology contract 提供单向引用式 conformance adapter 和合成金标，证明公共契约能关联既有语义而不接管其 authority。
- 让后续 diagnosis shadow change 只消费 contract，不反向规定 contract 的数据库或消息实现。

**Non-Goals:**

- 不实现 Evidence Bus 消息中间件、Event Broker、EvidenceStore/EvidenceIndex adapter 或数据库 schema。
- 不接入 runner、Router、precheck、verdict_gate、AuditResult、SSE、2C API 或 SQLite/SQL Server 双写。
- 不回填历史审计结果，不迁移 oncology payload，不把 proof tree 转成公共合同的替代品。
- 不引入 Neo4j、Milvus、Kafka、RDF/OWL、外部 reasoner、向量检索、插件 SDK 或完整医学术语库。
- 不接入 LLM extractor，也不批量转换现有结构化/非结构化患者数据。

## Decisions

### 1. 以一个隔离的 Pydantic v2 package 作为唯一模型真相源

新增 `src/javert/evidence/`，以 Pydantic v2 models 和 enums 定义公共记录、`EvidenceBundle`、OntologyPack 与结构化 validation issues。Pydantic 是现有依赖，可直接完成 Python 类型校验和 JSON Schema 导出；不再手写第二套 JSON Schema，也不新增 schema/graph library。contract 版本独立于项目版本，v0.1 序列化值固定声明 `contract_version`。

备选方案是先写纯 JSON Schema 或 RDF ontology，再生成 Python 模型。它会引入双源漂移或额外工具链，且 diagnosis shadow 的首个消费者就在 Python 进程内，因此不采用。需要非 Python 消费者时，可从权威 Pydantic models 确定性导出 schema 并把生成物纳入兼容门禁。

### 2. Fact 是去重 proposition，Assertion 承担来源、真值和时间

`Fact` 只包含 normalized subject、predicate 和 object；subject/object 引用 `EntityRef` 或受约束 literal。同一 bundle 内相同 canonical proposition 只能有一个 Fact，调用方提供的稳定 opaque ID 不参与语义相等判断。

`Assertion` 引用 Fact，并保存：`origin`（OBSERVED/INFERRED/KNOWLEDGE）、`value`（TRUE/FALSE/UNKNOWN）、coverage scope、临床 valid time、系统 recorded time、producer/activity、OntologyRef、evidence links 以及可选 supersedes/retracts。TRUE/FALSE 必须有直接证据或推导链；UNKNOWN 则必须记录实际检查的 source/time coverage 和 uncertainty reason，且不得伪造 supports/contradicts evidence。UNKNOWN 是“在声明的覆盖内无法建立真或假”的评估状态，不生成 Unknown concept/entity/Fact。Conflict 引用适用 scope 重叠的相反 Assertions；ReviewAction 是追加事件。

备选方案是把来源类别、真值和时间都放入 Fact。那会让同一 proposition 因不同生产者或不同观察时间重复，并把事实内容与 evidence accounting 混在一起，因此不采用。

### 3. CandidateAssertion 是唯一候选入口，但 contract 不提供 commit/store

producer 只构造 `CandidateAssertion`。一个纯函数式 validator 接收 candidate/bundle、显式 OntologyPack 和可选的合成 source material mapping，按固定顺序执行 Pydantic shape、ID/reference、No Naked Facts、lineage、time、relation 和 ontology 检查。只有零 issues 才返回完整 validated records；任一 issue 都只返回按 `(code, record_id, path)` 排序的错误列表，不返回“已接受子集”。

这里的 atomic 表示校验边界全有或全无，不假装提供数据库事务。持久化与真正 commit 由后续 change 设计。备选方案是现在抽象 EvidenceStore interface 或 event bus；当前没有第二个实现或吞吐需求，属于提前设计，因此不采用。

### 4. SourceArtifact version 是 canonical lineage authority

`SourceArtifact` 表示不可变 canonical ingestion artifact version，至少包含稳定 artifact/version ID、artifact kind、system recorded time 和 `sha256` content checksum。EvidenceItem 的 locator 相对该 version：

- structured：zero-based row ordinal、基于声明的 canonical row serialization 计算的 deterministic row fingerprint、field name、value checksum；
- document：zero-based row ordinal、同样的 deterministic row fingerprint、document field、可选 document ID/section、zero-based `[start_char, end_char)` 和 span checksum。

validator 总是检查 locator 形状、边界顺序、checksum 格式和引用一致性；golden case 通过显式传入的 synthetic source material mapping 检查真实 bounds/checksum，不读取数据库或文件。若调用方不提供 source material，结果只能证明 locator 自洽，不能宣称原始内容仍可访问。上游 HIS locator 是可选、非权威的额外 hop；canonical locator 缺失则 fail closed。

备选方案是把完整 canonical row/document 放进 SourceArtifact 以便自校验。它会复制 PHI 并让 contract bundle 过重，因此不采用。另一个方案是为 source resolver 建一套 interface；首个 change 只需标准 `Mapping`，没有必要造单实现抽象。

### 5. 关系使用受约束的 nested link，不建通用图 DSL

EvidenceItem 到 Assertion 只允许 `supports`/`contradicts`；Inference 到输入 Fact/Assertion 使用 `derived_from`；Assertion 到先前 Assertion 使用 `supersedes`/`retracts`。每类 link 使用窄 Pydantic union 和稳定 IDs，validator 检查端点类型。Conflict 与 ReviewAction 保持独立顶层记录。

备选方案是统一成任意 `Edge(subject, predicate, object)`。它会重复 Ontology predicate 系统、允许非法元关系，并迫使首版实现通用图查询，因此不采用。

### 6. Ontology v0.1 是两个有限有向无环层级加 predicate 表

OntologyPack 包含精确 ID/version/schema/checksum、EntityTypeDefinition、PredicateDefinition、ConceptDefinition，以及 entity-type 和 concept 的显式 `is_a` edges。type hierarchy 仅用于 domain/range subtype 判断；concept hierarchy 仅用于 `is_a` inference。validator 用标准集合/DFS 完成缺失端点、self-edge、cycle、domain/range 和 ConceptRef exact-version 检查，排序保证输出稳定。

`ConceptRef(system, code, version, display)` 的 identity 是前三项，display 仅供展示。任何推理生成的是 origin=INFERRED 的 Assertion，并记录 exact OntologyRef、输入 IDs、遍历 path 和 reasoner version；Fact 本身仍是 proposition。validator 从调用方显式传入 exact pack，不存在 `latest` fallback。

备选方案包括 OWL reasoner、RDF store 或通用规则 DSL。v0.1 只需要 deterministic type checking 与 `is_a`，标准 Python 图遍历足够。

### 7. 稳定序列化和 ID 规则保持小而明确

contract 不自动生成包含业务值的 ID；调用方必须提供稳定、opaque、去标识 ID。validator 拒绝 bundle 内重复 ID、dangling reference 和同 proposition 多 Fact。canonical JSON 序列化按 record type 与 stable ID 排序 collections，并使用固定 key 排序与 JSON 标量格式，使 golden outputs 可复现。schema evolution 在 v0.x 内只允许添加有默认值的可选字段；删除、改名或语义变更必须提升 contract version 并提供新 conformance fixtures。

备选方案是从临床原文或患者主键计算 content-addressed ID。它容易泄漏低熵标识并将 ID 稳定性绑定到 PHI，因此不采用。

### 8. Oncology 使用单向 adapter，authority 不迁移

新增一个 oncology-owned adapter（建议 `src/javert/oncology/evidence_adapter.py`），单向依赖公共 evidence package。adapter 输出 `OncologyConformanceLinks`：以稳定 JSON Pointer 或 node ID 指向原 `eligibility_json`/proof node，并关联对应 public EvidenceItem、Fact、Assertion、ProvenanceActivity IDs；criterion state、source/release/evaluator version 原样附在 link 上。

adapter 不修改 `src/javert/oncology/contracts.py`，不要求把整个 EligibilityEvaluation 转成 EvidenceBundle，也不提供从公共 contract 重建 oncology payload 的反向函数。测试在 adapter 调用前后比较权威 payload 的 dump 和 legacy verdict，并单独断言 UNKNOWN/CONFLICT 标签未被折叠。

备选方案是让公共 models 直接继承 oncology models，或用公共 contract 替换 eligibility_json。两者都会倒置领域依赖或改变已验收裁决权威，因此不采用。

### 9. Conformance 以少量合成 JSON fixtures 和 pytest 为门禁

在 `tests/fixtures/evidence_contract/` 使用 `SYNTH-*` 语义 ID 与人工编写的非患者文本，覆盖 structured/document locator、多证据、冲突、UNKNOWN、非法 domain、`is_a` inference、cycle、retract 和 oncology links。expected validation reports 使用 canonical JSON。测试同时约束 fixture 的 `deidentified=true`、ID prefix/允许字段与禁止 credential key；失败只报告 path，不回显 value。

不采用“20 个真实患者”作为 contract 金标。真实数据属于后续受控 shadow 验收，Git contract fixture 只验证语义与不变量。

### 10. Evaluation Contract 从“患者风险”而不是模型分数出发

评测的基本单元必须是同一不可变 `source_snapshot` 下的同一 `patient × drug × policy_scope` 候选；若 legacy arm 只能给患者级 RD04 结论，则主要 outcome 比较降到患者级，drug/scope 级只比较 B 的证据与 proof 质量，禁止把一个患者 verdict 复制到多个药物后虚增样本量。每个 `EvaluationPlan` 固定：plan/schema version 与 checksum、cohort/query version、source snapshot checksum、A/B 代码 commit 与配置摘要、model/tool/ontology/knowledge/release 版本、重复次数、随机/盲法策略、分析单位、harm matrix、验收 profile 和签署角色。

每个 case 的两个 arm 必须读取相同 source artifact 版本。oncology `shadow` 同一次运行的 legacy verdict 是 A，`selected_eligibility_evaluation.legacy_verdict` 是 B；当前历史报告里的旧 verdict 不共享输入，只能继续标为 `historical_unpaired`。同一病例至少保留每次 repetition，不能用多数票隐藏 legacy 波动；预声明的 modal outcome（平票投影为 INCONCLUSIVE）只用于主混淆矩阵，原始重复结果用于稳定性指标。

评测不生成一个可被“速度提升抵消安全失败”的总分，而是依次判断不可补偿的 gates：

1. **Validity**：输入/候选/版本配对完整率、两臂输出完整率、schema/PHI/重复 ownership/技术错误。
2. **Clinical safety**：相对专家 reference 的 false violation、false clean、unsafe auto-decision、UNKNOWN/CONFLICT 诚实性和 versioned harm-weighted loss。
3. **Useful automation**：正确自动裁决率、适当/不必要 abstention，防止 B 靠全部 INCONCLUSIVE 获得“零误判”。
4. **Evidence integrity**：决定性 evidence grounding、locator 可解析、provenance/proof/version 完整率和冲突可见率。
5. **Reproducibility**：相同快照重复运行的 canonical digest 一致率、arm 内 outcome disagreement 和技术失败率。
6. **Expert usability**：盲化来源定位成功率、解释充分性（1–5）、复核耗时和需要纠正的证据链数量。

所有 rate 必须同时保存 numerator、denominator 和 Wilson 95% interval；分母为零时结果是 `not_estimable`，不能写成 0%。paired harm loss 保存逐 case difference，并用 plan 固定 seed 的 deterministic bootstrap 生成单侧 95% upper bound；A/B win/tie/loss 另给 exact sign-test。confidence 只有在两个 arm 都输出同语义概率时才比较，否则不把 legacy confidence 与 B 的四态证据充分性伪装成校准概率。

### 11. 分阶段验收 profile 可复用但不能越权

验收阈值进入 versioned `EvaluationPlan`，不能埋在脚本里；默认 profile 是基线，医疗/医保负责人可通过新 plan 版本收紧，不能就地改报告：

| Gate | CONFORMANCE | SHADOW | PROMOTION |
|---|---:|---:|---:|
| paired/input completeness | 100% | 100% | 100% |
| schema/PHI/technical errors | 0 | 0 | 0 |
| adjudicated sample | synthetic suite 全覆盖 | ≥30 且实际出现的每个 reference class ≥5 | ≥100 且每个 reference class ≥20 |
| B 新增 safety-critical regression | 0 | 0 | 0 |
| B unsafe-auto rate | golden 为 0 | 不高于 A，paired upper bound ≤5pp | Wilson upper 95% ≤5%，且不高于 A |
| paired harm-loss B−A upper 95% | ≤0 | ≤0.05 | ≤0.02 |
| correct automation B−A lower 95% | 不适用 | ≥−5pp | ≥−5pp |
| decisive grounding / locator | 100% / 100% | ≥95% / ≥95% | 自动裁决 100% / 100%，全体 ≥98% / ≥98% |
| provenance/proof/version completeness | 100% | 100% | 100% |
| B repeat canonical stability | 100% | 100% | 100% |
| blinded explanation / source retrieval | 结构完整 | 样本足够时 median≥4/5、≥95% | median≥4/5、≥95% |

样本量或分层不足时状态必须是 `INSUFFICIENT_EVIDENCE`，不是 PASS。`PASS` 只表示满足该 profile、可以进入下一次人工决策，不等于发布或部署授权。专家 reference 必须来自与两 arm 展示顺序隔离的盲化裁定：至少两名独立 reviewer；分歧经第三方 adjudication 后形成 reference，原始意见和最终裁定均追加保留。自由文本只留在受控私有工件，公共报告使用 reason code。

### 12. 评测工件采用 canonical、append-only 引用链

评测包包含 `evaluation_plan.json`、`evaluation_cases.json`、`arm_observations.json`、`expert_adjudications.json`、`evaluation_report.json` 和 `manifest.json`。每个文件使用 canonical JSON 与 SHA-256，report 引用 plan、case、observation、adjudication digest；新一次运行创建新 evaluation ID，不覆盖旧报告。Git 只保存合成去标识 conformance 包；真实病例包必须位于受控 0700/0600 路径，公开报告只含 salted case/run refs、聚合指标和 reason code。

oncology adapter 在现有 `shadow` AuditResult 中提取同一次运行的 A/B patient-level outcome、B criterion/proof/evidence completeness 和运行成本。缺少 source snapshot checksum、arm manifest、完整配对或 candidate denominator 时报告 `INVALID`；`historical_unpaired` 数据可被引用为背景，但不得进入 paired effect 或 promotion gate。

## Risks / Trade-offs

- [v0.1 对象仍可能过宽，过早冻结字段] → 只把 specs 要求的字段设为必填，扩展 metadata 保持窄且可选；diagnosis shadow 反馈通过新 contract version 演进。
- [Fact proposition 去重可能跨 ontology version 误合并] → EntityRef/ConceptRef identity 含 exact terminology version，predicate 来自 exact OntologyRef；canonical key 包含这些语义 identity。
- [只有 checksum/locator 不等于上游数据仍可访问] → 明确 canonical artifact version 是 authority，区分“locator 自洽”和“提供 material 后内容已验证”，不承诺原始 HIS 可达。
- [UNKNOWN 被误当作疾病实体或负面事实] → validator 要求 evaluation coverage scope，并对 Unknown concept/diagnosis golden anti-case fail closed。
- [oncology adapter 映射不完整造成双 authority] → adapter 只输出 references，权威状态和 proof 永远从 eligibility_json 读取；测试断言原 payload byte-equivalent 且 verdict 不变。
- [Pydantic JSON Schema 对非 Python 消费者不够稳定] → contract_version 与 canonical fixtures 才是兼容门禁；确有第二语言消费者后再冻结导出 schema artifact。
- [专家 reference 样本小或类别失衡] → 分层最小样本门禁与 `INSUFFICIENT_EVIDENCE`，不以总体 accuracy 掩盖稀有 VIOLATION/CLEAN 类。
- [全量 INCONCLUSIVE 看起来“安全”] → unsafe error 与 correct automation/abstention 分开门禁，系统必须同时证明诚实和有用。
- [重复运行 legacy LLM 成本较高] → profile 固定重复次数并完整记录 token/latency；成本是次级指标，不能减少到无法估计稳定性。

## Migration Plan

1. 新增隔离的 evidence models、ontology pack models、canonical serializer 和纯 validator；不从现有运行时模块导入它们。
2. 增加最小 synthetic Ontology v0.1 pack、正反 golden fixtures 和离线 conformance tests，验证无网络/数据库依赖。
3. 增加 oncology-owned conformance adapter 与只读测试，证明 eligibility_json、proof tree、UNKNOWN/CONFLICT 和 legacy verdict 不变。
4. 实现 Evaluation Contract、分层 gates、canonical 评测包和 oncology same-run paired adapter；用合成 A/B fixtures 验证历史 unpaired 不会冒充 paired、样本不足不会 PASS。
5. 运行相关 pytest、Evidence/Evaluation Contract schema/golden 校验和 `openspec validate --strict`；不执行数据库 migration、真实患者评测或生产部署。

本 change 没有数据迁移和运行时开关。若需回滚，删除新增 package、adapter 和 synthetic fixtures 即可；现有审计、肿瘤资格和存储路径未接线，因此无需恢复历史数据。

## Open Questions

本 change 无阻塞问题。EvidenceBundle 的持久化布局、shadow ledger retention、跨 bundle 去重和上游 HIS resolver 只有在 diagnosis shadow 产生实测需求后，才由后续 change 决定。
