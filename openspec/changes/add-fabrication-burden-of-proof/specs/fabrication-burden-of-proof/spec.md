# fabrication-burden-of-proof Spec Delta

## ADDED Requirements

### Requirement: M5 虚构类证据不足默认 INCONCLUSIVE

M5 (虚构医药服务) 模板派生规则的裁决语义 MUST 满足: 治疗/手术类收费项目存在, 且文书中找不到该操作的执行证据时, 默认裁决为 INCONCLUSIVE (证据缺失待人工核查), MUST NOT 因"文书未提及"而判 CLEAN; 仅当文书**正面反证**该操作确实执行 (操作记录 / 治疗单 / 执行记载) 时才判 CLEAN。该语义 MUST 落在 `configs/templates/M5.yaml` master prompt 层并经重渲染进入派生规则, MUST NOT 改动 runner 引擎。

#### Scenario: 收费存在 + 文书零执行记录 → I 而非 C

- **WHEN** 某 M5 规则命中治疗费收费行, 且 search_notes 未找到任何该操作的执行记载
- **THEN** 裁决为 INCONCLUSIVE (进专家队列), 不是 CLEAN

#### Scenario: 文书正面反证 → CLEAN

- **WHEN** 收费行存在, 且操作记录明确记载该操作已执行 (含操作名/时间/过程)
- **THEN** 裁决为 CLEAN

### Requirement: 名称不匹配警示

M5 模板 master prompt MUST 含名称不匹配警示: 收费项目名与文书术式名常不一致, MUST NOT 仅因名称不同断言"未收费"或"未执行", 应用解剖部位与操作类别交叉核对。

#### Scenario: 名称差异不再产生断言

- **WHEN** 文书记载「尺骨截骨术」而费用单为「肘关节截骨术」
- **THEN** LLM 推理不得出现"完全没有尺骨截骨术收费记录"式断言, 而是按部位/类别交叉核对

### Requirement: 脑血管介入溶栓虚构规则

系统 MUST 有规则覆盖"收取经皮穿刺脑血管腔内溶栓术费用, 但手术记录无溶栓操作且全费用单无任何溶栓药"的违规形态 (优先细化 0325 清单既有 H 类条目, 无对应条目则新建)。该规则 MUST 声明 companion 模式 precheck: A=溶栓术式费用, B=溶栓药 (尿激酶/阿替普酶/rt-PA/替奈普酶/瑞替普酶 等)。

#### Scenario: 专家案例 211351896 被接住

- **WHEN** 对患者 211351896 跑该规则 (收溶栓术 ¥1100, 手术记录为取栓无溶栓, 费用单零溶栓药)
- **THEN** 裁决至少 INCONCLUSIVE, evidence 点名溶栓术费用行与配套药缺失事实

### Requirement: 内镜治疗虚构规则

系统 MUST 有规则覆盖"收取经内镜特殊治疗类费用, 但操作记录仅见检查/染色、无任何治疗操作"的违规形态 (优先细化既有条目, 否则新建)。

#### Scenario: 专家案例 211427558 被接住

- **WHEN** 对患者 211427558 跑该规则 (收「经胃镜特殊治疗」¥400, 12/22 操作记录仅胃镜检查+染色, 抬举征阴性后未治疗)
- **THEN** 裁决至少 INCONCLUSIVE, evidence 点名该治疗费行与操作记录不含治疗操作
