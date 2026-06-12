## MODIFIED Requirements

### Requirement: audit-patient command supports configurable concurrency

The `javert audit-patient` command SHALL accept a `--concurrency N` option of type `click.IntRange(1, 10)` with `default=1`. When `--concurrency 1` (default), the command MUST execute rules serially exactly as before this change (preserves all existing scenarios in `add-patient-centric-audit/specs/cli/spec.md`). When `--concurrency N` with N ≥ 2, the command MUST execute rules concurrently via `concurrent.futures.ThreadPoolExecutor(max_workers=min(N, len(selected_rules)))`, persisting each completed audit result in the main thread and recording failures (LlmUnavailableError, generic Exception) per rule without aborting the remainder of the batch.

The summary block printed to stdout SHALL include a `concurrency: N` line immediately after the `cache mode:` line, so reruns and docs can record the parallel configuration used.

#### Scenario: concurrency=1 preserves serial behavior

- **WHEN** `javert audit-patient JT001 --concurrency 1` runs (or `--concurrency` omitted)
- **THEN** the command executes rules sequentially in rule_id order
- **AND** mid-batch `LlmUnavailableError` aborts further rule execution (pending > 0 in summary)
- **AND** behavior matches add-patient-centric-audit baseline tests exactly

#### Scenario: concurrency=N runs all rules concurrently

- **GIVEN** 5 P0 rules selected and `--concurrency 3`
- **WHEN** the command runs to completion
- **THEN** all 5 rules produce AuditResults persisted to audit_runs
- **AND** the ThreadPoolExecutor used `max_workers=3`
- **AND** the summary line shows `concurrency: 3`

#### Scenario: share-tool-cache works under concurrency

- **GIVEN** `--share-tool-cache --concurrency 3` and 5 rules that all call `note_diagnosis(patient_id)`
- **WHEN** the run completes
- **THEN** the tool_calls hit_rate in summary is ≥ 50% (one cold + four cached, modulo race timing)
- **AND** no rule produces an AuditResult with verdict INCONCLUSIVE due to cache corruption

#### Scenario: LlmUnavailableError isolated under concurrency

- **GIVEN** 5 mock rules where the 3rd raises `LlmUnavailableError`
- **WHEN** `--concurrency 3` is used
- **THEN** the other 4 rules still produce AuditResults
- **AND** summary reports `Verdicts: V=… / C=… / I=…  (4 completed, 0 pending, 1 failed)`
- **AND** exit code is 1 (any failure marks process exit code 1)

#### Scenario: summary block reports concurrency

- **WHEN** `--concurrency 5` is used with 3 rules
- **THEN** summary stdout contains a line matching `concurrency: 5`
- **AND** the effective ThreadPoolExecutor max_workers is `min(5, 3) = 3`
