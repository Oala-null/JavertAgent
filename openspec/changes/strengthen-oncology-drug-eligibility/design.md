## Context

Javert 已有两层肿瘤药知识来源：`docs/药品限制/` 中的医保限定/说明书资料，以及编译后的 `drug_audit_kb.json`、`oncology_drug_kb.json`。运行时 `drug_audit_lookup` 能按费用代码或名称返回整段限定，但 `AuditResult` 仍只有 verdict、reasoning、evidence 等扁平字段；病理标志物、既往治疗、治疗方案和移植适合性由 LLM 在自由文本中临时解释。

这导致三个稳定性问题：

- 同一限定中的 AND/OR 条件没有独立状态，病种命中可能错误短路标志物或既往治疗条件；
- 病理结果和治疗方案存在大量别名，字面检索找不到 `CerbB2(1+)` 或无法从 `Pola-R-GemOx` 稳定解析通用名；
- “已有较强临床合理性、但医保所需文书未写全”和“存在明确反证”被压缩进同一个三态 verdict，无法同时表达患者用药连续性与文书完善需求。

一期必须离线运行、兼容既有 SQLite/SQL Server 双写与工作台消费者、不引入新的在线依赖，并保持真实患者数据不进入代码仓库或测试夹具。

## Goals / Non-Goals

**Goals:**

- 把当前有效肿瘤医保限定转换为可测试、可版本化、可追溯的条件树。
- 确定性归一病理标志物与治疗方案事实，区分明确反证、材料缺失和证据冲突。
- 在不破坏旧 verdict/API 的前提下输出资格证明树和患者中心的文书建议。
- 以尿路上皮 HER2 低表达、Pola 周期年份冲突和 Pola 移植适合性缺口的最小化合成金标夹具阻止已知误判回归。
- 为后续扩展到更多癌种、IHC 和治疗方案提供稳定数据契约及来源审核流程。

**Non-Goals:**

- 不在一期收录所有诊断型免疫组化抗体或替代 WHO/CAP/CSCO 等完整病理知识体系。
- 不让系统自动写回、回填或修改患者病历。
- 不调整专家审核按钮“认同/驳回”的交互语义。
- 不以患者年龄单独自动判定临床适合或不适合移植；年龄仅作为文书建议的上下文。
- 不让 LLM 直接决定条件树的最终真值。

## Decisions

### 1. 三个独立知识资产，共用版本与审核元数据

新增：

- `configs/oncology_eligibility_rules.json`：药品/癌种/医保限定的条件树；
- `configs/pathology_biomarker_kb.json`：标志物别名、方法、癌种相关评分和阈值；
- `configs/oncology_regimen_kb.json`：治疗方案别名、组分、癌种上下文和药品概念。

每个激活条目必须包含稳定 ID、来源引用、来源版本、`review_status=approved` 和构建版本。病历语料只能用于发现候选别名，未经审核的候选进入 review 清单，运行时不得作为确定性规则激活。

选择独立资产而非继续扩充 `drug_audit_kb.json`，是为了避免把“原始药品限定”“病理解释”“治疗方案组成”混成不可独立更新的一张大表。三个文件均由 JSON Schema/Pydantic 校验，运行时离线加载并缓存，不联网查询。

### 2. 限定编译成类型化 AST，四态叶子由确定性聚合器求值

条件节点使用：

```text
all[] / any[] / leaf
```

一期 leaf 类型至少包括：

```text
diagnosis
stage
biomarker
prior_therapy
line_of_therapy
treatment_status
clinician_assessment
```

每个叶子输出：

```text
SATISFIED
NOT_SATISFIED
UNKNOWN
CONFLICT
```

聚合规则固定为：

- `all`：任一 NOT → NOT；否则任一 CONFLICT → CONFLICT；否则任一 UNKNOWN → DOCUMENTATION_GAP；全部满足 → SATISFIED。
- `any`：任一满足 → SATISFIED；全部 NOT → NOT；否则任一 CONFLICT → CONFLICT；其余 → DOCUMENTATION_GAP。

每个节点保留原限定文本、结构化事实、原文 span、时间、来源和缺失项，组成 proof tree。LLM 可以抽取候选事实，但不能覆盖聚合器结果。

