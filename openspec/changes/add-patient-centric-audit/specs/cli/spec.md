## ADDED Requirements

### Requirement: audit-patient subcommand

The CLI SHALL expose a new subcommand `javert audit-patient <patient_id>` that runs a configurable rule set against a single patient in one shot and prints a patient-level summary to stdout. The subcommand MUST NOT alter the behavior of the existing seven subcommands (`init / list / dry-run / run / mark / report / show`).

#### Scenario: Default invocation runs all P0 non-abandoned rules

- **WHEN** the operator runs `javert audit-patient J66252` with no other flags
- **THEN** the CLI loads every `configs/rules/Rxxx.yaml` with `priority == "P0"` and `status != "abandoned"`, runs each against patient `J66252` in order, persists all results to the audit store, and prints a per-rule progress line plus a final summary block to stdout

#### Scenario: --priority flag scopes the rule set

- **WHEN** the operator runs `javert audit-patient J66252 --priority P1`
- **THEN** only rules with `priority == "P1"` (and `status != "abandoned"`) are run

#### Scenario: --rules flag overrides priority filter

- **WHEN** the operator runs `javert audit-patient J66252 --rules R045,R191`
- **THEN** exactly two rules R045 and R191 are run regardless of their priority or status; the summary header explicitly lists "rule selection: explicit (--rules)"

#### Scenario: --share-tool-cache enables cross-rule cache reuse

- **WHEN** the operator runs `javert audit-patient J66252 --share-tool-cache`
- **THEN** all rules in the run share one `ToolExecutor` instance and each `Runner.audit` call uses `reset_cache=False`; the summary reports the cache hit rate (e.g., `tool_cache: 134 calls, 45 cached, hit_rate=33.6%`)

#### Scenario: --share-tool-cache off by default for baseline timing

- **WHEN** the operator runs `javert audit-patient J66252` without `--share-tool-cache`
- **THEN** each rule's audit resets the executor cache at start (cold-start baseline); the summary's cache hit rate reports `0%`

### Requirement: Patient-level summary output

After all rules in an `audit-patient` run complete (or fail), the CLI SHALL print a structured summary to stdout that includes: total wall time, average and median (p50) per-rule duration, slowest top-3 rules with their `rule_id` and duration, per-verdict counts (`VIOLATION` / `CLEAN` / `INCONCLUSIVE` / `skipped`), tool call totals, and cache hit rate.

#### Scenario: Summary block format

- **WHEN** an `audit-patient` run of 30 rules completes successfully
- **THEN** stdout contains a final block matching the form:
  ```
  === audit-patient J66252 summary ===
  rule selection: priority=P0 (excluded R312 [abandoned])
  cache mode: cold-start (reset per rule)
  
  Total: 8m24s (504s)  avg=16.8s  p50=14.2s
  Slowest: R191 (45.2s), R220 (31.0s), R313 (28.5s)
  Verdicts: V=2 / C=21 / I=7  (30 completed, 0 skipped, 0 failed)
  Tool calls: 134 total, 0 cached (hit_rate=0.0%)
  ```

#### Scenario: Per-rule progress lines

- **WHEN** an `audit-patient` run executes rule-by-rule
- **THEN** for each rule, the CLI emits one progress line of the form `[i/N] Rxxx → V conf=0.85 12.3s tc=4 (cached 0)` to stderr (mirrors existing `javert run` format), where `V`/`C`/`I` is the verdict letter

### Requirement: Exit codes for patient-level audit

The `audit-patient` subcommand SHALL exit with code 0 when all rules complete (regardless of verdicts), code 1 when at least one rule failed with `LlmUnavailableError` or other exception, and code 2 on usage errors (unknown patient, no rules matched the filter, etc.).

#### Scenario: All rules complete (any verdicts)

- **WHEN** 30 rules produce 2 VIOLATION, 21 CLEAN, 7 INCONCLUSIVE
- **THEN** the process exits 0

#### Scenario: LLM endpoint dies mid-batch

- **WHEN** an `LlmUnavailableError` interrupts the run after 12 rules
- **THEN** the process exits 1 with a stderr message identifying which rule failed and the count of completed-vs-pending

#### Scenario: No rules match the priority filter

- **WHEN** the operator runs `javert audit-patient J66252 --priority P9` (invalid)
- **THEN** Click rejects the value at parse time and exits 2; if the value is valid but no rule has that priority, the CLI prints "no rules matched filter" to stderr and exits 2 without invoking the LLM
