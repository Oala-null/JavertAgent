## ADDED Requirements

### Requirement: SqlServerStore connection pool with pymssql

The system SHALL provide a `SqlServerStore` class that connects to a remote SQL Server instance via `pymssql`, exposes typed methods for the four `javert_*` tables, and maintains a connection pool. The driver MUST be `pymssql` (not `pyodbc`) so the same code path works on macOS and Linux without ODBC system dependencies.

#### Scenario: Establishing a connection with explicit config

- **WHEN** the system instantiates `SqlServerStore(host="192.168.31.142", port=1433, user="machendong", password="...", database="zadig")`
- **THEN** the store establishes a pymssql connection and caches it for reuse; a subsequent call to `.healthcheck()` returns `True` if the connection is alive

#### Scenario: Configuration via JAVERT_MSSQL_* environment variables

- **WHEN** the operator sets env vars `JAVERT_MSSQL_HOST`, `JAVERT_MSSQL_PORT`, `JAVERT_MSSQL_USER`, `JAVERT_MSSQL_PASSWORD`, `JAVERT_MSSQL_DATABASE`, then calls `SqlServerStore.from_env()`
- **THEN** the store reads those env vars (overriding `configs/llm.yaml mssql:` block if present) and constructs a connection; missing required vars MUST raise `MssqlConfigError` listing the missing keys

#### Scenario: Healthcheck failure does not raise

- **WHEN** SQL Server 142 is unreachable (timeout / connection refused) and the system calls `SqlServerStore.healthcheck()`
- **THEN** the method returns `False` and logs a warning at WARN level; it MUST NOT raise an exception (so callers can degrade gracefully)

#### Scenario: Connection pool reuses across requests

- **WHEN** a FastAPI request handler obtains a store instance via dependency injection (`Depends(get_mssql_store)`) and a second request arrives shortly after
- **THEN** both requests share the same underlying pymssql connection (the store is request-scoped wrapper around a process-level singleton pool); 100 sequential single-row queries complete in under 2 seconds total on a local intranet

### Requirement: Idempotent DDL for four javert_* tables

The system SHALL provide a method `SqlServerStore.ensure_schema()` that creates the four tables (`javert_users`, `javert_audit_runs`, `javert_vio_review`, `javert_audit_logs`) and their indexes if and only if they do not already exist. Re-invocation of `ensure_schema()` against a database that already has these tables MUST be a no-op and MUST NOT raise.

#### Scenario: First-run creates all four tables

- **WHEN** `ensure_schema()` runs against a fresh database where none of the four tables exist
- **THEN** all four tables are created with the columns and indexes specified in design.md D3; a follow-up query `SELECT name FROM sys.tables WHERE name LIKE 'javert_%'` returns exactly those four names

#### Scenario: Re-run is a no-op

- **WHEN** `ensure_schema()` is called a second time after a successful first run
- **THEN** no exception is raised; no DDL is re-executed (the method uses `IF NOT EXISTS` guards or catches the `42S01` "table already exists" code)

#### Scenario: Partial state recovery

- **WHEN** `ensure_schema()` runs against a database where 2 of the 4 tables exist (e.g. someone deleted `javert_vio_review` manually)
- **THEN** the method creates only the missing tables; existing tables and their data are untouched

#### Scenario: javert_users table schema

- **WHEN** `ensure_schema()` completes and the system describes `javert_users`
- **THEN** the table has columns: `id INT IDENTITY PRIMARY KEY`, `username NVARCHAR(64) UNIQUE NOT NULL`, `pw_hash NVARCHAR(255) NOT NULL`, `display_name NVARCHAR(128) NULL`, `created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()`, `last_login DATETIME2 NULL`

#### Scenario: javert_audit_runs table mirrors sqlite audit_runs schema

- **WHEN** `ensure_schema()` completes and the system describes `javert_audit_runs`
- **THEN** the table has primary key `run_id NVARCHAR(64)` and columns matching the sqlite `audit_runs` table (`patient_id`, `rule_id`, `verdict`, `confidence`, `reasoning`, `evidence_json`, `tool_calls_json`, `duration_ms`, `model`, `started_at`, `created_at`, `triggered_by`); four indexes for query speed: `ix_javert_runs_patient (patient_id)`, `ix_javert_runs_rule (rule_id)`, `ix_javert_runs_verdict (verdict)`, **`ix_javert_runs_created_at (created_at)`** (the last one is critical — the SSE audit watcher polls `WHERE created_at > @last_seen` every second, needs index seek not full scan)

