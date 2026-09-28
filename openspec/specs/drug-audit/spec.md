# drug-audit Specification

## Purpose
TBD - created by syncing change fix-fee-refund-netting. Update Purpose after archive.
## Requirements
### Requirement: 用药命中扣除全退药

`drug_audit_lookup` 判定患者用药集时 SHALL 使用退费净额, **完全充退** (净 `cnt ≤ 0`) 的药 MUST NOT 计入患者用药集, 即使该药在监管知识库中有条目也 MUST NOT 命中。

#### Scenario: 全退药不命中知识库

- **WHEN** 患者某药被完全充退 (净量 0), 而该药通用名在 `drug_audit_kb.json` 中存在受监管条目
- **THEN** `lookup_patient_drugs` 不把该药列入命中 (患者实际没用), MUST NOT 产生该药的违规信号

#### Scenario: 部分退药仍命中并显示净量

- **WHEN** 患者某受监管药净量 > 0 (部分退)
- **THEN** 该药仍命中知识库, 命中信息按净量计 (不按虚高的原始行数)

### Requirement: bulk 规则独占药品审计

药品适应症/限定审计 MUST 仅由 bulk 规则 (R007/RD01/RD02/RD03) 产出裁决; RD10-RD37 精选单药规则 MUST 处于 abandoned 状态 (notes 注明由 bulk 独占), 不进入 `audit-patient` 默认执行集与 router index 的可执行集合. 同一患者同一药品 MUST NOT 因规则重叠产生多条 VIOLATION.

#### Scenario: 精选规则不再运行

- **WHEN** 患者使用了艾普拉唑钠, 执行 `javert audit-patient <pid> --priority all --use-router`
- **THEN** 该药仅由对应 bulk 规则审计一次, RD20 不出现在执行集合中

#### Scenario: bulk 覆盖不缩水

- **WHEN** 精选 28 条收敛后, 对同一患者重跑药品审计
- **THEN** 精选规则原可命中的药品仍全部落在 R007/RD01-03 的 `drug_audit_lookup(rule_type=...)` 命中集合内 (KB 覆盖面不变)

#### Scenario: 恢复路径存在

- **WHEN** 需要临时单独复核某一精选药品规则
- **THEN** 该 RD yaml 仍在 `configs/rules/` 且个性化字段完整, `--rules RDxx` 显式指定仍可单条运行
