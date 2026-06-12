## ADDED Requirements

### Requirement: Rule priority field

The `Rule` model SHALL include a `priority` field with type `Literal["P0","P1","P2","P3"]` and default value `"P3"` (lowest priority). The field MUST be persisted in the YAML file under the key `priority`. When the field is absent in an existing YAML file, the loader MUST treat it as `"P3"` without raising an error.

#### Scenario: Loading a rule yaml with priority field

- **WHEN** the system reads `configs/rules/R191.yaml` containing `priority: P0`
- **THEN** the returned `Rule` object has `priority == "P0"`

#### Scenario: Loading a rule yaml without priority field (backwards compat)

- **WHEN** the system reads a yaml file that lacks the `priority` key
- **THEN** the returned `Rule` object has `priority == "P3"` and no validation error is raised

#### Scenario: Rejecting an invalid priority value

- **WHEN** the system reads a yaml with `priority: P4` or `priority: high`
- **THEN** the system raises a `RuleValidationError` listing the four allowed values

### Requirement: CSV-driven priority ingest

The system SHALL provide a one-off ingest script at `scripts/sync_priority_csv.py` that reads `docs/163规则可行性分析表.csv` and, for each `Rxxx` row, either patches the `priority` field of the existing `configs/rules/Rxxx.yaml` or creates a skeleton yaml if none exists. The script MUST support a `--dry-run` mode that prints the planned changes without writing. The script MUST preserve all non-`priority` fields of existing yaml files (operator-edited `prompt_addon`, `trigger_keywords`, `expected_signal`, `notes`, etc.).

#### Scenario: Ingest patches priority of existing yaml

- **WHEN** the script encounters `R191` in the CSV with `Javert 优先级 == P0` and `configs/rules/R191.yaml` already exists with non-empty `prompt_addon`
- **THEN** after running with `--write`, the yaml file has `priority: P0` set and its `prompt_addon` is byte-identical to before

#### Scenario: Ingest creates skeleton for missing yaml

- **WHEN** the script encounters `R045` in the CSV with `P0` priority and no `configs/rules/R045.yaml` exists
- **THEN** after running with `--write`, a new `configs/rules/R045.yaml` is emitted with `priority: P0`, `status: drafting`, `prompt_addon: ""`, and `rule_id`/`violation_type`/`question` populated from the CSV row

#### Scenario: Ingest dry-run does not modify files

- **WHEN** the script is invoked with `--dry-run`
- **THEN** stdout shows a per-file diff summary (`R045: would create`, `R191: would patch priority drafting→P0`) and no file under `configs/rules/` is touched

#### Scenario: Ingest warns on missing priority annotation

- **WHEN** the CSV contains a row with empty `Javert 优先级` column
- **THEN** the script logs a warning identifying the rule_id and skips that row rather than defaulting to `P3`

### Requirement: Priority-aware listing

The `javert list` output SHALL include a `priority` column so operators can spot regression in priority distribution at a glance.

#### Scenario: list shows priority column

- **WHEN** the operator runs `javert list`
- **THEN** each rule row includes its `priority` value alongside `rule_id`, `status`, `domain`, and audit summary columns
