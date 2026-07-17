## Why

Javert 当前把肿瘤药医保限定作为整段文本交给 LLM 自由解释，缺少癌种相关的病理标志物判读、治疗方案到通用名的确定性映射，以及“临床合理但文书条件未写全”的表达能力，导致同一病例出现 CLEAN/VIOLATION 摇摆。现有三个合成回归场景已分别暴露 HER2 别名漏识别、治疗周期误判为治疗线数、移植适合性文书缺口被混同为违规等问题，适合作为一期金标回归。

## What Changes

- 将现有肿瘤药医保限定编译为可版本化的条件树，逐条件输出 `SATISFIED / NOT_SATISFIED / UNKNOWN / CONFLICT`，由确定性聚合器生成资格状态和证明树，LLM 仅负责抽取候选事实。
- 建立一期病理标志物知识库，覆盖当前有效肿瘤医保限定中出现的 IHC/ISH/FISH/分子标志物、癌种上下文、别名、评分和阈值；未审核条目显式进入人工复核，不静默猜测。
- 建立独立肿瘤治疗方案知识库及 resolver，把方案名、英文缩写、商品名、通用名和医保代码关联起来，并区分拟行/已实施/既往治疗、周期数和治疗线数。
- 引入双轴结果：审核处置 `NO_VIOLATION_FOUND / VIOLATION_FOUND / REVIEW_REQUIRED` 与资格状态 `SATISFIED / NOT_SATISFIED / DOCUMENTATION_GAP / CONFLICT`；继续兼容现有 `CLEAN / VIOLATION / INCONCLUSIVE`。
- 按缺失的具体医保条件生成患者中心的文书建议，而不是给所有药品违规追加同一句复核提示。例如移植适合性文书缺口金标应提示：“患者74岁且已多线治疗；如拟使用该药，建议病程中补充‘不适合造血干细胞移植’及简要原因，避免因文书缺项影响医保报销。”
- 把尿路上皮 HER2 低表达、Pola 周期年份冲突和 Pola 移植适合性缺口制作成最小化、去标识的合成金标夹具，并增加别名、阈值、AND/OR、治疗方案边界、时间冲突和向后兼容测试。
- 一期数据范围为“当前有效肿瘤医保限定涉及的病理条件 + 当前病历语料高频肿瘤方案”；数据模型支持后续扩展到更多癌种和诊断型 IHC。
- 本期不调整专家审核按钮“认同/驳回”的产品交互，也不试图一次收录全部病理诊断抗体。

## Capabilities

### New Capabilities

- `oncology-eligibility-evaluation`: 肿瘤药医保限定条件树、四态叶子求值、确定性聚合和可追溯证明树。
- `pathology-biomarker-normalization`: 癌种相关病理标志物别名、检测方法、评分阈值、时序和冲突归一。
- `oncology-regimen-resolution`: 治疗方案别名及组分解析、药品通用名/商品名/代码关联、周期与线数分离。
- `documentation-gap-guidance`: 双轴资格结果及面向患者报销连续性的条件级文书完善建议。

### Modified Capabilities

- `drug-audit`: 肿瘤药 bulk 审计接入结构化条件求值与方案证据，同时保持单药只审一次及全退药不命中。
- `audit-engine`: `AuditResult`、落库和对外结果在保留旧 verdict 的同时携带结构化资格状态、逐条件证据和文书建议。

## Impact

- 配置与构建：新增结构化肿瘤资格规则、病理标志物和治疗方案知识库及其校验/构建脚本。
- 审计运行时：新增条件求值器、病理归一器、方案 resolver，并调整 RD04/R007 的肿瘤药协作边界，避免默认重复审核。
- 结果契约：扩展 `AuditResult` 和持久化结构；旧消费者继续读取 `verdict/reasoning/evidence`，新消费者可读取资格证明树和文书建议。
- 工具与界面：`drug_audit_lookup` 返回确定性汇总证据；工作台在现有 reasoning 旁展示资格状态与文书建议，不改变专家审核按钮语义。
- 测试：增加三例金标、知识库 schema/来源校验、规则真值表、方案边界和旧结果反序列化回归。
