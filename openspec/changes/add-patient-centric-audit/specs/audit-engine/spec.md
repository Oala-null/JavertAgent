## ADDED Requirements

### Requirement: Optional cross-rule tool cache reuse

`Runner.audit` SHALL accept a `reset_cache: bool = True` keyword parameter. When `True` (default), the runner MUST call `ToolExecutor.reset_cache()` at the start of each audit (preserving existing behavior). When `False`, the runner MUST NOT reset the cache, allowing tool results to be reused across consecutive audits performed by the same `ToolExecutor` instance.

#### Scenario: Default behavior preserves existing dry-run / run --pilot

- **WHEN** an existing caller invokes `Runner.audit(rule, patient_id)` without specifying `reset_cache`
- **THEN** the executor cache is reset at the start of the audit, matching pre-change behavior bit-for-bit

#### Scenario: Cross-rule cache reuse when explicitly disabled

- **WHEN** the same `Runner` instance is used to call `audit(rule_A, "J66252", reset_cache=False)` then `audit(rule_B, "J66252", reset_cache=False)`, and both audits cause the LLM to invoke `note_diagnosis(patient_id="J66252")` with identical arguments
- **THEN** the second `ToolCall` record carries `cached=True` and a duration substantially smaller than the first

#### Scenario: Cache reset between different patients is still safe

- **WHEN** a caller invokes `audit(rule_A, "J66252", reset_cache=False)` followed by `audit(rule_A, "J18906", reset_cache=False)` on the same executor
- **THEN** the caller (orchestration layer) MUST reset the executor cache between patients; the runner itself does NOT enforce this — it is the orchestrator's responsibility

### Requirement: Patient-centric orchestration

The system SHALL provide a patient-level orchestration operation that, given a `patient_id` and a list of `Rule` objects, runs each rule's audit sequentially against that patient and returns the list of `AuditResult` objects in run order, plus aggregate metrics (total wall time, per-verdict counts, slowest-N rules, cache hit counts).

#### Scenario: Patient-centric run produces one AuditResult per rule

- **WHEN** the orchestrator is invoked with `patient_id="J66252"` and 30 rules
- **THEN** the operation returns exactly 30 `AuditResult` objects, in the same order as the input rules; each is also written to the `audit_runs` store with `triggered_by="cli-audit-patient"`

#### Scenario: Abandoned rules are skipped by default

- **WHEN** the input rule list contains `R312` with `status == "abandoned"` and the caller did NOT pass an explicit rule subset
- **THEN** R312 is silently excluded from the run; the returned result list omits it; the patient summary's "skipped count" includes R312 with reason `status=abandoned`

#### Scenario: Explicit rule subset overrides abandoned filter

- **WHEN** the caller passes an explicit `rules=[R045, R312]` subset that names an abandoned rule
- **THEN** R312 IS run (explicit > status); the orchestrator logs a warning per abandoned rule that was force-included

#### Scenario: Aggregate metrics reflect all rules run

- **WHEN** 30 rules are run with mixed verdicts (2 VIOLATION, 21 CLEAN, 7 INCONCLUSIVE)
- **THEN** the returned metrics include `total_duration_ms`, `per_verdict_counts={V:2, C:21, I:7}`, `slowest_rules=[(rule_id, duration_ms), ...]` (top 3), and `tool_cache_hits` (count of `ToolCall.cached=True` across all audits)

### Requirement: LLM failure mid-batch leaves partial results intact

When a `LlmUnavailableError` is raised partway through a patient-centric run, the orchestrator MUST persist all successfully-completed AuditResults to the store before propagating the exception. The patient summary MUST clearly distinguish "completed" from "skipped due to LLM failure".

#### Scenario: LLM dies after 12 of 30 rules

- **WHEN** rule #13's audit raises `LlmUnavailableError`
- **THEN** the first 12 results are already in `audit_runs`; the orchestrator emits a summary showing `completed=12, llm_failed=18, total=30` and propagates the exception so the CLI can exit non-zero