#### Scenario: javert_vio_review table schema with is_latest flag

- **WHEN** `ensure_schema()` completes and the system describes `javert_vio_review`
- **THEN** the table has `id INT IDENTITY PK`, `run_id NVARCHAR(64) NOT NULL FK→javert_audit_runs`, `user_id INT NOT NULL FK→javert_users`, `review_verdict NVARCHAR(16) NOT NULL`, `comment NVARCHAR(MAX) NULL`, `created_at DATETIME2 DEFAULT SYSUTCDATETIME()`, `is_latest BIT NOT NULL DEFAULT 1`; one composite index `(run_id, user_id, is_latest)` for the "latest review per reviewer" query

#### Scenario: javert_audit_logs table schema for behavior trail

- **WHEN** `ensure_schema()` completes and the system describes `javert_audit_logs`
- **THEN** the table has `id BIGINT IDENTITY PK`, `user_id INT NULL` (nullable because failed logins have no user), `action NVARCHAR(32) NOT NULL`, `target_id NVARCHAR(64) NULL`, `payload_json NVARCHAR(MAX) NULL`, `ip NVARCHAR(45) NULL`, `user_agent NVARCHAR(255) NULL`, `ts DATETIME2 DEFAULT SYSUTCDATETIME()`; three indexes on `user_id`, `action`, `ts`

### Requirement: User CRUD operations

The system SHALL expose typed methods on `SqlServerStore` for user lifecycle: `create_user(username, pw_hash, display_name=None) -> int`, `get_user_by_username(username) -> User | None`, `get_user_by_id(user_id) -> User | None`, `update_last_login(user_id) -> None`. All methods MUST log via `javert_audit_logs` per their `action` semantics.

#### Scenario: Creating a new user

- **WHEN** the system calls `create_user("alice", "$2b$12$...", display_name="Dr. Alice")`
- **THEN** a row is inserted into `javert_users` with auto-generated `id`; the method returns the new `id`; a row is also inserted into `javert_audit_logs` with `action="register"`, `user_id=<new id>`, `target_id="alice"`

#### Scenario: Duplicate username is rejected

- **WHEN** `create_user("alice", ...)` is called twice with the same username
- **THEN** the second call raises `DuplicateUsernameError` (a typed exception wrapping the underlying `2627` unique constraint violation); no row is added the second time; `javert_audit_logs` records action=`register_fail`, target_id="alice"

#### Scenario: Lookup by username returns None for missing user

- **WHEN** `get_user_by_username("nobody")` is called and no such user exists
- **THEN** the method returns `None` (does NOT raise)

#### Scenario: Login update bumps last_login

- **WHEN** `update_last_login(user_id=5)` is called for an existing user
- **THEN** the `last_login` column is set to the current UTC time; `javert_audit_logs` records `action="login"`, `user_id=5`

### Requirement: Audit run upsert

The system SHALL expose `SqlServerStore.upsert_run(run_record: AuditRunRecord) -> None` that inserts a new row or updates an existing row in `javert_audit_runs` keyed by `run_id`. Upsert is required (not just insert) because re-running an audit can re-emit the same `run_id` in retry scenarios.

#### Scenario: New run_id is inserted

- **WHEN** `upsert_run(record)` is called with a `run_id` that does not exist in `javert_audit_runs`
- **THEN** a new row is inserted with all 12 columns populated from the record; `created_at` defaults to SYSUTCDATETIME if not provided

#### Scenario: Existing run_id is updated

- **WHEN** `upsert_run(record)` is called twice with the same `run_id` but different `reasoning` text
- **THEN** the second call updates the existing row (no duplicate insert); the final row has the second call's `reasoning`

#### Scenario: Upsert is atomic per row

