## ADDED Requirements

### Requirement: SqliteStore and ToolExecutor are thread-safe

`SqliteStore` SHALL protect its `write`, `mark_synced`, and `mark_sync_failed` methods with a `threading.Lock` instance held on `self._write_lock`. The underlying sqlite3 connection MUST be opened with `check_same_thread=False` so multiple threads may use the same connection (serialized by the lock).

`ToolExecutor` SHALL protect its `_cache` reads and writes (in `execute` and `reset_cache`) with a `threading.Lock` instance held on `self._cache_lock`. The lock MUST cover both the cache lookup and the post-execution cache write to prevent two threads from each missing the cache and both invoking the underlying tool function.

`Runner.audit` SHALL accept an optional `manage_patient_context: bool = True` keyword argument. When True (default, preserves existing behavior), `audit()` invokes `executor.set_patient_context(patient_id)` and `executor.clear_patient_context()`. When False, `audit()` skips both — the caller is responsible for setting/clearing the patient context (used by the audit-patient concurrent path, which sets once at batch entry and clears once at batch exit).

#### Scenario: concurrent SqliteStore writes do not lose rows

- **GIVEN** a `SqliteStore` opened on a temp DB
- **WHEN** 50 threads each call `store.write(result)` for a distinct AuditResult
- **THEN** the audit_runs table contains exactly 50 rows
- **AND** no thread raises `sqlite3.OperationalError("database is locked")`

#### Scenario: concurrent ToolExecutor cache lookups are race-safe

- **GIVEN** a ToolExecutor with one registered tool that increments a counter
- **WHEN** 50 threads concurrently call `execute({"name": "tool", "arguments": {"x": 1}})`
- **THEN** the counter increments by exactly 1 (one cold miss; remainder cache hits)
- **AND** at least 49 of the 50 `execute` returns have `cached=True`

#### Scenario: Runner.audit with manage_patient_context=False

- **GIVEN** an executor with patient_context = "PRE_SET_VALUE"
- **WHEN** `runner.audit(rule, "OTHER_PATIENT", manage_patient_context=False)` runs and returns
- **THEN** `executor._patient_context == "PRE_SET_VALUE"` (unchanged by the audit)

#### Scenario: Runner.audit with manage_patient_context=True (default)

- **GIVEN** an executor with patient_context = None
- **WHEN** `runner.audit(rule, "J66252")` runs and returns
- **THEN** `executor._patient_context is None` (set then cleared by audit)
