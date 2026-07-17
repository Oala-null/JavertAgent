# drug-audit — delta spec

## MODIFIED Requirements

### Requirement: 用药命中扣除全退药

`drug_audit_lookup` 判定患者用药集时 SHALL 使用退费净额, **完全充退** (净 `cnt ≤ 0`) 的药 MUST NOT 计入患者用药集, 即使该药在监管知识库中有条目、病历出现其商品名或治疗方案 resolver 推导出该药组分, 也 MUST NOT 进入结构化资格求值或产生违规信号。

#### Scenario: 全退药不命中知识库

- **WHEN** 患者某药被完全充退 (净量 0), 而该药通用名在 `drug_audit_kb.json` 中存在受监管条目
- **THEN** `lookup_patient_drugs` 不把该药列入命中 (患者实际没用), MUST NOT 产生该药的违规信号

#### Scenario: 部分退药仍命中并显示净量

- **WHEN** 患者某受监管药净量 > 0 (部分退)
- **THEN** 该药仍命中知识库, 命中信息按净量计 (不按虚高的原始行数)

#### Scenario: 全退药虽在方案文书中出现仍不进入资格求值

- **WHEN** 某肿瘤药净量为 0, 但病历记录的方案别名可解析出该药为组分
- **THEN** 方案解析结果只能作为病历事实保留, `drug_audit_lookup` MUST NOT 把该药作为医保支付审计候选, MUST NOT 为它生成 `eligibility_evaluation` 或裁决

### Requirement: bulk 规则独占药品审计

药品适应症/限定审计 MUST 仅由 bulk 规则 (`R007`/`RD01`/`RD02`/`RD03`/`RD04`) 产出裁决; `RD10`-`RD37` 精选单药规则 MUST 处于 abandoned 状态 (notes 注明由 bulk 独占), 不进入 `audit-patient` 默认执行集与 router index 的可执行集合。肿瘤药中 `rule_type=限适应症`、`source_type=insurance` 的条目 MUST 由 `RD04` 作为默认唯一所有者, `R007` MUST 排除同一患者、同一药品、同一医保限定依据; 非肿瘤的限适应症条目继续由 `R007` 审计。同一患者同一药品同一依据 MUST NOT 因规则重叠产生多条 VIOLATION。

#### Scenario: 精选规则不再运行

- **WHEN** 患者使用了艾普拉唑钠, 执行 `javert audit-patient <pid> --priority all --use-router`
- **THEN** 该药仅由对应 bulk 规则审计一次, `RD20` 不出现在执行集合中

#### Scenario: bulk 覆盖不缩水

- **WHEN** 精选 28 条收敛后, 对同一患者重跑药品审计
- **THEN** 精选规则原可命中的药品仍全部落在 `R007`/`RD01`-`RD04` 的 `drug_audit_lookup` 命中集合内 (KB 覆盖面不变)

#### Scenario: 恢复路径存在

- **WHEN** 需要临时单独复核某一精选药品规则
- **THEN** 该 RD yaml 仍在 `configs/rules/` 且个性化字段完整, `--rules RDxx` 显式指定仍可单条运行

#### Scenario: 肿瘤医保限定默认只由 RD04 审计

- **WHEN** 默认审计集同时加载 `RD04` 与 `R007`, 且患者某肿瘤药命中 `source_type=insurance` 的医保限定条目
- **THEN** 该药及该限定依据只进入 `RD04`, `R007` 的候选集中不再出现, 最终只有一条该药的审核结果

#### Scenario: 非肿瘤限适应症继续由 R007 审计

- **WHEN** 患者命中一个不属于肿瘤药知识库的限适应症药品
- **THEN** 该药继续进入 `R007` 而不进入 `RD04`, 既有非肿瘤药覆盖不缩水

#### Scenario: 显式组合运行仍不得重复裁决

- **WHEN** 操作者显式指定同时运行 `RD04` 与 `R007`
- **THEN** 系统仍按药品概念、患者收费和依据版本执行所有权去重, 同一药品同一依据 MUST NOT 产生两条裁决

## ADDED Requirements

### Requirement: 肿瘤药 bulk 命中接入结构化资格求值

