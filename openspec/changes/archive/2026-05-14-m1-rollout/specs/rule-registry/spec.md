## ADDED Requirements

### Requirement: M1-class rules complete-set ready state

After this change is applied, the rule yamls under `configs/rules/` SHALL contain a complete set of 15 rules whose `violation_type` is "重复收费" and whose `derived_from_template` equals `M1`, with `status: ready`. The 15 rules are: R045 R047 R069 R077 R112 R116 R118 R119 R185 R191 R208 R226 R228 R260 R300.

Each rule MUST carry a non-empty `prompt_addon` (>=400 chars), a `trigger_keywords` list (>=3 items), a `suggested_tools` list, and a non-empty `expected_signal`. R191 retains its hand-written content (already ready before this change); R045 was written by `add-rule-template-fitter` Phase 3 verification and is recorded here as the first M1-derived rule moved to ready. The remaining 13 rules become ready via this change.

#### Scenario: M1 set is loadable and all ready

- **WHEN** `javert list` runs after this change
- **THEN** all 15 listed rule_ids show `status=ready` and `priority=P0`, and have `derived_from_template: M1` in their yaml files

#### Scenario: derived_from_template invariant for M1 set

- **WHEN** any rule in the 15-id set is loaded via `load_rule(path)`
- **THEN** `Rule.derived_from_template == "M1"` AND `Rule.prompt_addon` is non-empty (length > 400 chars) AND `len(Rule.trigger_keywords) >= 3`

#### Scenario: M1 set passes audit-patient sanity on J66252

- **WHEN** `javert audit-patient J66252 --share-tool-cache` runs after this change with `--rules R045,R047,R069,R077,R112,R116,R118,R119,R185,R191,R208,R226,R228,R260,R300`
- **THEN** every rule produces a verdict (V / C / I) without aborting; no rule errors out for "prompt_addon empty" reasons; the run records `prompt_addon` length per rule in stderr / store, allowing operator to confirm 15 rules ran with their M1-rendered prompts
