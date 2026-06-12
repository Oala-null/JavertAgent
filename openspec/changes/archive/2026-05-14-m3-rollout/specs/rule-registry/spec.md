## ADDED Requirements

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
