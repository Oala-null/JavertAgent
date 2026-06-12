## ADDED Requirements

### Requirement: Per-audit output token accounting

`AuditResult` SHALL carry a `completion_tokens: int` field (default `0`). During an audit, `Runner.audit` MUST accumulate the `usage.completion_tokens` returned by every LLM call in the agent loop (the LLM provider already returns `usage`) and write the running total into the resulting `AuditResult`. The value MUST be persisted to and read back from the audit store. Existing records without the field MUST read back as `0` (treated as "not captured").

#### Scenario: Token total equals the sum of all LLM calls in the loop

- **WHEN** an audit's agent loop makes three LLM calls returning `completion_tokens` of 100, 200, and 300
- **THEN** the produced `AuditResult.completion_tokens` is 600

#### Scenario: Partial accumulation survives mid-audit failure

- **WHEN** the agent loop fails or early-exits after two LLM calls (100 + 200) before a third
- **THEN** the recorded `completion_tokens` is 300 (accumulation up to the failure point is not lost)

#### Scenario: Token is persisted and read back

- **WHEN** an `AuditResult` with `completion_tokens=600` is written to the store and later read back
- **THEN** the reconstructed `AuditResult` has `completion_tokens == 600`

#### Scenario: Legacy rows default to zero

- **WHEN** a pre-change audit row (stored before the column existed) is read back
- **THEN** its `completion_tokens` reads as `0` and no error is raised

#### Scenario: Deterministic gate layer adds no tokens

- **WHEN** the verdict gate downgrades a verdict without any LLM call
- **THEN** it contributes `0` to `completion_tokens` (only LLM agent-loop calls are counted)
