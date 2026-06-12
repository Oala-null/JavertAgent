## ADDED Requirements

### Requirement: M6-class rules main set ready (7 rules)

After this change, the rule yamls under `configs/rules/` SHALL contain a complete main subset of 7 rules whose `violation_type` is "过度诊疗" (or includes "过度诊疗或虚构医药服务项目" for R225) and whose `derived_from_template` equals `M6`, with `status: ready` and `priority: P0`. The 7 rules are: R218 R221 R222 R225 R310 R311 R312.

Each rule MUST carry a non-empty `prompt_addon` (>= 500 chars), a `trigger_keywords` list (>= 5 items), a `suggested_tools` list, and a non-empty `expected_signal`.

#### Scenario: M6 main set is loadable and all ready

- **WHEN** `javert list` runs after this change
- **THEN** all 7 main rule_ids show `status=ready` and `priority=P0`, and have `derived_from_template: M6`
- **AND** total ready count is `ready: 65` out of 82 yamls

### Requirement: M6 jumbled-category rules drafting (7 rules)

After this change, 7 jumbled-category rules (R280 R281 R282 R283 R284 R285 R286) SHALL exist as yaml skeletons with `status: drafting` and `priority: P2`. These rules CANNOT be audited under current 4-tool capability (require cross-patient stats, identity verification, behavior auditing, or ICD code tampering detection).

#### Scenario: M6 jumbled set has explanatory notes

- **WHEN** any of R280-R286 is loaded
- **THEN** rule exists with status=drafting, priority=P2, and `notes` field explains the limitation
