## ADDED Requirements

### Requirement: Composite store double-write integration

The `Runner` and `audit-patient` orchestrator SHALL accept a `CompositeStore` (from the `persistence` capability) instead of a bare `SqliteStore`. The composite handles both writes: sqlite first (strict), SQL Server second (best-effort). Existing callers passing a `SqliteStore` directly MUST continue to work by being silently wrapped in a `CompositeStore(sqlite_store, mssql_store=None)`.

#### Scenario: Runner persists to both stores in production mode

- **WHEN** the operator runs `javert audit-patient J66252 --priority P0` against a production environment (env vars `JAVERT_MSSQL_*` set, SQL Server reachable)
- **THEN** each rule's `AuditResult` is written to local sqlite `audit_runs` (via `SqliteStore`) AND to remote SQL Server `javert_audit_runs` (via `SqlServerStore.upsert_run`); both writes occur for every rule; total wall time is within 5% of the baseline (single-store) timing — SQL Server write adds ≤ 50ms per row over intranet

#### Scenario: SQL Server unavailable does not block audit

- **WHEN** the operator runs `javert audit-patient J66252` and SQL Server 142 is unreachable
- **THEN** sqlite writes still succeed for all rules; each SQL Server write attempt logs a WARN like `[mssql-write-fail] run_id=... rule=R191 error=...`; the sqlite rows have `_sync_pending=1`; the CLI exits 0 (audit succeeded); end-of-run summary includes a line `mssql_sync: 30 pending (catch up with: javert sync-to-mssql --pending-only)`

#### Scenario: Backwards compatibility with bare SqliteStore

- **WHEN** existing code calls `Runner(store=SqliteStore(path), ...)` (legacy)
- **THEN** the system implicitly wraps it: `composite = CompositeStore(store, mssql_store=None)`; behavior is identical to pre-change (single-store sqlite writes); no SQL Server attempts are made

#### Scenario: Dry-run does not write to either store

- **WHEN** the operator runs `javert dry-run R191 --patient J66252` (existing dry-run, not modified by this change)
- **THEN** neither store is written (dry-run was always read-only); no `javert_audit_runs` row appears; no `_sync_pending` marker is set anywhere

### Requirement: Sync-pending flag in sqlite audit_runs

The local sqlite `audit_runs` table SHALL gain a column `_sync_pending INTEGER NOT NULL DEFAULT 0` via a migration on first run after this change is deployed. Existing rows (3345 pre-change) get `_sync_pending=0` initially — they'll be flipped to 1 by the Phase 1 migration script if and only if they fail to push to 142, then flipped back to 0 after successful sync.

#### Scenario: Migration adds column idempotently

- **WHEN** the `SqliteStore` is constructed and detects the `audit_runs` table exists without `_sync_pending` column
- **THEN** the migration runs `ALTER TABLE audit_runs ADD COLUMN _sync_pending INTEGER NOT NULL DEFAULT 0`; subsequent restarts detect the column exists and do not re-run

#### Scenario: Double-write failure sets the flag

- **WHEN** the composite store writes a new run to sqlite successfully, then the SQL Server upsert raises an exception
- **THEN** an UPDATE runs against sqlite to set `_sync_pending=1` for that run_id; the column is the only thing changed; the original row data is untouched

#### Scenario: Catch-up sync clears the flag

- **WHEN** `javert sync-to-mssql --pending-only` runs and successfully pushes 8 pending rows to SQL Server
- **THEN** each of those sqlite rows has its `_sync_pending` updated to 0; future runs of the catch-up command return "0 pending"

### Requirement: Audit summary reports mssql_sync status

The `audit-patient` summary block (after all rules complete) SHALL include a line indicating whether all writes synced to SQL Server. Format: `mssql_sync: <N>/<M> succeeded (<P> pending)` where N = succeeded, M = total, P = pending.

#### Scenario: All writes succeeded

- **WHEN** an `audit-patient` run completes with 30 rules and SQL Server is healthy throughout
- **THEN** the summary contains `mssql_sync: 30/30 succeeded (0 pending)`

#### Scenario: Partial failure

- **WHEN** an `audit-patient` run completes with 30 rules and SQL Server is unavailable for rules 15-20 (recovers after)
- **THEN** the summary contains `mssql_sync: 24/30 succeeded (6 pending)`; the 6 pending row IDs are listed in a `slow_sync: [run_id_1, run_id_2, ...]` line for traceability

#### Scenario: Local-only mode

- **WHEN** an `audit-patient` run completes and the `CompositeStore` was instantiated with `mssql_store=None` (e.g. Mac dev mode)
- **THEN** the summary contains `mssql_sync: disabled (local-only mode)` instead of a count; the line is dimmed (different style) in stdout

### Requirement: Triggered_by reflects audit-patient lineage

The `triggered_by` column in `audit_runs` (and the mirrored `javert_audit_runs`) SHALL be populated by the audit orchestrator to indicate which CLI command produced the row. This is needed by the workbench to filter "show me all runs from a specific audit batch" or "show me only patient-centric runs".

#### Scenario: cli-audit-patient

- **WHEN** a row is persisted from an `audit-patient` invocation
- **THEN** its `triggered_by` value is `cli-audit-patient`

#### Scenario: cli-run-pilot

- **WHEN** a row is persisted from `javert run R191 --pilot`
- **THEN** its `triggered_by` value is `cli-run-pilot`

#### Scenario: cli-batch (50 patients runner)

- **WHEN** a row is persisted from `scripts/run_batch_50patients.py`
- **THEN** its `triggered_by` value is `cli-batch-50p` (the script sets this via env var or `--triggered-by` flag passed through to `audit-patient`)

#### Scenario: web-trigger reserved for future

- **WHEN** future code triggers an audit from the web workbench (out of scope for this change)
- **THEN** the convention is `triggered_by=web-<reason>` (e.g. `web-rerun-by-reviewer`); the workbench can filter on this prefix
