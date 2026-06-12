## ADDED Requirements

### Requirement: M4-class rules complete-set ready state

After this change, the rule yamls under `configs/rules/` SHALL contain a complete set of 12 rules whose `violation_type` is "超标准收费" and `derived_from_template` equals `M4`, with `status: ready` and `priority: P0`. The 12 rules are: R020 R063 R074 R165 R193 R196 R200 R212 R250 R291 R292 R293.

Each rule MUST carry a non-empty `prompt_addon` (>= 500 chars), `trigger_keywords` (>= 5 items), `suggested_tools`, non-empty `expected_signal`.

#### Scenario: M4 set is loadable and all ready

- **WHEN** `javert list` runs after this change
- **THEN** all 12 rule_ids show `status=ready` and `priority=P0` and `derived_from_template: M4`
- **AND** total ready count is `ready: 77` out of 91 yamls

#### Scenario: derived_from_template invariant for M4 set

- **WHEN** any rule in the 12-id set is loaded
- **THEN** `derived_from_template == "M4"` AND `prompt_addon` length > 500 AND `len(trigger_keywords) >= 5`

#### Scenario: M4 set passes audit-patient sanity on J66252

- **WHEN** `javert audit-patient J66252 --share-tool-cache --concurrency 5` runs
- **THEN** all 12 M4 rules produce verdicts (V/C/I) without aborting; expected J66252 M4 子集大部分 CLEAN (无关项目), R212 可能 INCONCLUSIVE (PACU 配置未知)
