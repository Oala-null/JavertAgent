## ADDED Requirements

### Requirement: AuditResult 携带可空临床条件求值

`AuditResult` SHALL 在既有字段之外新增可空顶层字段 `clinical_criteria_evaluation`。CD 规则完成结构化求值时，该对象 MUST 携带 criteria 标识、版本/revision/checksum、病种、`execution_status`、四态逐节点 `proof_tree`、`qualified: true | false | null`、`qualification_disposition: QUALIFIED | NOT_QUALIFIED | REVIEW_REQUIRED`、证据锚点、缺失材料和数据质量标志；未接入临床条件树的规则 MUST 使用 `clinical_criteria_evaluation=None`。`execution_status=BLOCKED` 是根级执行控制态而非第五种节点真值，此时 MUST 有 `qualified=null`、`qualification_disposition=REVIEW_REQUIRED`，且不得启动根节点聚合。

#### Scenario: CD 规则返回完整结构化结果

- **WHEN** CD02 使用已审核且未阻断的 criteria revision 对候选完成求值
- **THEN** `AuditResult.clinical_criteria_evaluation` 包含版本校验信息、资格投影、逐节点 proof tree 和可追溯证据，既有 `verdict/reasoning/evidence/tool_calls` 字段仍存在

#### Scenario: 普通规则不伪造临床条件结果

- **WHEN** 一条既有 R/RD 规则未接入慢病条件树并完成审核
- **THEN** `clinical_criteria_evaluation=None`，其既有结果字段与变更前保持一致

#### Scenario: 阻断标准不执行根聚合

- **WHEN** CD 规则引用的 criteria `execution_status=BLOCKED`
- **THEN** 结果为 `qualified=null`、`qualification_disposition=REVIEW_REQUIRED`，proof 中保留阻断原因，但不把 BLOCKED 写成任一叶节点 state

### Requirement: 慢病资格发现确定性投影为非违规兼容 verdict

本变更只在普通病例中发现慢病资格，不含“已经申报或领取待遇”的 claim 输入，因此系统 SHALL 确定性投影旧 verdict：`QUALIFIED → CLEAN`、`NOT_QUALIFIED → CLEAN`、`REVIEW_REQUIRED → INCONCLUSIVE`；`execution_status=BLOCKED` 同样 SHALL 通过 `REVIEW_REQUIRED` 投影为 INCONCLUSIVE。普通诊断名、ICD 或关键词召回本身只产生 candidate，MUST NOT 直接产生资格结论。`qualified=true` 只表示满足慢病认定标准，`qualified=false` 只表示现有证据不满足当前标准，两者都 MUST NOT 被解释为违规或进入普通违规统计；结构化资格轴 SHALL 保留两者差异。若未来审核“已申报却不符合”的合规性，MUST 另建包含 claim 上下文的 capability，不得在本变更中偷渡该语义。

#### Scenario: QUALIFIED 兼容为 CLEAN 而非违规

- **WHEN** CD 求值根节点为 `SATISFIED` 并得到 `qualified=true`、`qualification_disposition=QUALIFIED`
- **THEN** 兼容 verdict 为 CLEAN，系统不得生成 VIOLATION，Workbench 可从结构化字段显示“符合慢病认定标准”

#### Scenario: 明确不满足仍是非违规资格发现结果

- **WHEN** CD 候选完成求值，根节点为 `NOT_SATISFIED` 且 disposition 为 `NOT_QUALIFIED`
- **THEN** 兼容 verdict 为 CLEAN，并由结构化字段保留 `qualified=false` 及决定性反证节点；该结果不进入违规数量或违规率

#### Scenario: 缺失冲突或阻断进入人工复核

- **WHEN** 根节点为 `UNKNOWN` 或 `CONFLICT`，或 criteria `execution_status=BLOCKED`
- **THEN** `qualified=null`、`qualification_disposition=REVIEW_REQUIRED` 且兼容 verdict=INCONCLUSIVE，不得把缺失事实当阴性

### Requirement: 临床条件求值不改变既有 precheck 语义

新增 CD 确定性求值 MUST 保留 canonical audit-engine 中“带 precheck 字段规则的 precheck-first 执行路径”的全部既有语义。既有规则的 precheck `clean` 仍须零 LLM、零工具调用短路 CLEAN；`facts` 仍须注入事实并在 VIOLATION 时合并费用 evidence；`skip`、无 precheck 或开关关闭仍须走原 LLM loop。慢病条件树求值是独立路径，MUST NOT 扩张通用费用 precheck 的职责，也 MUST NOT 以费用或文书缺失把 CD 条件短路为 CLEAN/NOT_QUALIFIED。

