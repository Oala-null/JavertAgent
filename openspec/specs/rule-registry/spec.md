# rule-registry Specification

## Purpose
TBD - created by archiving change m2-rollout. Update Purpose after archive.
## Requirements
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

### Requirement: M3-class rules complete-set ready state

After this change is applied, the rule yamls under `configs/rules/` SHALL contain a complete set of 17 rules whose `domain` is "口腔" and whose `derived_from_template` equals `M3`, with `status: ready` and `priority: P1`. The 17 rules are: R233 R234 R235 R236 R237 R238 R239 R240 R241 R242 R243 R244 (G-a 子类 12 条) and R245 R246 R247 R248 R249 (G-b 子类 5 条).

Each rule MUST carry a non-empty `prompt_addon` (>= 600 chars), a `trigger_keywords` list (>= 5 items), a `suggested_tools` list, and a non-empty `expected_signal`. All 17 rules become ready via this change (none existed as yaml before; all are init'd from 0325 xls by `init_pilot_rules`).

violation_type follows 0325 xls exact wording: R233-R244 use "虚构医药服务项目或以骗保为目的串换项目"; R245-R249 use "虚构医药服务". Both groups share `derived_from_template: M3` as the unified template anchor.

#### Scenario: M3 set is loadable and all ready

- **WHEN** `javert list` runs after this change
- **THEN** all 17 listed rule_ids show `status=ready` and `priority=P1`, and have `derived_from_template: M3` in their yaml files
- **AND** total ready count (M1 + M2 + M3) is `ready: 50` out of 58 yamls (41 pre-existing + 17 new)

#### Scenario: derived_from_template invariant for M3 set

- **WHEN** any rule in the 17-id set is loaded via `load_rule(path)`
- **THEN** `Rule.derived_from_template == "M3"` AND `Rule.prompt_addon` is non-empty (length > 600 chars) AND `len(Rule.trigger_keywords) >= 5` AND `Rule.priority == "P1"`

#### Scenario: M3 set passes audit-patient sanity on J66252 (P1)

- **WHEN** `javert audit-patient J66252 --priority P1 --share-tool-cache --concurrency 5` runs after this change with the 17 M3 rules included (plus 3 M2 P1: R154, R155, R162)
- **THEN** every rule in the 17-id M3 set produces a verdict (V / C / I) without aborting; no rule errors out for "prompt_addon empty" reasons
- **AND** all 17 M3 rules return CLEAN for J66252 (thyroid cancer patient, no dental fees/diagnoses) as expected per design doc §7

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

