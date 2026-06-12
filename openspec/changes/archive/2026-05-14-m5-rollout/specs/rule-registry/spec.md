## ADDED Requirements

### Requirement: M5-class rules main set ready (8 rules)

After this change is applied, the rule yamls under `configs/rules/` SHALL contain a complete main subset of 8 rules whose `violation_type` is "虚构医药服务项目" and whose `derived_from_template` equals `M5`, with `status: ready` and `priority: P0`. The 8 main rules are: R015 R037 R080 R103 R105 R134 R203 R224.

Each rule MUST carry a non-empty `prompt_addon` (>= 600 chars), a `trigger_keywords` list (>= 5 items), a `suggested_tools` list, and a non-empty `expected_signal`.

#### Scenario: M5 main set is loadable and all ready

- **WHEN** `javert list` runs after this change
- **THEN** all 8 main rule_ids (R015 R037 R080 R103 R105 R134 R203 R224) show `status=ready` and `priority=P0`, and have `derived_from_template: M5` in their yaml files
- **AND** total ready count (M1 + M2 + M3 + M5 main) is `ready: 58` out of 68 yamls

### Requirement: M5-class special rules drafting (2 rules)

After this change, two H-class rules (R003 R004) SHALL exist as yaml skeletons with `status: drafting` and `priority: P2`. These rules CANNOT be audited under the current 4-tool capability (R003 needs cross-patient text similarity, R004 needs procurement data). Their `notes` field MUST state the limitation and pending change.

#### Scenario: R003/R004 are draft skeletons with explanation

- **WHEN** `javert show R003` (or R004) runs after this change
- **THEN** the rule exists with status=drafting, priority=P2, and `notes` field explains the limitation
- **AND** prompt_addon is empty (not filled), since the rule cannot be audited yet
