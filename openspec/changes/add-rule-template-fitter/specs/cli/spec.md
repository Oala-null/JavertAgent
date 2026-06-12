## ADDED Requirements

### Requirement: prompt-fit subcommand

The CLI SHALL expose `javert prompt-fit <rule_id> --template <template_id> [mode-flag]` where `mode-flag` is exactly one of `--vars <path>`, `--interactive`, or `--auto`. The command renders the named template against the resolved vars and writes the result back to the rule yaml's `prompt_addon` (and corresponding aux fields when the template defines them). The command MUST NOT modify rule yamls that don't match its target `rule_id`.

#### Scenario: prompt-fit with --vars file

- **WHEN** the operator runs `javert prompt-fit R045 --template M1 --vars docs/m1_r045_vars.json`
- **THEN** R045.yaml's `prompt_addon` is replaced with the rendered output; `derived_from_template: M1` is set; exit 0; stdout reports "✓ R045 written from M1"

#### Scenario: prompt-fit with --interactive

- **WHEN** the operator runs `javert prompt-fit R045 --template M1 --interactive` and the operator answers all field prompts at the CLI
- **THEN** the same write-back occurs; the typed vars are captured (and can optionally be written to a vars file via `--save-vars docs/m1_r045_vars.json`)

#### Scenario: prompt-fit with --auto

- **WHEN** the operator runs `javert prompt-fit R045 --template M1 --auto` and the LLM drafter succeeds
- **THEN** stdout shows the drafted vars + rendered prompt; CLI prompts `[y/N]`; on `y` the write proceeds; on any other input no write occurs and exit 0 with "aborted by user"

#### Scenario: mutually-exclusive mode flags

- **WHEN** the operator passes both `--vars X.json --interactive`
- **THEN** Click rejects at parse time, exit 2 with message identifying the conflict

#### Scenario: unknown template aborts before any write

- **WHEN** the operator runs `prompt-fit R045 --template M99 --vars X.json` and `configs/templates/M99.yaml` does not exist
- **THEN** the CLI exits 2 with stderr "未知 template_id: M99"; R045.yaml is untouched

#### Scenario: --dry-run prints without writing

- **WHEN** `prompt-fit R045 --template M1 --vars X.json --dry-run --output -` runs
- **THEN** stdout contains the rendered prompt_addon; R045.yaml is byte-identical to before; exit 0

### Requirement: template subcommand group

The CLI SHALL expose a `javert template` subcommand group with at least three children: `template list`, `template show <id>`, and `template validate <id>`.

#### Scenario: template list shows all templates

- **WHEN** `javert template list` runs
- **THEN** stdout shows a table with columns `template_id / name / description / status` for every yaml under `configs/templates/`; `status` is one of `empty` (no master_prompt or no fields) / `partial` (some fields filled) / `ready` (master_prompt + fields all filled); exit 0

#### Scenario: template show prints full template

- **WHEN** `javert template show M1` runs and `configs/templates/M1.yaml` exists
- **THEN** stdout contains the parsed template's full content (template_id, name, description, master_prompt body, fields list with their types and desc)

#### Scenario: template validate reports schema errors

- **WHEN** `javert template validate M1` is run against a M1.yaml with an enum field missing `options`
- **THEN** exit 1 and stderr identifies the offending field plus the validation rule violated

#### Scenario: template validate accepts empty placeholder

- **WHEN** M1 is a placeholder (empty `master_prompt`, empty `fields`)
- **THEN** validate exits 0 with stdout "M1: empty (not yet filled)"

### Requirement: prompt-fit exit codes

The `prompt-fit` subcommand SHALL exit 0 on successful write, 0 on `--auto` aborted by user (with explicit "aborted by user" message), 1 on rendering or template-load failure, and 2 on usage / argument errors.

#### Scenario: rendering error exits 1

- **WHEN** a vars file is missing a required field with no default
- **THEN** prompt-fit exits 1 with stderr identifying the missing field

#### Scenario: --rules R045,R191 syntax not allowed in prompt-fit

- **WHEN** `prompt-fit R045,R191 --template M1 ...` is invoked
- **THEN** Click parses `R045,R191` as the literal argument, lookup of `R045,R191.yaml` fails, and exit 2 (one rule_id at a time; batching is left to shell loops or future change)
