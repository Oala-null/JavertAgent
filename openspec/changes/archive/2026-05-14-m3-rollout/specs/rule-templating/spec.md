## ADDED Requirements

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
