## ADDED Requirements

### Requirement: Drug-rule ID namespace

The `Rule.rule_id` validation pattern SHALL accept a drug-rule namespace in addition to the existing `R\d{3}` form, so that drug audit rules with no 0325 序号 can be identified. The accepted pattern MUST be `^(R\d{3}|RD\d{2,3})$`. The rule loader's `glob("R*.yaml")` already discovers `RD*.yaml` files; rules MUST continue to be keyed by `rule_id` string. Existing `R\d{3}` rules MUST keep loading unchanged.

#### Scenario: Loading an RD-namespaced rule succeeds

- **WHEN** the loader reads `configs/rules/RD01.yaml` whose `rule_id` is `RD01`
- **THEN** the returned `Rule` has `rule_id == "RD01"` and no validation error is raised

#### Scenario: Existing R-numbered rule still valid

- **WHEN** the loader reads `configs/rules/R007.yaml` with `rule_id: R007`
- **THEN** it loads without error, preserving backwards compatibility

#### Scenario: Malformed drug-rule id is rejected

- **WHEN** the loader reads a yaml with `rule_id: RD` or `rule_id: RD1234` or `rule_id: RDx`
- **THEN** the system raises a `RuleValidationError` for the `rule_id` field

#### Scenario: Loader discovers RD files via glob

- **WHEN** `load_all` runs over a `configs/rules/` directory containing both `R*.yaml` and `RD*.yaml`
- **THEN** both are loaded and any duplicate `rule_id` across them raises `RuleValidationError`

### Requirement: Drug audit type-level rules ready

After this change, `configs/rules/` SHALL contain four type-level drug rules with `derived_from_template: M8` and `status: ready`, each carrying a non-empty `prompt_addon` and a `drug_rule_type` configuration matching one KB rule type. The four are: `R007` (限适应症, repurposed from its existing `drafting` skeleton — its 0325 question already covers limited-payment), `RD01` (超说明书), `RD02` (限二线), `RD03` (禁忌症). Each type-level rule audits ALL of the patient's KB-matched drugs of its type via `drug_audit_lookup` bulk mode.

#### Scenario: Four type-level rules load ready with M8 lineage

- **WHEN** `javert list` runs after this change
- **THEN** `R007`, `RD01`, `RD02`, `RD03` each show `status=ready` and have `derived_from_template: M8`, and each yaml declares a `drug_rule_type` among `限适应症/超说明书/限二线/禁忌症`

#### Scenario: R007 flips from drafting to ready

- **WHEN** `javert show R007` runs after this change
- **THEN** `R007` has `status=ready`, `derived_from_template: M8`, a non-empty `prompt_addon`, and `drug_rule_type` resolving to `限适应症`

### Requirement: Drug audit curated rules ready

After this change, `configs/rules/` SHALL contain a set of curated drug rules (target 20–40) in the `RD` namespace with `derived_from_template: M8` and `status: ready`. Each curated rule MUST target a single high-frequency, high-risk drug whose 通用名 (a) exists in `drug_audit_kb.json` and (b) appears in the patients' fee data, and MUST set `trigger_keywords` to that drug's 通用名 so the router can trigger it precisely. The exact drug list is data-driven from the build script's hit-frequency table.

#### Scenario: A curated rule targets a single KB drug

- **WHEN** any curated `RD` rule is loaded
- **THEN** it has `derived_from_template: M8`, a `drug_rule_type` set, and its `trigger_keywords` contains the 通用名 of a drug present in `drug_audit_kb.json`

#### Scenario: Curated drugs are observed in the fee data

- **WHEN** the curated drug list is selected
- **THEN** every chosen 通用名 is among the ~157 KB drugs that intersect the patients' 西药/中药/草药 fee names (no purely-dormant drug is given a curated rule)

### Requirement: Contraindication rules carry a distinct violation_type

Drug rules whose `drug_rule_type` is `禁忌症` SHALL carry a `violation_type` that marks them as 用药安全/禁忌 rather than a medical-insurance fraud category, so reporting separates patient-safety findings from 骗保 findings.

#### Scenario: 禁忌症 rule violation_type is distinct

- **WHEN** a `禁忌症` drug rule (e.g. `RD03` or a curated contraindication rule) is loaded
- **THEN** its `violation_type` is distinguishable from the medical-insurance fraud `violation_type` values used by M1–M7 rules
