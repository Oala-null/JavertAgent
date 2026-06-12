## ADDED Requirements

### Requirement: M2-class rules complete-set ready state

After this change is applied, the rule yamls under `configs/rules/` SHALL contain a complete set of 18 rules whose `violation_type` is "过度检查" and whose `derived_from_template` equals `M2`, with `status: ready`. The 18 rules are: R109 R129 R130 R131 R141 R143 R146 R151 R153 R154 R155 R156 R160 R161 R162 R219 R220 R313.

Each rule MUST carry a non-empty `prompt_addon` (>= 500 chars), a `trigger_keywords` list (>= 3 items), a `suggested_tools` list, and a non-empty `expected_signal`. All 18 rules become ready via this change (none were ready before).

This set is one short of the design doc's 28 B-class targets — the remaining 10 (R108 R132 R218 R221 R222 R225 R278 R279 R311 R312) are MISSING (no yaml skeleton exists) and will be added by a follow-up `add-pending-rules-b-class` change.

#### Scenario: M2 set is loadable and all ready

- **WHEN** `javert list` runs after this change
- **THEN** all 18 listed rule_ids show `status=ready`, and have `derived_from_template: M2` in their yaml files
- **AND** total ready count (M1 + M2) is `ready: 33` out of 41 yamls

#### Scenario: derived_from_template invariant for M2 set

- **WHEN** any rule in the 18-id set is loaded via `load_rule(path)`
- **THEN** `Rule.derived_from_template == "M2"` AND `Rule.prompt_addon` is non-empty (length > 500 chars) AND `len(Rule.trigger_keywords) >= 3`

#### Scenario: M2 set passes audit-patient sanity on J66252

- **WHEN** `javert audit-patient J66252 --share-tool-cache --concurrency 5` runs after this change with the 18 M2 rules included (default P0+P1 selection covers all 18)
- **THEN** every rule in the 18-id set produces a verdict (V / C / I) without aborting; no rule errors out for "prompt_addon empty" reasons; total elapsed time stays within 1.5x of organize E baseline (15 M1 rules in 18.6 min) when normalized to per-rule average
