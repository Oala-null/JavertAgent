## ADDED Requirements

### Requirement: M5 template loadable and complete

After this change, `configs/templates/M5.yaml` SHALL contain a complete template definition for "虚构医药服务" (phantom service): a non-empty `master_prompt` with Jinja2 conditionals supporting `dept_check` / `supporting_dx_kw_list` / `special_notes` / `pilot_caveat` / `inconclusive_addendum`, plus `keywords_template`, `tools_template`, `signal_template`, and a `fields` list covering 12 field declarations.

`load_template("M5")` MUST return a valid `Template` instance, `master_prompt` length MUST be >= 700 characters, and `fields` MUST include at minimum: `service_desc`, `service_kw_list`, `catalog_basis`, `execution_evidence_kw_list`, `notes_section_hints`, `aux_keywords`, `aux_tools`, `aux_signal`.

#### Scenario: M5 template validation passes

- **WHEN** `javert template validate M5` runs after this change
- **THEN** validation reports `ready` status; all required fields present; jinja2 syntax valid

#### Scenario: M5 template can render with vars

- **WHEN** `javert prompt-fit R134 --template M5 --vars docs/m5_R134_vars.json --dry-run` runs after this change
- **THEN** rendering succeeds; output contains the 6-step audit logic (fee 命中检测 → notes 执行证据检索 → 缺失则 V)
