## ADDED Requirements

### Requirement: M6 template loadable and complete

After this change, `configs/templates/M6.yaml` SHALL contain a complete template for "过度诊疗" (overtreatment): `master_prompt` with Jinja2 conditionals supporting `exclusion_dx_list` (hard-evidence VIOLATION) / `notes_evidence_section` / `notes_evidence_kw_list` / `special_notes` / `pilot_caveat`, plus 13 fields.

`load_template("M6")` MUST return a valid `Template` instance, `master_prompt` length MUST be >= 600 characters, and `fields` MUST include: `treatment_desc`, `treatment_kw_list`, `catalog_basis`, `indication_dx_list`, `exclusion_dx_list`, `aux_keywords`, `aux_tools`, `aux_signal`.

#### Scenario: M6 template validation passes

- **WHEN** `javert template validate M6` runs after this change
- **THEN** validation reports `ready` status

#### Scenario: M6 exclusion_dx hard-evidence branch

- **WHEN** vars include `exclusion_dx_list: ["非全麻"]` 
- **THEN** rendered prompt includes "若有任一排除指征命中 + treatment 命中 → VIOLATION (强证据)" clause
- **WHEN** `exclusion_dx_list: []` (空)
- **THEN** the exclusion clause is still present in template but with empty list (no items)
