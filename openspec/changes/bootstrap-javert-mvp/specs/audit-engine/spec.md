## ADDED Requirements

### Requirement: Single-rule single-patient audit

The system SHALL provide a `Runner.audit(rule, patient_id) -> AuditResult` operation that combines a `Rule` (loaded from rule-registry) and a patient ID into a single LLM agent loop, returning a structured `AuditResult` with fields `verdict ∈ {VIOLATION, CLEAN, INCONCLUSIVE}`, `confidence ∈ [0.0, 1.0]`, `evidence: list[Evidence]`, `reasoning: str`, `tool_calls: list[ToolCall]`, `duration_ms: int`, `started_at: datetime`, `model: str`.

#### Scenario: Successful audit produces a verdict

- **WHEN** `Runner.audit(rule_R191, "J66252")` is called with the LLM endpoint reachable
- **THEN** the operation returns an `AuditResult` whose `verdict` is one of the three allowed values, `tool_calls` is non-empty (the agent invoked at least one tool), and `reasoning` is a non-empty string

#### Scenario: Default verdict on tool-call exhaustion

- **WHEN** the agent reaches the configured maximum tool-call count (default 10) without emitting a final verdict
- **THEN** the runner forces termination, emits `verdict: INCONCLUSIVE` with a `reasoning` field noting "max tool calls exhausted", and the `AuditResult` is still well-formed

#### Scenario: LLM connection failure is handled gracefully

- **WHEN** the sglang endpoint at `192.168.31.62:30000` is unreachable
- **THEN** the runner raises a `LlmUnavailableError` after the configured retry budget, and the caller can choose to retry or skip — partial state MUST NOT be written to the audit store

### Requirement: Tool registration

The system SHALL expose to the LLM agent at minimum the following tools, each invocable via the `<tool_call>{...}</tool_call>` text protocol inherited from `zadig_agent`: `search_notes(patient_id, section?, keyword?)`, `search_fees(patient_id, category?, keyword?)`, `note_diagnosis(patient_id)`, `drug_indication(drug_name)`. Each tool's bound DataFrame MUST come from the `DataLoader` interface, never from a direct file read inside the tool.

#### Scenario: Tool registry exposes the four required tools

- **WHEN** the runner is initialized
- **THEN** the tool executor reports exactly the four required tools as registered, each with a non-empty description string

#### Scenario: Tool calls are captured in the trace

- **WHEN** the agent invokes `search_fees(patient_id="J66252", keyword="肿瘤")` during an audit
- **THEN** the resulting `AuditResult.tool_calls` contains a record with `tool_name="search_fees"`, the full arguments dict, the truncated result text (≤2000 chars), and the call duration in ms

### Requirement: Prompt assembly from rule

The system SHALL assemble each audit's prompt from a base auditor system prompt (`src/javert/audit/prompts/base.txt`) plus the rule's `prompt_addon`, `question`, `example`, and `trigger_keywords`. The assembled prompt MUST instruct the LLM to (a) emit at least one tool call before reaching a verdict, (b) cite specific note or fee record identifiers in its `evidence` field, (c) return the final verdict in a fenced JSON block matching the `AuditResult` schema.

#### Scenario: Verdict is parsed from fenced JSON

- **WHEN** the LLM emits `\`\`\`json\n{"verdict": "VIOLATION", "confidence": 0.85, ...}\n\`\`\`` as its final message
- **THEN** the runner parses the JSON block and populates the corresponding fields of `AuditResult`

#### Scenario: Malformed final JSON is rescued once

- **WHEN** the final LLM message contains a JSON block with a syntax error
- **THEN** the runner sends one repair turn requesting a corrected JSON block; if the repair turn also fails, the runner emits `verdict: INCONCLUSIVE` with `reasoning` noting "malformed verdict JSON"

### Requirement: Dry-run mode prints the full trace

The system SHALL provide a `dry-run` operation distinguished from `run` by emitting the complete agent trace (every tool call, every LLM message, the final parsed verdict) to stdout in human-readable form, while still writing the `AuditResult` to the audit store. `dry-run` MUST be the default mode for single-patient invocations during rule design.

#### Scenario: Dry-run prints tool-call details

- **WHEN** operator runs `javert dry-run R191 --patient J66252`
- **THEN** stdout shows each tool call as `[Tool] tool_name(args) → summary` lines, each LLM turn as `[LLM] ...` lines, and the final verdict as `[Verdict] V/C/I conf=N.NN duration=Ns`

### Requirement: Batch run over the pilot roster

The system SHALL provide a `run` operation that accepts a `rule_id` and either an explicit `--patient` ID or a `--pilot` flag (consuming `data/pilot_patients.txt`), executes the audit for each patient sequentially, and writes every result to the audit store. The `run` operation MUST report progress to stderr (one line per patient with rule, patient, verdict, duration).

#### Scenario: Pilot run completes 50 patients

- **WHEN** operator runs `javert run R191 --pilot` with `pilot_patients.txt` listing 50 patients
- **THEN** the audit store contains 50 new records for `rule_id=R191`, the progress log contains 50 lines, and the command exits with status 0 if all 50 succeeded
