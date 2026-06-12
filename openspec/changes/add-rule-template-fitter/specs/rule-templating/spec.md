## ADDED Requirements

### Requirement: Template yaml storage and schema

The system SHALL store each rule template as a single yaml file at `configs/templates/Mx.yaml` containing the fields: `template_id` (string, e.g. `M1`), `name` (Chinese display name), `description` (one-line summary of the violation pattern), `master_prompt` (Jinja2 template string for `prompt_addon`), `fields` (list of `TemplateField` declarations specifying `name`/`type`/`desc`/`required`/`default`), and optional `keywords_template` / `tools_template` / `signal_template` (Jinja2 strings that render the regular auxiliary fields of `Rule`).

#### Scenario: Loading a well-formed template

- **WHEN** the system reads `configs/templates/M1.yaml` containing all required schema fields with valid types
- **THEN** the system returns a `Template` object whose `template_id == "M1"`, `master_prompt` is a Jinja2-compilable string, and `fields` is a list of `TemplateField` instances

#### Scenario: Rejecting a template missing template_id

- **WHEN** the system reads a template yaml lacking `template_id` or `name` or `master_prompt`
- **THEN** the system raises `TemplateValidationError` identifying the offending file path and missing field name

#### Scenario: Empty placeholder templates load successfully

- **WHEN** the system reads a placeholder template (`M4.yaml` with `master_prompt: ""` and `fields: []`)
- **THEN** the system returns a valid `Template` object; `template validate M4` reports status "empty (not yet filled)" but does not raise

### Requirement: TemplateField typed schema

`TemplateField` SHALL declare `name` (str), `type` (one of `str` / `list[str]` / `enum` / `bool` / `int`), `desc` (str), `required` (bool, default true), and an optional `default` value. When `type == "enum"`, the field MUST also carry an `options` list of allowed string values. The loader MUST validate that any `default` is type-compatible.

#### Scenario: Loading enum field with options

- **WHEN** the template declares `fields[0].type == "enum"` with `options: [手术类, 药品类, 耗材类, 检查类, 其他类]`
- **THEN** the loader accepts; subsequent `validate_vars()` for this field rejects any value not in options

#### Scenario: Loading enum field without options

- **WHEN** a field has `type: enum` but no `options` key
- **THEN** the loader raises `TemplateValidationError` referencing the field name and "enum requires options"

### Requirement: Jinja2 rendering with strict undefined

The template renderer SHALL use Jinja2's `StrictUndefined` so any vars reference that is missing AND has no `default` raises `UndefinedError` (which the CLI converts into a human-readable error). Vars that are present and pass type validation MUST be substituted; conditional blocks (`{% if %}`/`{% for %}`) MUST be evaluated against the vars dict.

#### Scenario: Successful render with full vars

- **WHEN** all required fields are present in vars and types match
- **THEN** the renderer returns the fully substituted `prompt_addon` string plus rendered `trigger_keywords` / `suggested_tools` / `expected_signal` (if those templates exist), as a dict

#### Scenario: Missing required field aborts render

- **WHEN** a required field is absent in vars and has no `default`
- **THEN** the renderer raises a typed error naming the missing field; the CLI catches it and prints "field 'X' is required but not provided"

#### Scenario: Conditional block evaluates correctly

- **WHEN** master_prompt contains `{% if has_extra_diagnosis_condition %}...{% endif %}` and vars include `has_extra_diagnosis_condition: true`
- **THEN** the conditional content is included in the rendered output

### Requirement: Vars input from three modes

The system SHALL accept template personalization vars via three CLI modes: `--vars <json|yaml file>` (batch / config-driven), `--interactive` (CLI prompts the operator field-by-field), and `--auto` (the system queries the LLM provider to draft vars from `rule.question` + `rule.example`, then presents them for confirmation). The three modes MUST be mutually exclusive in a single invocation; modes may be re-run on the same rule (vars file → interactive edit → auto refine).

#### Scenario: --vars loads JSON file

- **WHEN** `prompt-fit R045 --template M1 --vars docs/m1_r045_vars.json` runs and the file contains all required fields
- **THEN** the renderer produces the prompt_addon and the CLI writes it to R045.yaml

#### Scenario: --interactive prompts for each unfilled field

- **WHEN** `prompt-fit R045 --template M1 --interactive` runs and R045.yaml has no existing vars
- **THEN** the CLI iterates the template's `fields` list, prints each `desc`, and reads operator input from stdin; the operator can hit ENTER to accept `default` (if present)

#### Scenario: --auto drafts vars via LLM

- **WHEN** `prompt-fit R045 --template M1 --auto` runs
- **THEN** the system sends `rule.question + rule.example + template.fields_schema` to the configured LLM (default Qwen3.5 via existing `Qwen35Provider`), parses LLM-returned JSON into a vars dict, validates types against the template, and prints the drafted vars + rendered prompt_addon to stdout; the CLI requires explicit operator confirmation (default `[y/N]`) before writing to yaml

#### Scenario: --auto refuses to write without confirmation

- **WHEN** the operator answers `N` or non-`y` at the confirmation prompt after `--auto` drafting
- **THEN** no changes are written to the rule yaml; the system exits 0 with stdout "aborted by user"

### Requirement: Template-to-rule write-back

Successful `prompt-fit` invocations MUST write the rendered output to the target rule yaml, replacing the entire `prompt_addon` field and (when corresponding templates exist) the `trigger_keywords`, `suggested_tools`, and `expected_signal` fields. The write SHALL preserve all other fields (`rule_id`, `domain`, `violation_type`, `question`, `example`, `status`, `priority`, `notes`) and use ruamel.yaml round-trip to preserve comments where possible.

#### Scenario: Write preserves rule_id and notes

- **WHEN** R045.yaml has `notes: "v0.1 备注"` before `prompt-fit`
- **THEN** after prompt-fit, R045.yaml's `notes` field still equals "v0.1 备注" byte-for-byte

#### Scenario: derived_from_template recorded on Rule

- **WHEN** `prompt-fit R045 --template M1` writes back
- **THEN** R045.yaml has a new field `derived_from_template: M1` recording which template produced the prompt_addon

### Requirement: Verification round-trip on existing reference yaml

The system SHALL support a verification workflow where an already-written `prompt_addon` (e.g. R191's existing M1-style prompt) is reproduced by writing a `vars file` against the template, rendering, and confirming byte-equality with the original.

#### Scenario: R191 prompt reproduced from M1 template + vars

- **WHEN** `configs/templates/M1.yaml` is filled and `docs/m1_r191_vars.json` carries the R191 personalization values, and `prompt-fit R191 --template M1 --vars docs/m1_r191_vars.json --dry-run --output -` is run
- **THEN** stdout shows the rendered prompt_addon; it matches R191.yaml's current `prompt_addon` field byte-for-byte (modulo final newline)
