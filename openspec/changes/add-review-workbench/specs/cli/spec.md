## ADDED Requirements

### Requirement: sync-to-mssql subcommand

The CLI SHALL expose a new subcommand `javert sync-to-mssql` that pushes rows from local sqlite `audit_runs` to remote SQL Server `javert_audit_runs`. The command is the one-shot migration tool for the initial 3345-row import AND the periodic catch-up for double-write failures.

#### Scenario: Default invocation pushes all rows

- **WHEN** the operator runs `javert sync-to-mssql` against a fresh `javert_audit_runs` table and 3345 sqlite rows
- **THEN** the CLI calls `SqlServerStore.ensure_schema()` first, then iterates sqlite rows in batches of 200, upserting each via `SqlServerStore.upsert_run()`; progress is printed every batch (`[200/3345] uploaded...`); on success exits 0 and prints summary `synced 3345 rows in <wall_time>s`

#### Scenario: --dry-run shows plan without writing

- **WHEN** the operator runs `javert sync-to-mssql --dry-run`
- **THEN** the CLI prints `would INSERT <N> rows / would UPDATE <M> rows / would SKIP <K> unchanged` based on diffing sqlite row count against existing `javert_audit_runs.run_id` count; no actual writes occur; exits 0

#### Scenario: --pending-only targets _sync_pending rows

- **WHEN** the operator runs `javert sync-to-mssql --pending-only` and sqlite has 8 rows with `_sync_pending=1`
- **THEN** only those 8 rows are pushed; on success, each row's `_sync_pending` is set back to 0 (via a second UPDATE pass); summary line `synced 8 pending rows, 0 remaining`

#### Scenario: --batch-size customization

- **WHEN** the operator runs `javert sync-to-mssql --batch-size 500`
- **THEN** sqlite rows are uploaded in batches of 500; progress prints every 500; if --batch-size is set ≤0 or >1000, CLI exits 2 with validation error

#### Scenario: SQL Server unreachable exits non-zero

- **WHEN** the operator runs `javert sync-to-mssql` and `SqlServerStore.healthcheck()` returns False
- **THEN** the CLI prints `error: SQL Server 192.168.31.142 unreachable` and exits 3 (a specific non-zero code so wrappers can distinguish "infrastructure problem" from other failure modes); no partial writes occur

#### Scenario: --target-host override for testing

- **WHEN** the operator runs `javert sync-to-mssql --target-host localhost --target-port 1434 --target-db test_zadig` (e.g. to test against a Docker SQL Server)
- **THEN** the override replaces env-var-loaded host/port/db just for this invocation; user/pw still come from env (no secret on cli)

### Requirement: web subcommand mssql toggle

The existing `javert web` subcommand SHALL gain `--with-mssql / --no-mssql` flags. Default behavior: auto-detect (if `JAVERT_MSSQL_*` env vars are set and the connection passes healthcheck, run with mssql; otherwise log warning and start in local-only mode).

#### Scenario: Explicit --with-mssql requires connectivity

- **WHEN** the operator runs `javert web --with-mssql --port 8090` and SQL Server is unreachable
- **THEN** the CLI exits 3 with `error: --with-mssql specified but SQL Server unreachable` (does NOT silently start in local mode when explicitly requested)

#### Scenario: Explicit --no-mssql skips connectivity check

- **WHEN** the operator runs `javert web --no-mssql --port 8090` (e.g. Mac dev without 142)
- **THEN** the CLI starts the app via `create_app(with_mssql=False)`; no SQL Server connection is attempted; server logs `mode: local-only (no-mssql)`

#### Scenario: Default auto-detect succeeds

- **WHEN** the operator runs `javert web --port 8090` and env vars are set, 142 is reachable
- **THEN** the CLI calls `SqlServerStore.from_env().healthcheck()` → True → constructs `create_app(with_mssql=True)`; logs `mode: production (mssql enabled)`

#### Scenario: Default auto-detect falls back

- **WHEN** the operator runs `javert web` and env vars are set but 142 is unreachable
- **THEN** the CLI logs WARN `SQL Server unreachable, falling back to local-only mode; some routes will return 503`; starts in local-only mode anyway; exits 0 on Ctrl-C as usual