选择四态而非布尔或纯置信度，是为了让 `CerbB2(1+)` 这样的明确反证与“未见 HER2 报告”严格分开，也让旧标本与新标本冲突不会被任意覆盖。

### 3. 病理归一同时绑定 marker、method、cancer context 和 specimen time

病理事实模型至少包含：

```text
marker_id
matched_alias
method
result/score
cancer_context
specimen_site
specimen_time
raw_span
```

别名先归一 marker，再根据癌种和当前限定选择评分规则。基因扩增、基因突变和蛋白 IHC 不得互相替代，除非该限定显式允许。多个标本不静默覆盖：按规则允许的时序策略选择，无法确定时输出 CONFLICT。

例如尿路上皮癌维迪西妥单抗分支中，`HER2 / HER-2 / c-erbB-2 / CerbB2 / HER2/neu` 归一到同一 marker；`CerbB2(1+)` 形成 `HER2 IHC 1+`，明确使“HER2过表达（IHC 2+/3+）”叶子为 NOT_SATISFIED。

### 4. 方案 resolver 使用上下文最长匹配和证据优先级

方案解析流程：

1. Unicode、大小写、空格和连字符归一；
2. 在癌种上下文中做最长别名匹配；
3. 证据优先级为“同句明确药品清单 > 商品名/通用名映射 > 方案字典推导”；
4. 明确组分与字典组分不一致时输出 CONFLICT；
5. 分别输出 `treatment_status`、`cycle_no`、`line_of_therapy`，禁止由周期数推导治疗线数；
6. 费用代码作为患者实际使用药品的交叉验证，不替代方案语义。

因此 `Pola-R-GemOx` 可以解析出维泊妥珠单抗、利妥昔单抗、吉西他滨和奥沙利铂；`R-GemOx` 不得推导出 Pola；“第四次”只赋值 `cycle_no=4`，`line_of_therapy` 仍为 UNKNOWN。

### 5. 双轴结果将事实资格与审核处置分开

新增结构化结果：

```text
audit_disposition:
  NO_VIOLATION_FOUND
  VIOLATION_FOUND
  REVIEW_REQUIRED

eligibility_status:
  SATISFIED
  NOT_SATISFIED
  DOCUMENTATION_GAP
  CONFLICT
```

旧 verdict 映射保持：

```text
NO_VIOLATION_FOUND -> CLEAN
VIOLATION_FOUND    -> VIOLATION
REVIEW_REQUIRED    -> INCONCLUSIVE
```

资格聚合器只产出 `eligibility_status`；处置策略再根据缺失条件类别决定 disposition：

- 明确反证且无其他可满足分支 → VIOLATION_FOUND；
- 缺少可由本次临床评估补充的 `clinician_assessment`，同时已有充分病种/复发难治支持且无反证 → NO_VIOLATION_FOUND + DOCUMENTATION_GAP；
- 缺少无法靠本次一句评估补足的历史事实（既往药物、治疗线数、初始病程），或存在关键时间冲突 → REVIEW_REQUIRED；
- 条件全部满足 → NO_VIOLATION_FOUND + SATISFIED。

这使 Pola 移植适合性缺口金标输出 CLEAN + DOCUMENTATION_GAP，而 Pola 周期年份冲突金标输出 INCONCLUSIVE + DOCUMENTATION_GAP，并保留时间冲突 flag。

### 6. 文书建议由缺失条件模板生成，面向患者报销连续性

每个允许生成建议的叶子可配置 `documentation_template`。建议使用已证实事实填充，不由 LLM自由发挥，也不得反向修改叶子状态。

Pola 移植适合性缺口金标的一期标准输出为：

> 患者74岁且已多线治疗；如拟使用该药，建议病程中补充“不适合造血干细胞移植”及简要原因，避免因文书缺项影响医保报销。

该措辞把重点放在患者少折腾、不中断用药和减少自付风险；系统仅展示建议，不自动写回病历，也不把“建议补充”表述成“病历已经存在”。

### 7. 以单一可选 JSON 字段扩展结果和存储

