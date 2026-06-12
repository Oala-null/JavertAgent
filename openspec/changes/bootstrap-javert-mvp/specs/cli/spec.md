## ADDED Requirements

### Requirement: Single binary entry point

The system SHALL expose a single command-line entry point invoked as `javert <subcommand> [options]` (or equivalently `python cli.py <subcommand>`). The entry point MUST register at minimum the following subcommands: `init`, `list`, `dry-run`, `run`, `mark`, `report`, `show`. Unknown subcommands MUST exit with status 2 and print the list of available subcommands.

#### Scenario: Subcommand discovery

- **WHEN** operator runs `javert --help`
- **THEN** the help output includes all seven subcommand names with one-line descriptions

#### Scenario: Unknown subcommand fails clearly

- **WHEN** operator runs `javert frobnicate`
- **THEN** the process exits with status 2 and stderr contains "unknown subcommand 'frobnicate'" plus the list of valid subcommands

### Requirement: init bootstraps project state

The system SHALL implement `javert init` to perform, in order: (1) snapshot data files from upstream `zadig_agent/data/`, (2) generate yaml rule files for the 34-rule pilot subset under `configs/rules/`, (3) create `data/pilot_patients.txt` with 50 thyroid-cancer patient IDs, (4) create `output/audit.sqlite` with the canonical schema, (5) verify connectivity to the configured LLM endpoint and print the resolved model name. Each step MUST log its outcome (success / skipped / failed) and the command MUST exit with status 0 only if every step succeeded or was a benign skip.

#### Scenario: Clean init from empty project

- **WHEN** operator runs `javert init` against a freshly checked-out project directory
- **THEN** all five steps complete in order, the log shows five "✓" entries, and the next step "ready to run `javert dry-run R191 --patient ...`" is printed

#### Scenario: Init reports LLM connectivity failure

- **WHEN** the sglang endpoint is unreachable during step 5
- **THEN** the data/rules/db steps still complete, step 5 prints "✗ LLM unreachable: <details>", and the command exits with non-zero status while leaving filesystem state intact

### Requirement: Status mutation via mark

The system SHALL implement `javert mark <rule_id> --status <new_status>` that atomically updates the `status` field of `configs/rules/<rule_id>.yaml`, preserving every other field byte-for-byte (comments, key order, indentation). Backward transitions MUST be rejected unless `--force` is given, per the rule-registry state-machine specification.

#### Scenario: Mark preserves non-status fields

- **WHEN** operator edits `R191.yaml` to add custom comments and a populated `prompt_addon`, then runs `javert mark R191 --status ready`
- **THEN** the file's comments, `prompt_addon`, and field ordering are preserved exactly, with only the `status` field changed

### Requirement: report aggregates over the audit store

The system SHALL implement `javert report` that prints a tabular summary covering all rules with at least one audit run, listing for each: `rule_id`, `domain`, `status`, `dry_run_count`, `verdict_distribution (V / C / I)`, `mean_confidence`, `median_duration_ms`. The report MUST optionally accept `--since <iso-date>` to limit the time window and `--rule <rule_id>` to limit to a single rule.

#### Scenario: Default report covers all rules with runs

- **WHEN** the audit store has runs for R001, R191, R193 and operator runs `javert report`
- **THEN** stdout contains exactly three rows (one per rule), with verdict counts summing to the total run count for each rule

#### Scenario: Filtered report by rule

- **WHEN** operator runs `javert report --rule R191`
- **THEN** stdout contains exactly one row corresponding to R191, identical to the row that would appear in the unfiltered output

### Requirement: All commands honor a single config file

The system SHALL read shared configuration (LLM endpoint, model name, max-tool-calls budget, paths to data and rules directories, audit-store path) from a single `configs/llm.yaml` file. Environment variables MUST be able to override individual fields using the prefix `JAVERT_` (e.g. `JAVERT_LLM_ENDPOINT`, `JAVERT_MAX_TOOL_CALLS`).

#### Scenario: Default config loads cleanly

- **WHEN** the project ships with a default `configs/llm.yaml` and no environment overrides are set
- **THEN** every subcommand resolves the same set of config values and the values match the file contents

#### Scenario: Environment variable overrides file value

- **WHEN** `JAVERT_MAX_TOOL_CALLS=5` is set in the environment and `configs/llm.yaml` specifies `max_tool_calls: 10`
- **THEN** the runner uses 5 as the budget for any audit invocation in that process