- **WHEN** two concurrent processes call `upsert_run` with the same `run_id` simultaneously
- **THEN** SQL Server's MERGE semantics ensure exactly one row exists at the end (one process wins, neither raises); the winning row is determined by SQL Server's transaction order

### Requirement: Review submission with is_latest transition

The system SHALL expose `SqlServerStore.submit_review(run_id, user_id, verdict, comment, ip, user_agent) -> ReviewRecord` that performs the insert-only transition: UPDATE existing latest row to is_latest=0, INSERT new row with is_latest=1, and INSERT corresponding log entry — all in one transaction.

#### Scenario: First review for a (run_id, user_id) pair

- **WHEN** `submit_review("run_abc", 5, "V", "符合规则", "10.0.0.1", "Mozilla/5.0")` is called and no prior review exists for this pair
- **THEN** exactly one row is inserted into `javert_vio_review` with `is_latest=1`; one row is inserted into `javert_audit_logs` with `action="review_submit"`, `target_id="run_abc"`, `payload_json={"verdict":"V","comment":"符合规则","previous_verdict":null}`; the transaction commits as a unit

#### Scenario: Re-review by same user updates is_latest semantics

- **WHEN** `submit_review("run_abc", 5, "C", "重新看是干净的", ...)` is called after a prior `submit_review("run_abc", 5, "V", ...)` exists
- **THEN** the prior row's `is_latest` is updated to `0`; a new row is inserted with `is_latest=1, review_verdict="C"`; `javert_audit_logs` records `action="review_update"` with `payload_json` including `previous_verdict: "V"`; both review rows persist (no deletion); the query `SELECT * FROM javert_vio_review WHERE run_id="run_abc" AND user_id=5` returns 2 rows

#### Scenario: Different users review same run independently

- **WHEN** user 5 calls `submit_review("run_abc", 5, "V", ...)` and user 7 calls `submit_review("run_abc", 7, "C", ...)`
- **THEN** two rows exist in `javert_vio_review`, both with `is_latest=1`, one per user_id; both are valid latest reviews (single audit, two opinions)

#### Scenario: Transaction rollback on failure