`RD04` 的每个有效收费候选 SHALL 调用版本化肿瘤医保资格求值器, 并在 `drug_audit_lookup` 的既有原始 fee 名、通用名、`rule_type`、依据原文和诊断字段之外返回可追溯的 `eligibility_evaluation`。该对象 MUST 包含 `audit_disposition`、`eligibility_status`、`criterion_assessments[]`、`proof_tree`、`data_quality_flags[]` 和 `documentation_suggestions[]`; 每个条件的状态 MUST 来自确定性求值结果 `SATISFIED / NOT_SATISFIED / UNKNOWN / CONFLICT`, 且 MUST 保留条件 ID、规范化事实、期望条件和证据锚点。LLM 可以抽取候选事实和生成自然语言摘要, 但 MUST NOT 覆盖确定性条件状态或把 `UNKNOWN` 猜成已满足。

#### Scenario: 尿路上皮 HER2 低表达金标的 CerbB2 1+ 成为明确反证

- **WHEN** 尿路上皮 HER2 低表达合成金标夹具使用维迪西妥单抗, 病理文书为 `CerbB2(1+)`, 且未找到既往含铂化疗的可靠证据
- **THEN** `CerbB2` 被归一为 HER2, HER2 IHC 条件为 `NOT_SATISFIED`, 既往含铂条件保持 `UNKNOWN`, `proof_tree` 引用对应病理锚点, 系统 MUST NOT 把“本次化疗后无不良反应”解释为既往含铂治疗

#### Scenario: 结构化求值保留限定版本与来源

- **WHEN** 同一药品存在多个生效期或不同适应症分支的医保限定
- **THEN** `eligibility_evaluation` 指向本次求值采用的规则版本、适应症分支和来源锚点, 不把其他版本或其他癌种的条件混入证明树

#### Scenario: 未审核规则不静默自由解释

- **WHEN** 某肿瘤药医保限定尚未编译或其结构化条目 `review_status` 不是已审核状态
- **THEN** 该候选进入 `REVIEW_REQUIRED`/`DOCUMENTATION_GAP` 路径并记录数据质量标志, MUST NOT 退回由 LLM 静默猜测为 CLEAN 或 VIOLATION

#### Scenario: 非肿瘤 bulk 输出保持兼容

- **WHEN** `R007`、`RD01`、`RD02` 或 `RD03` 审计一个未接入肿瘤资格求值器的既有药品
- **THEN** `drug_audit_lookup` 的原始 fee 名、通用名、`rule_type`、`basis` 和诊断字段保持原语义, 结构化扩展不得破坏既有消费者

### Requirement: 肿瘤药 bulk 命中携带治疗方案证据

对进入 `RD04` 的药品候选, `drug_audit_lookup` SHALL 合并治疗方案 resolver 的结构化 `regimen_evidence[]`, 每条至少包含 `regimen_id`、`canonical_name`、`components`、`event_status` (`PLANNED / ADMINISTERED / HISTORICAL`)、`cycle_no`、`line_of_therapy`、癌种上下文状态、来源锚点和冲突列表。显式药品名称或商品名证据 MUST 优先于方案组分推导; 方案证据只能补充候选药品的资格条件, MUST NOT 绕过收费净额创建新的医保支付候选。周期数与治疗线数 MUST 为独立字段, 任一缺失时保持未知。

#### Scenario: Pola 周期冲突金标的 Pola-R-GemOx 解析到通用名

- **WHEN** Pola 周期冲突合成金标夹具记录“第四次 Pola-R-GemOx 方案化疗”并列出“优罗华”
- **THEN** resolver 返回规范方案及包含维泊妥珠单抗、利妥昔单抗、吉西他滨、奥沙利铂的组分, `event_status=ADMINISTERED`, `cycle_no=4`, `line_of_therapy` 保持未知, MUST NOT 因“第四次”推导为第四线、复发或难治

#### Scenario: 不含 Pola 的 R-GemOx 不得推导维泊妥珠单抗

- **WHEN** 病历只记录 `R-GemOx` 且没有维泊妥珠单抗的显式药名、商品名或有效收费
- **THEN** `regimen_evidence.components` 不包含维泊妥珠单抗, 系统 MUST NOT 据此生成该药的医保审计候选

#### Scenario: 明示组分与方案字典冲突时进入复核

- **WHEN** 病历显式列出的药品组分与匹配方案字典不一致
- **THEN** 显式组分作为优先证据, 差异写入 `conflicts`, 相应条件保持 `CONFLICT` 或 `UNKNOWN` 并进入人工复核, MUST NOT 选择有利的一侧强行裁决

#### Scenario: 时间冲突不被方案解析掩盖

- **WHEN** 同一治疗事件的住院日期、出院记录日期或给药日期互相矛盾
- **THEN** `regimen_evidence.conflicts` 或 `data_quality_flags` 包含时间冲突, 依赖该时间顺序的治疗线数条件 MUST NOT 被判定为已满足
