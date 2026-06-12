# rule-templating Specification

## Purpose
TBD - created by archiving change m2-rollout. Update Purpose after archive.
## Requirements
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

### Requirement: M3 template loadable and complete

After this change, `configs/templates/M3.yaml` SHALL contain a complete template definition for "口腔串换" (dental hijack): a non-empty `master_prompt` with Jinja2 conditionals supporting dental_dept_check / self_pay_bonus_list / total_amount_threshold / special_notes / pilot_caveat, plus `keywords_template`, `tools_template`, `signal_template`, and a `fields` list covering 14-16 field declarations.

`load_template("M3")` MUST return a valid `Template` instance (validation passes), `master_prompt` length MUST be >= 800 characters, and `fields` MUST include at minimum: `hijacked_surgery_desc`, `hijacked_surgery_kw_list`, `catalog_basis`, `trivial_dx_kw_list`, `supporting_dx_kw_list`, `dental_dept_check`, `aux_keywords`, `aux_tools`, `aux_signal`.

#### Scenario: M3 template validation passes

- **WHEN** `javert template validate M3` runs after this change
- **THEN** validation reports `ready` status (not `empty` / `partial` / `error`); all required fields present; jinja2 syntax valid

#### Scenario: M3 template can render with vars

- **WHEN** `javert prompt-fit R245 --template M3 --vars docs/m3_R245_vars.json --dry-run` runs after this change
- **THEN** rendering succeeds without `UndefinedError`; output contains the 7-step audit logic + dental_dept_check clause + supporting_dx + trivial_dx + 自费项加分

#### Scenario: M3 template subclass support

- **WHEN** vars include `subclass: "G-a"` (美容修复套医保大手术)
- **THEN** rendered prompt logic works the same way; subclass is metadata-only and does not break rendering
- **WHEN** vars include `subclass: "G-b"` (普通诊治虚增手术)
- **THEN** rendering similarly succeeds

### Requirement: M5 template loadable and complete

After this change, `configs/templates/M5.yaml` SHALL contain a complete template definition for "虚构医药服务" (phantom service): a non-empty `master_prompt` with Jinja2 conditionals supporting `dept_check` / `supporting_dx_kw_list` / `special_notes` / `pilot_caveat` / `inconclusive_addendum`, plus `keywords_template`, `tools_template`, `signal_template`, and a `fields` list covering 12 field declarations.

`load_template("M5")` MUST return a valid `Template` instance, `master_prompt` length MUST be >= 700 characters, and `fields` MUST include at minimum: `service_desc`, `service_kw_list`, `catalog_basis`, `execution_evidence_kw_list`, `notes_section_hints`, `aux_keywords`, `aux_tools`, `aux_signal`.

#### Scenario: M5 template validation passes

- **WHEN** `javert template validate M5` runs after this change
- **THEN** validation reports `ready` status; all required fields present; jinja2 syntax valid

#### Scenario: M5 template can render with vars

- **WHEN** `javert prompt-fit R134 --template M5 --vars docs/m5_R134_vars.json --dry-run` runs after this change
- **THEN** rendering succeeds; output contains the 6-step audit logic (fee 命中检测 → notes 执行证据检索 → 缺失则 V)

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

### Requirement: M4 template loadable and complete

After this change, `configs/templates/M4.yaml` SHALL contain a complete template for "超标准收费" (overcharge): `master_prompt` with quantitative calculation guidance (catalog unit vs actual event count), plus 14 fields including `service_name`, `catalog_unit`, `addon_rule_text`, `violation_pattern`, `actual_event_kw_list`, `expected_calc_hint`.

`load_template("M4")` MUST return a valid `Template` instance, `master_prompt` length MUST be >= 800 characters.

#### Scenario: M4 template validation passes

- **WHEN** `javert template validate M4` runs after this change
- **THEN** validation reports `ready` status

#### Scenario: M4 template can render with vars

- **WHEN** `javert prompt-fit R074 --template M4 --vars docs/m4_R074_vars.json --dry-run` runs
- **THEN** rendering succeeds; output contains catalog 条款 (单位=次, 基础价 399, 加成规则原文) + 6 步审计逻辑