- **WHEN** `submit_review` is called and the UPDATE step succeeds but the INSERT step fails (e.g. DB constraint violation or network drop mid-transaction)
- **THEN** the transaction is rolled back; `javert_vio_review` returns to its pre-call state (the prior latest row's `is_latest=1` is restored); no `audit_logs` row is left dangling

### Requirement: Behavior log append

The system SHALL expose `SqlServerStore.log_action(user_id, action, target_id=None, payload=None, ip=None, user_agent=None) -> None`. All mutating operations (register, login, login_fail, logout, review_submit, review_update, export) MUST call this method as part of their transaction. Read-only operations (browsing the workbench) MUST NOT log per-page-view (would spam the table).

#### Scenario: Login success logs action=login

- **WHEN** a user submits correct credentials and the auth route accepts
- **THEN** `log_action(user_id=5, action="login", target_id="alice", ip="...", user_agent="...")` is called

#### Scenario: Login failure logs action=login_fail with NULL user_id

- **WHEN** a user submits wrong credentials for username "alice"
- **THEN** `log_action(user_id=NULL, action="login_fail", target_id="alice", payload={"reason":"bad_password"}, ip="...")` is called; the table accepts `user_id=NULL`

#### Scenario: Export operation logs payload size

- **WHEN** a user requests `/export?scope=v_and_i`
- **THEN** `log_action(user_id=5, action="export", target_id="v_and_i", payload={"row_count": 395, "format": "xlsx"})` is called BEFORE the file is streamed

#### Scenario: Page views do NOT log

- **WHEN** a logged-in user navigates the workbench (`GET /workbench`, `GET /workbench/J66252`) repeatedly
- **THEN** zero rows are added to `javert_audit_logs` for these reads (we do not log browsing — the table would explode)

### Requirement: Read queries for workbench and dashboard

The system SHALL expose typed read methods supporting the workbench and dashboard UIs: `list_patients_with_violations(filter)`, `list_runs_for_patient(patient_id, filter)`, `list_reviews_for_run(run_id)`, `count_reviews_by_user(user_id)`, `verdict_distribution_by_rule()`.

#### Scenario: list_patients_with_violations respects V+I filter

- **WHEN** `list_patients_with_violations(filter="v_and_i")` is called against a database holding the 50-patient v0.5 snapshot
- **THEN** returns a list of patient summaries (patient_id, v_count, i_count, c_count, reviewed_count) ordered by v_count descending; patients with both v_count=0 AND i_count=0 are excluded

#### Scenario: list_patients_with_violations filter=all returns all

- **WHEN** `list_patients_with_violations(filter="all")` is called
- **THEN** returns all 50 patients including those with v_count=0 i_count=0 (e.g. J40485 with all CLEAN)

#### Scenario: list_runs_for_patient with reviews joined

- **WHEN** `list_runs_for_patient("J66252", filter="v_and_i")` is called and 18 rows match (18 V + I for this patient)
- **THEN** returns 18 run records, each enriched with `reviews: List[ReviewRecord]` containing all is_latest=1 reviews from any user; runs with zero reviews have `reviews: []`

#### Scenario: verdict_distribution_by_rule for dashboard

- **WHEN** `verdict_distribution_by_rule()` is called
- **THEN** returns one row per rule_id with javert_verdict counts (V/I/C) and expert_review counts grouped by review_verdict (V/I/C) and "agreement rate" (% of latest reviews that match javert's verdict)

### Requirement: Since-last-login stats for welcome banner

The system SHALL expose `SqlServerStore.get_since_last_login_stats(user_id: int, since: datetime | None) -> SinceLastLoginStats` returning a dataclass with `new_patients: int`, `new_violations: int`, `new_inconclusive: int`, `last_review_at: datetime | None`. Used by the workbench welcome banner (review-workbench D12) on every `GET /workbench` call. The method MUST execute in under 100ms over the intranet (no full-table scan; relies on `javert_audit_runs.created_at` index).

#### Scenario: Returning user gets accurate counts since prev login

- **WHEN** `get_since_last_login_stats(user_id=5, since=datetime(2026,5,18,10,0,0))` is called and between that timestamp and now: 3 distinct new patient_ids appeared in `javert_audit_runs`, 15 new rows with verdict='VIOLATION', 7 new rows with verdict='INCONCLUSIVE'; the user's own latest review was at `2026-05-19T15:00:00Z`
- **THEN** the returned dataclass has `new_patients=3, new_violations=15, new_inconclusive=7, last_review_at=datetime(2026,5,19,15,0,0)`

#### Scenario: First-login sentinel (since=None) returns total workspace counts

- **WHEN** `get_since_last_login_stats(user_id=5, since=None)` is called (first-ever login)
- **THEN** the method returns counts over the WHOLE workspace (not just the delta): `new_patients` = total distinct patient_ids ever audited, `new_violations` = total V rows ever, `new_inconclusive` = total I rows ever; `last_review_at=None` (no review yet, since this is first login)

#### Scenario: User who never reviewed

- **WHEN** `get_since_last_login_stats(user_id=5, since=...)` is called and the user has zero rows in `javert_vio_review` (with any is_latest value)
- **THEN** `last_review_at=None`; the workbench banner renders "尚未提交批复"

#### Scenario: Three queries max, no joins to heavy tables

- **WHEN** the method runs
- **THEN** it executes at most 3 SQL queries: (1) `SELECT COUNT(DISTINCT patient_id), SUM(CASE WHEN verdict='VIOLATION' THEN 1 ELSE 0 END), SUM(CASE WHEN verdict='INCONCLUSIVE' THEN 1 ELSE 0 END) FROM javert_audit_runs WHERE created_at > @since` (single roundtrip via cursor.execute with output args); (2) `SELECT MAX(created_at) FROM javert_vio_review WHERE user_id=@user_id AND is_latest=1` for last review timestamp; the queries hit the `ix_javert_runs_*` and `ix_review_user` indexes, total execution time under 100ms even with 100K+ rows

#### Scenario: Returned dataclass is pydantic typed

- **WHEN** the method returns
- **THEN** the result is a `SinceLastLoginStats` pydantic model (in `models.py`) with fields strictly typed (`new_patients: int >= 0`, `new_violations: int >= 0`, `new_inconclusive: int >= 0`, `last_review_at: datetime | None`); the model is JSON-serializable for embedding in templates

### Requirement: Incremental fetch for SSE audit watcher

The system SHALL expose `SqlServerStore.fetch_runs_since(last_seen: datetime, limit: int = 100) -> list[AuditRunRecord]` returning all rows from `javert_audit_runs` with `created_at > last_seen`, ordered by `created_at` ascending, limited to `limit` rows per call. Used by the SSE audit watcher background task (review-workbench D13) running every 1 second.

#### Scenario: Returns empty list when no new rows

- **WHEN** `fetch_runs_since(last_seen=<now>)` is called and no new audit rows have been written since
- **THEN** the method returns an empty list `[]`; no exception; total query time under 20ms (single index seek on `ix_javert_runs_created_at`)

#### Scenario: Returns rows in created_at ascending order

- **WHEN** `fetch_runs_since(last_seen=<T0>)` is called and 5 new rows exist with `created_at` values T1 < T2 < T3 < T4 < T5
- **THEN** the method returns a list of 5 `AuditRunRecord` objects in order [T1, T2, T3, T4, T5]; the caller can safely set `last_seen = result[-1].created_at` for next iteration

#### Scenario: Limit caps single-call result size

- **WHEN** `fetch_runs_since(last_seen=<old>, limit=100)` is called and 250 new rows exist since `last_seen`
- **THEN** the method returns exactly 100 rows (the oldest 100); the caller's next iteration with the new `last_seen = result[-1].created_at` picks up the remaining 150 rows; this bounded fetch prevents memory spike if many rows were inserted at once (e.g. via `sync-to-mssql` bulk import)

#### Scenario: Equal-created_at boundary handling

- **WHEN** two rows have the exact same `created_at` (rare but possible at sub-millisecond) and `last_seen` equals that timestamp
- **THEN** the method uses `created_at > @last_seen` (strict greater-than) so both rows are EXCLUDED on this iteration; this means the rows are missed unless the caller uses `>=` semantics — which we explicitly DO NOT (to avoid duplicate broadcasts). Trade-off accepted: in the rare collision case those rows surface on next page load, not via SSE. Document this in the watcher implementation comment.

### Requirement: Helper to check if patient has other runs

The system SHALL expose `SqlServerStore.has_other_runs(patient_id: str, exclude_run_id: str) -> bool` returning True if `javert_audit_runs` has any row with the given `patient_id` except for the specified `run_id`. Used by the SSE watcher to compute the `is_new_patient` flag in the broadcast payload.

#### Scenario: First-ever run for a patient

- **WHEN** `has_other_runs(patient_id="J88888", exclude_run_id="run_new_abc")` is called and `J88888` has only that one row (it's a brand-new patient being audited for the first time)
- **THEN** the method returns `False`; the SSE event payload's `is_new_patient` flag is set to `True`

#### Scenario: Patient with prior audits

- **WHEN** `has_other_runs(patient_id="J66252", exclude_run_id="run_new_xyz")` is called and J66252 already has 110 other rows
- **THEN** the method returns `True`; the SSE event's `is_new_patient` flag is set to `False`

#### Scenario: Query uses EXISTS for speed

- **WHEN** the method runs
- **THEN** the SQL is `SELECT TOP 1 1 FROM javert_audit_runs WHERE patient_id=@pid AND run_id<>@exclude` (returns 1 row or 0 rows); query plan uses `ix_javert_runs_patient` index seek; total execution under 5ms even with 100K+ rows in the table

### Requirement: Sync from sqlite audit_runs

The system SHALL provide a method `SqlServerStore.bulk_upsert_from_sqlite(sqlite_path, batch_size=200) -> SyncReport` that reads all rows from a local `audit_runs` table and upserts them to `javert_audit_runs` in batches. Used for both the one-shot 3345-row migration and ongoing catch-up of double-write failures.

#### Scenario: Dry-run mode prints plan

- **WHEN** the operator runs `javert sync-to-mssql --dry-run`
- **THEN** stdout shows expected operation counts (`will INSERT 3345, will UPDATE 0, will SKIP 0 unchanged`) and exits without writing 142

#### Scenario: Batch insert with progress

- **WHEN** the operator runs `javert sync-to-mssql --batch-size 200` against an empty `javert_audit_runs` and 3345 sqlite rows
- **THEN** progress is printed every 200 rows (`[200/3345] inserted... [400/3345] ...`); total wall time < 5 minutes on intranet; final count `SELECT COUNT(*) FROM javert_audit_runs == 3345`

#### Scenario: Re-run is idempotent

- **WHEN** the operator runs `javert sync-to-mssql` twice in a row
- **THEN** the second run reports `0 INSERT, 3345 UPDATE` (or `0 INSERT, 0 UPDATE` if "skip-unchanged" detection is on); no duplicates in `javert_audit_runs`

#### Scenario: Catch-up mode targets _sync_pending rows

- **WHEN** the operator runs `javert sync-to-mssql --pending-only` and the sqlite `audit_runs` table has 8 rows with `_sync_pending=1`
- **THEN** only those 8 rows are pushed to 142; after success, their `_sync_pending` is cleared in sqlite (via a separate UPDATE pass)

### Requirement: Pydantic models for typed data interchange

The system SHALL define pydantic models in `src/javert/persistence/models.py` for all four entity types: `User`, `AuditRunRecord`, `ReviewRecord`, `AuditLogRecord`. All `SqlServerStore` methods MUST accept/return these typed models, never raw tuples or dicts.

#### Scenario: User model fields

- **WHEN** the system loads `from javert.persistence.models import User`
- **THEN** the model has fields: `id: int`, `username: str`, `display_name: str | None`, `created_at: datetime`, `last_login: datetime | None`; the `pw_hash` field is excluded from default serialization (Config.exclude={"pw_hash"}) to prevent accidental log leakage

#### Scenario: AuditRunRecord matches sqlite audit_runs schema

- **WHEN** the system loads `AuditRunRecord`
- **THEN** the model has the same fields as the sqlite `audit_runs` row plus an optional `_sync_pending: bool = False`; field types align with both pymssql column types and pydantic V2 conventions

#### Scenario: ReviewRecord includes is_latest

- **WHEN** the system loads `ReviewRecord`
- **THEN** the model has fields: `id: int`, `run_id: str`, `user_id: int`, `review_verdict: Literal["V","I","C"]`, `comment: str | None`, `created_at: datetime`, `is_latest: bool`

### Requirement: CompositeStore for double-write

The system SHALL provide a `CompositeStore` adapter (in `src/javert/persistence/composite.py`) that wraps a `SqliteStore` and an optional `SqlServerStore`. Write methods MUST call sqlite first (strict consistency), then attempt SQL Server (best-effort). SQL Server write failures MUST be logged as warnings and the sqlite row MUST be marked `_sync_pending=1`.

#### Scenario: Both stores succeed

- **WHEN** `CompositeStore.persist_run(record)` is called with both stores healthy
- **THEN** sqlite is written first (synchronously commits), then SQL Server is written; both succeed; the sqlite row has `_sync_pending=0`

#### Scenario: SQL Server fails, sqlite still succeeds

- **WHEN** `CompositeStore.persist_run(record)` is called and the SQL Server write raises `MssqlWriteError`
- **THEN** sqlite still holds the row (commit was already done); the row's `_sync_pending` is set to `1`; the error is logged at WARN level with the run_id and exception details; the method does NOT raise (so the caller's audit pipeline continues)

#### Scenario: Operator-configured local-only mode

- **WHEN** the operator instantiates `CompositeStore(sqlite_store, mssql_store=None)` (e.g. Mac dev mode `javert web --no-mssql`)
- **THEN** all write methods only hit sqlite; no SQL Server attempts; no `_sync_pending` flag is set (the table column may still exist but stays 0)

#### Scenario: Read methods prefer SQL Server if available

- **WHEN** `CompositeStore.list_patients_with_violations(filter="v_and_i")` is called and `mssql_store` is set
- **THEN** the read is served from SQL Server (which has the joined review data); if `mssql_store` is None (local-only), the read falls back to sqlite and returns runs without review enrichment (reviews are 142-only data)
