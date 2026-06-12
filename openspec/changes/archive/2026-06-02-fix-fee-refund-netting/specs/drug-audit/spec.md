## ADDED Requirements

### Requirement: 用药命中扣除全退药

`drug_audit_lookup` 判定患者用药集时 SHALL 使用退费净额, **完全充退** (净 `cnt ≤ 0`) 的药 MUST NOT 计入患者用药集, 即使该药在监管知识库中有条目也 MUST NOT 命中。

#### Scenario: 全退药不命中知识库

- **WHEN** 患者某药被完全充退 (净量 0), 而该药通用名在 `drug_audit_kb.json` 中存在受监管条目
- **THEN** `lookup_patient_drugs` 不把该药列入命中 (患者实际没用), MUST NOT 产生该药的违规信号

#### Scenario: 部分退药仍命中并显示净量

- **WHEN** 患者某受监管药净量 > 0 (部分退)
- **THEN** 该药仍命中知识库, 命中信息按净量计 (不按虚高的原始行数)
