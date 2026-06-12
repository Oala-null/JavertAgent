## ADDED Requirements

### Requirement: M4 template loadable and complete

After this change, `configs/templates/M4.yaml` SHALL contain a complete template for "超标准收费" (overcharge): `master_prompt` with quantitative calculation guidance (catalog unit vs actual event count), plus 14 fields including `service_name`, `catalog_unit`, `addon_rule_text`, `violation_pattern`, `actual_event_kw_list`, `expected_calc_hint`.

`load_template("M4")` MUST return a valid `Template` instance, `master_prompt` length MUST be >= 800 characters.

#### Scenario: M4 template validation passes

- **WHEN** `javert template validate M4` runs after this change
- **THEN** validation reports `ready` status

#### Scenario: M4 template can render with vars

- **WHEN** `javert prompt-fit R074 --template M4 --vars docs/m4_R074_vars.json --dry-run` runs
- **THEN** rendering succeeds; output contains catalog 条款 (单位=次, 基础价 399, 加成规则原文) + 6 步审计逻辑
