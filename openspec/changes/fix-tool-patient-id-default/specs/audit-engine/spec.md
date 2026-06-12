## ADDED Requirements

### Requirement: Tool patient_id auto-injection

`ToolExecutor` SHALL support a per-instance `patient_context` state managed via `set_patient_context(patient_id: str)` and `clear_patient_context()`. When `execute(tool_call)` runs a tool whose module declares `REQUIRES_PATIENT_ID = True` and the tool_call `arguments` does NOT contain a `patient_id` key, the executor MUST merge `{"patient_id": self._patient_context}` into a copy of arguments before computing the cache key and invoking the tool function. Tools with `REQUIRES_PATIENT_ID = False` (e.g. `drug_indication`) SHALL be invoked without any injection.

`Runner.audit(rule, patient_id, reset_cache)` MUST call `executor.set_patient_context(patient_id)` after the optional `reset_cache()` and before any LLM call, and MUST call `executor.clear_patient_context()` in a `finally` block that wraps the entire audit body, so that exception paths also restore the executor to a clean state.

#### Scenario: patient_id auto-injection on missing arg

- **GIVEN** ToolExecutor has registered `search_fees` with `requires_patient_id=True`
- **AND** `executor.set_patient_context("J66252")` was called
- **WHEN** `executor.execute({"name": "search_fees", "arguments": {"category": "手术类"}})` runs
- **THEN** the tool function receives `patient_id="J66252"` AND `category="手术类"`
- **AND** the cache key reflects `{"category": "手术类", "patient_id": "J66252"}`

#### Scenario: explicit patient_id is preserved

- **GIVEN** ToolExecutor with `patient_context = "J66252"`
- **WHEN** `execute({"name": "search_fees", "arguments": {"patient_id": "OTHER", "category": "X"}})` runs
- **THEN** the tool function receives `patient_id="OTHER"` (LLM-supplied value wins; no override)

#### Scenario: drug_indication unaffected

- **GIVEN** `drug_indication` registered with `requires_patient_id=False`
- **AND** `patient_context = "J66252"`
- **WHEN** `execute({"name": "drug_indication", "arguments": {"drug_name": "顺铂"}})` runs
- **THEN** the tool function receives ONLY `drug_name="顺铂"`, no patient_id

#### Scenario: cache key unified across injection modes

- **GIVEN** `patient_context = "J66252"` and `search_fees` requires patient_id
- **WHEN** `execute({"arguments": {"category": "X"}})` runs (injection path) then `execute({"arguments": {"patient_id": "J66252", "category": "X"}})` runs (explicit)
- **THEN** the second call returns `cached=True`

#### Scenario: context cleared after audit

- **GIVEN** a Runner with a shared ToolExecutor
- **WHEN** `runner.audit(rule, "J66252")` returns (successfully or by raising)
- **THEN** `executor._patient_context is None`

#### Scenario: finally clears on LlmUnavailableError

- **GIVEN** `audit()` is configured to raise `LlmUnavailableError` after `set_patient_context`
- **WHEN** the audit raises
- **THEN** the exception propagates AND `executor._patient_context is None` (verified by inspecting the executor after the raise)