`AuditResult` 增加带默认值的可选 `eligibility_evaluation`，其中包含 disposition、eligibility status、criterion assessments、proof tree、data-quality flags 和 documentation suggestions。SQLite 与 SQL Server 的 `audit_runs` 增加 nullable `eligibility_json`；旧行读取为 `None`，旧 API 字段和旧模板继续工作。

选择单一 JSON 字段而非立即拆多张关系表，是为了降低一期迁移和双写风险；稳定后再评估对常用条件建立索引列。工作台只新增资格状态和文书建议区，不改变现有专家审核动作。

### 8. RD04 成为肿瘤医保限定 bulk 入口，R007 排除同一范围

迁移期间 RD04 保持 abandoned，只能显式运行做 shadow validation。达到金标和批量回归门槛后：

- RD04 转 ready，处理 `oncology=true AND source_type=insurance`；
- R007 继续处理非肿瘤限适应症，并排除上述集合；
- RD10-RD37 继续 abandoned；
- 同一患者同一药品/限定分支只允许一个执行入口。

通过 `JAVERT_ONCOLOGY_ELIGIBILITY_V2` 控制新执行路径。关闭开关即可回到旧 bulk 行为，数据库新增字段保持可空，不需要回滚数据。

### 9. 四条并行实施线先冻结接口，再分别开发

实现前先冻结 `NormalizedFact`、`CriterionAssessment`、`EligibilityEvaluation` 三个契约。随后分为：

- A：条件 AST、聚合器、病理知识库与归一器；
- B：治疗方案知识库与 resolver；
- C：结果模型、持久化、文书建议和工作台展示；
- Integration：RD04/R007/lookup 接线、三例金标与全候选回归。

前三条 worktree 不共同修改 `runner.py`、RD04 或 `drug_audit_lookup.py`；这些共享文件只由 Integration 线修改，减少合并冲突。

## Risks / Trade-offs

- [知识库不完整或过期] → 激活条目必须有来源版本和 approved 状态；构建报告列出未编译/待审核限定，禁止静默回退成肯定结论。
- [方案缩写歧义导致错推药品] → 癌种上下文、最长匹配、显式组分优先和 AMBIGUOUS/CONFLICT 状态共同约束。
- [文书建议被误解为系统已确认条件] → 建议与证据分区展示，使用“如拟使用，建议补充”措辞，不改变 condition status。
- [双轴结果让旧消费者失效] → 旧 verdict、reasoning、evidence 保持必填；新字段可空，旧记录与旧 API 回归测试覆盖。
- [RD04 与 R007 重复执行] → 激活前增加路由互斥测试和同患者同药唯一性断言；迁移期 RD04 仅显式 shadow。
- [LLM 抽取仍有波动] → 最终真值只由确定性归一和聚合器产生；金标重复运行必须得到相同结构化结果。
- [JSON 字段不便统计] → 一期优先降低迁移风险；后续根据真实查询需求再物化常用索引列。

## Migration Plan

1. 增加三个知识库 schema、构建器和审核报告，不接入生产裁决。
2. 实现 AST、病理归一和方案 resolver，使用合成夹具及三例最小化金标验证。
3. 扩展 `AuditResult` 与 nullable `eligibility_json`，验证旧记录、SQLite、SQL Server 和 API 向后兼容。
4. 在 `JAVERT_ONCOLOGY_ELIGIBILITY_V2=shadow` 下显式运行 RD04，对全部现有候选生成对照报告，不改变线上 verdict。
5. 达到验收门槛后启用 v2，RD04 转 ready，R007 排除肿瘤医保限定集合；监测 verdict drift、INCONCLUSIVE 率和专家一致率。
6. 回滚时关闭 feature flag 并恢复旧路由；新增 JSON 字段保留但不读取。

## Open Questions

- 病理/方案条目的最终审批角色和发布频率是否由医院药师、病理科与医保办共同维护；一期默认以代码评审中的 `review_status=approved` 作为发布闸。
- 文书建议模板是否需要按医院品牌口吻定制；一期先提供配置化中文模板，不把措辞硬编码进 evaluator。
- 七月诊疗项目目录是否替换四月版本属于独立数据升级，不阻塞本 change。
