## ADDED Requirements

### Requirement: Rule files are version-controlled YAML

The system SHALL persist each audit rule as a single YAML file at `configs/rules/Rxxx.yaml`, where `xxx` is the rule's `序号` (sequential number) from the source `2026年医疗机构自查自纠问题清单0325.xlsx`. The file MUST contain the following fields: `rule_id` (string, e.g. `R191`), `domain` (string, copied from 所属领域), `violation_type` (string, copied from 违规类型), `question` (string, copied from 问题), `example` (string, copied from 违规参考示例), `status` (enum: `drafting | ready | validated | abandoned`), `prompt_addon` (string, may be empty), `trigger_keywords` (list of strings, may be empty), `suggested_tools` (list of strings, may be empty), `expected_signal` (string, may be empty), `notes` (string, may be empty).

#### Scenario: Loading a well-formed rule file

- **WHEN** the system reads `configs/rules/R191.yaml` containing all required fields with valid types
- **THEN** the system returns a `Rule` object with every field populated from the file

#### Scenario: Rejecting a rule file with missing required fields

- **WHEN** the system reads a rule file missing `rule_id` or `status`
- **THEN** the system raises a `RuleValidationError` identifying the offending file path and missing field name

#### Scenario: Rejecting an invalid status value

- **WHEN** the system reads a rule file with `status: in_progress` (not one of the four valid enum values)
- **THEN** the system raises a `RuleValidationError` listing the allowed status values

### Requirement: Bootstrap from source spreadsheet

The system SHALL provide an `init` operation that reads `2026年医疗机构自查自纠问题清单0325.xlsx`, filters rows where `规则情况 == 做不了`, and emits one YAML file per row at `configs/rules/Rxxx.yaml`. The first six fields (`rule_id`, `domain`, `violation_type`, `question`, `example`, `status`) MUST be populated from the source row; `status` MUST default to `drafting`; the remaining four fields MUST be emitted with empty defaults (empty string or empty list).

#### Scenario: Initial bootstrap of pilot subset

- **WHEN** the operator runs init with `--pilot` flag specifying the 34-rule pilot subset (domain ∈ {肿瘤, 各科室通用类, 临床检验} subset documented in design.md)
- **THEN** exactly 34 YAML files appear under `configs/rules/`, each with `status: drafting` and source fields filled

#### Scenario: Init is idempotent for existing files

- **WHEN** init is run a second time and a YAML file already exists for a given rule_id
- **THEN** the existing file is left untouched and a log entry notes "skipped: already exists"

#### Scenario: Init refuses to overwrite operator edits

- **WHEN** init is invoked with `--force` against a directory containing operator-modified YAML files
- **THEN** the system refuses and prints the message "use --backup-and-replace to confirm overwrite"

### Requirement: Status state machine

The system SHALL enforce that rule `status` transitions follow the directed graph: `drafting → ready → validated`, with `abandoned` reachable from any state. Backward transitions (e.g. `validated → drafting`) MUST be rejected by the `mark` operation unless the operator passes `--force`.

#### Scenario: Forward transition succeeds

- **WHEN** operator runs `javert mark R191 --status ready` on a rule currently in `drafting`
- **THEN** the rule's YAML file is updated to `status: ready` and the operation logs the transition

#### Scenario: Backward transition is blocked without force

- **WHEN** operator runs `javert mark R191 --status drafting` on a rule currently in `validated`
- **THEN** the operation exits with non-zero status and message "use --force to confirm regression"

#### Scenario: Abandoned is reachable from anywhere

- **WHEN** operator runs `javert mark R191 --status abandoned` on a rule in any state
- **THEN** the rule's status is updated to `abandoned` without requiring `--force`

### Requirement: Status overview command

The system SHALL provide a `list` operation that prints a tabular overview of every rule file under `configs/rules/`, with columns `rule_id`, `domain`, `violation_type`, `status`, and (where audit results exist in the store) `dry_run_count` and `last_verdict_distribution`.

#### Scenario: Listing 34 pilot rules

- **WHEN** operator runs `javert list` after init
- **THEN** the output contains exactly 34 rows, sorted by `rule_id` ascending, all in `status: drafting`

#### Scenario: Listing reflects audit store data

- **WHEN** the audit store contains 50 dry-run records for R191 (15 VIOLATION, 30 CLEAN, 5 INCONCLUSIVE) and operator runs `javert list`
- **THEN** the row for R191 shows `dry_run_count: 50` and a verdict-distribution column rendering "V:15 C:30 I:5" (or equivalent)
