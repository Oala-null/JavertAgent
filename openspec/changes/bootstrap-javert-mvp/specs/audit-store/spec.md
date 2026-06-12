## ADDED Requirements

### Requirement: SQLite persistence with canonical schema

The system SHALL persist every `AuditResult` produced by the audit engine to a SQLite database at `output/audit.sqlite`. The schema MUST include a primary `audit_runs` table with columns `run_id (TEXT PK)`, `rule_id (TEXT NOT NULL)`, `patient_id (TEXT NOT NULL)`, `verdict (TEXT NOT NULL CHECK in VIOLATION/CLEAN/INCONCLUSIVE)`, `confidence (REAL)`, `reasoning (TEXT)`, `evidence_json (TEXT)`, `tool_calls_json (TEXT)`, `duration_ms (INTEGER)`, `model (TEXT)`, `started_at (TEXT)`, `created_at (TEXT DEFAULT CURRENT_TIMESTAMP)`. Indexes MUST exist on `(rule_id, patient_id)` and on `created_at`.

#### Scenario: Schema is created on first use

- **WHEN** the audit store is opened against a path where no SQLite file exists
- **THEN** the file is created, all required tables and indexes are present, and a `schema_version = 1` row is inserted into a `_meta` table

#### Scenario: Insert preserves all fields

- **WHEN** an `AuditResult` with all populated fields is inserted via `store.write(result)`
- **THEN** every column above is non-null in the resulting row and `evidence_json` / `tool_calls_json` round-trip back to equivalent Python objects via `json.loads`

### Requirement: Idempotent run identification

The system SHALL generate a deterministic `run_id` per audit invocation as a `nanoid` of length 12 prefixed with `aud_`, written before the audit begins. The audit store MUST allow multiple runs of the same `(rule_id, patient_id)` pair — historical runs are NOT overwritten, they are appended.

#### Scenario: Re-running an audit appends a new row

- **WHEN** operator runs `javert run R191 --patient J66252` twice on the same day
- **THEN** the audit store contains exactly two rows for `(R191, J66252)`, with distinct `run_id` and `started_at` values

### Requirement: Query by rule, patient, verdict

The system SHALL expose a query interface supporting at minimum: `find_by_rule(rule_id) -> list[AuditResult]`, `find_by_patient(patient_id) -> list[AuditResult]`, `find_by_verdict(verdict, since=None) -> list[AuditResult]`, and `summary_by_rule() -> dict[rule_id, {V, C, I, total}]`. Each query MUST execute against the existing indexes.

#### Scenario: Verdict distribution summary

- **WHEN** the store contains 50 records for R191 (15 VIOLATION, 30 CLEAN, 5 INCONCLUSIVE) and operator calls `summary_by_rule()`
- **THEN** the returned dict contains an entry `R191 → {V: 15, C: 30, I: 5, total: 50}`

#### Scenario: Recent VIOLATION lookup

- **WHEN** operator calls `find_by_verdict("VIOLATION", since=yesterday)`
- **THEN** the query returns only rows whose `created_at >= yesterday` and `verdict == "VIOLATION"`, ordered by `created_at` descending

### Requirement: Single-run trace inspection

The system SHALL provide a `show` operation that, given a `run_id` or a `(rule_id, patient_id)` pair (returning the most recent matching run), prints the full audit trace including every tool call, every LLM turn, the final verdict, and the elapsed time. The output MUST mirror the `dry-run` stdout format so operators can replay any historical audit.

#### Scenario: Show by run_id

- **WHEN** operator runs `javert show aud_a1b2c3d4e5f6`
- **THEN** stdout contains the full trace with `[Tool]`, `[LLM]`, and `[Verdict]` sections matching the `dry-run` format

#### Scenario: Show by rule + patient returns the latest run

- **WHEN** the store has three runs for `(R191, J66252)` and operator runs `javert show R191 --patient J66252`
- **THEN** the trace shown corresponds to the highest `started_at` value among those three runs
