## ADDED Requirements

### Requirement: M2 template loadable and complete

After this change, `configs/templates/M2.yaml` SHALL contain a complete template definition for "过度检查" (overuse screening): a non-empty `master_prompt` with Jinja2 conditionals supporting drug-check branch / single-count INCONCLUSIVE branch / special_notes / pilot_caveat, plus `keywords_template`, `tools_template`, `signal_template`, and a `fields` list covering 14-16 field declarations.

`load_template("M2")` MUST return a valid `Template` instance (validation passes), `master_prompt` length MUST be >= 800 characters, and `fields` MUST include at minimum: `exam_concept_desc`, `catalog_basis`, `exam_kw_primary_list`, `indication_dx_list`, `min_count`, `drug_check`, `aux_keywords`, `aux_tools`, `aux_signal`.

#### Scenario: M2 template validation passes

- **WHEN** `javert template validate M2` runs after this change
- **THEN** validation reports `ready` status (not `empty` / `partial` / `error`); all required fields present; jinja2 syntax valid

#### Scenario: M2 template can render with vars

- **WHEN** `javert prompt-fit R151 --template M2 --vars docs/m2_R151_vars.json --dry-run` runs after this change
- **THEN** rendering succeeds without `UndefinedError`; output contains the 6-step audit logic + 8+ indication diagnoses + min_count=2 reference + single-count INCONCLUSIVE clause

#### Scenario: M2 template conditional branches work

- **WHEN** vars include `drug_check: true` + `drug_kw_list: [...]`
- **THEN** rendered prompt includes "特殊条件: 若 search_fees(keyword 任一为 ...)" clause
- **WHEN** vars include `drug_check: false` (or omit)
- **THEN** rendered prompt does NOT include any drug-check clause