#### Scenario: Backwards-compatible default port and host

- **WHEN** the operator runs `javert web` with no flags
- **THEN** the server binds to `127.0.0.1:8090` (default, unchanged from pre-this-change behavior); the production deployment must explicitly pass `--host 0.0.0.0 --port 8090` to listen on the LAN interface

### Requirement: ensure-mssql-schema subcommand

The CLI SHALL expose `javert ensure-mssql-schema` that runs the idempotent DDL to create the four `javert_*` tables. Used by Phase 0 deployment and CI provisioning.

#### Scenario: Fresh database creates all tables

- **WHEN** the operator runs `javert ensure-mssql-schema` against a database where no `javert_*` tables exist
- **THEN** all four tables are created (users / audit_runs / vio_review / audit_logs); stdout lists `created: javert_users, javert_audit_runs, javert_vio_review, javert_audit_logs`; exits 0

#### Scenario: Existing schema is no-op

- **WHEN** the operator runs `ensure-mssql-schema` a second time
- **THEN** stdout lists `unchanged: javert_users, ... (4 tables already exist)`; no exceptions; exits 0

#### Scenario: --drop-first to reset (development only)

- **WHEN** the operator runs `javert ensure-mssql-schema --drop-first` AND env var `JAVERT_ALLOW_DROP=1` is set
- **THEN** all four tables are dropped (in FK-safe order: audit_logs → vio_review → audit_runs → users) and then recreated; if `JAVERT_ALLOW_DROP` is unset, the flag is rejected with `--drop-first requires JAVERT_ALLOW_DROP=1 env var (this is irreversible)` and exits 2 — safety belt against accidental data loss

### Requirement: mssql-user admin subcommand

The CLI SHALL expose `javert mssql-user` with subcommands `list`, `delete`, `reset-password` for administrative tasks against the `javert_users` table. Users are created via the web `/register` route (per design.md D4), NOT via CLI — so there is no `mssql-user create` subcommand.

#### Scenario: list shows all users

- **WHEN** the operator runs `javert mssql-user list`
- **THEN** stdout shows a table with columns `id | username | display_name | created_at | last_login`; exit 0

#### Scenario: delete removes a user and their reviews

- **WHEN** the operator runs `javert mssql-user delete alice --confirm`
- **THEN** the CLI runs a transaction: UPDATE `javert_vio_review SET is_latest=0 WHERE user_id=<alice>` (orphan their reviews to non-latest), then DELETE FROM `javert_users WHERE username='alice'`; the user's audit_logs rows are NOT deleted (audit trail preserved with user_id pointing to deleted user); without `--confirm`, the CLI prints a dry-run summary and asks for `--confirm`

#### Scenario: reset-password sets new bcrypt hash

- **WHEN** the operator runs `javert mssql-user reset-password alice` (interactive prompt for new password)
- **THEN** the CLI reads password from stdin (hidden via `getpass`), bcrypts it, UPDATEs the row; logs `action=password_reset_by_admin, target_id=alice` to `javert_audit_logs` (with `user_id` of the admin/CLI operator if available, NULL otherwise — CLI is not authenticated like web is)

## MODIFIED Requirements

### Requirement: web subcommand bind host

The existing `javert web` subcommand previously bound to `127.0.0.1` by default. To support the Linux 192.168.31.62 deployment, the default MUST stay at `127.0.0.1` (safe-by-default) but operators MUST be able to pass `--host 0.0.0.0` to listen on all interfaces. (This requirement is mentioned to ensure no regression — pre-change behavior is preserved.)

#### Scenario: Default localhost binding unchanged

- **WHEN** the operator runs `javert web` with no `--host`
- **THEN** the server binds to `127.0.0.1:8090`; remote clients cannot reach it; pre-change behavior holds

#### Scenario: Explicit 0.0.0.0 enables LAN access

- **WHEN** the operator runs `javert web --host 0.0.0.0 --port 8090`
- **THEN** the server listens on all interfaces; clients on the 192.168.31.0/24 LAN can reach `http://192.168.31.62:8090/`
