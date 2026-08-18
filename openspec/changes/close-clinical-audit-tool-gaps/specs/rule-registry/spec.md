## ADDED Requirements

### Requirement: 眼科专家扩展规则注册

规则库 SHALL 新增 `R319-R322` 四条专家扩展规则，分别覆盖眼科计价对账、睑板腺治疗执行举证、床头心电图现场核查和 A/B 超联合指征。四条规则 MUST 为 ready，带非空 prompt、关键词、编码、建议工具、预期信号和 precheck，并在 notes 中声明专家扩展来源。

#### Scenario: 新规则可加载

- **WHEN** 运行 `javert list` 和严格规则加载
- **THEN** R319、R320、R321、R322 均可加载且状态为 ready

#### Scenario: 无目标费用零 LLM

- **WHEN** 患者不含对应眼科收费
- **THEN** presence/presence_review/coexist_review precheck 直接 CLEAN，四条规则不调用 LLM

### Requirement: Router 索引与 YAML 一致

修改规则后系统 MUST 重建 `data/router/javert_rules_index.json`，索引中的状态、关键词和编码 MUST 与 YAML 一致。

#### Scenario: 构建后无漂移

- **WHEN** 运行规则映射构建和一致性测试
- **THEN** R319-R322 的状态、关键词和编码在 YAML 与 Router 索引中完全一致