#### Scenario: 既有 precheck clean 仍零调用

- **WHEN** 一条既有 M1 规则的 precheck 返回 `clean`
- **THEN** Runner 仍返回 CLEAN、没有 LLM/tool 调用并保留原 precheck 标签，`clinical_criteria_evaluation=None`

#### Scenario: 既有 precheck facts 仍合并费用证据

- **WHEN** 一条既有规则的 precheck 返回 `facts` 且后续 LLM 判 VIOLATION
- **THEN** 最终 evidence 仍含原确定性费用锚点，新增慢病字段不覆盖或改变该证据

#### Scenario: CD 缺失资料不借 precheck 误判

- **WHEN** CD 条件所需检验或文书数据不可得
- **THEN** 对应节点按临床条件合同得到 `UNKNOWN` 并投影 REVIEW_REQUIRED/INCONCLUSIVE，不得借用通用 precheck 的 CLEAN 短路

### Requirement: 临床条件投影不改变 verdict gate 只降不升

新增 CD 路径 MUST 保留现有 `apply_gate` 只对 VIOLATION 生效且只降不升的语义。CD 资格发现只产生兼容 CLEAN 或 INCONCLUSIVE，因此 MUST NOT 进入 VIOLATION gate，也不得被 gate 提升为 VIOLATION；gate MUST NOT 改写 `clinical_criteria_evaluation` 中的节点状态、`qualified` 或 `qualification_disposition`。非 CD 规则的 gate 顺序、阈值、标签和结果 MUST 保持不变。

#### Scenario: QUALIFIED 不被 gate 反向升为违规

- **WHEN** CD 求值得到 QUALIFIED 并投影为 CLEAN
- **THEN** `apply_gate` 不把结果改成 VIOLATION，结构化资格与兼容 verdict 保持 `QUALIFIED/CLEAN`

#### Scenario: NOT_QUALIFIED 不进入违规 gate

- **WHEN** CD 求值得到 NOT_QUALIFIED 并投影为 CLEAN
- **THEN** 结果不进入只处理 VIOLATION 的 gate，`qualified=false`、disposition、proof tree 与兼容 CLEAN 均保持不变

#### Scenario: 普通规则 gate 回归不变

- **WHEN** 一条既有 R/RD 规则走完原 precheck/LLM 路径并进入 `apply_gate`
- **THEN** gate 的适用条件、只降不升、confidence 归一和标签行为与变更前一致

### Requirement: 临床条件结果独立持久化且旧行兼容

SQLite `audit_runs` 与 SQL Server `javert_audit_runs` SHALL 使用独立 nullable `clinical_criteria_json` 字段持久化 `clinical_criteria_evaluation` 的完整 JSON，schema 迁移 MUST 幂等。该字段 MUST NOT 复用或改写肿瘤专用 `eligibility_json`。写入、受控跨库同步、读取及 API/SSE 序列化 SHALL 保持对象字段不丢失，并只以新增可空字段扩展既有契约。变更前没有该列或值为 NULL/空值的历史行 MUST 加载为 `clinical_criteria_evaluation=None`，不得回填、重判或修改旧 verdict、reasoning、evidence、eligibility 及审核状态。

#### Scenario: 新结构化结果双库往返不丢失

- **WHEN** 一条含 proof tree、证据、缺失材料和 criteria checksum 的 CD 结果写入 SQLite、同步 SQL Server并分别读回
- **THEN** 两端反序列化的 `clinical_criteria_evaluation` 语义等价，batch tag 与既有审核字段也保持一致

#### Scenario: 旧表幂等增加可空列

- **WHEN** SQLite 或 SQL Server 旧表尚无 `clinical_criteria_json` 并连续执行两次 schema ensure
- **THEN** 系统只增加一次 nullable 列，不删除、重建或改写历史行

#### Scenario: 历史行缺少新字段仍可读取

- **WHEN** 读取一条变更前创建且没有 `clinical_criteria_json` 值的 R/RD 审核记录
- **THEN** 记录正常构造为 `clinical_criteria_evaluation=None`，旧 verdict、reasoning、evidence、肿瘤 eligibility 与专家审核状态逐字保留

#### Scenario: API 与 SSE 只加不改

- **WHEN** API 或 SSE 返回带 `clinical_criteria_evaluation` 的新 CD 结果
- **THEN** 既有顶层字段名称和语义保持不变，不读取新字段的旧消费者仍可解析；返回历史行时新字段为 null 或省略的兼容形式
