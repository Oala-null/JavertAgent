## ADDED Requirements

### Requirement: SQL Server knowledge writes require an explicit owned target

Every knowledge DDL, migration, upload, materialization, approval, and release command SHALL require an explicit `--database` argument with no default. Before opening a write connection, the requested database MUST be present in `JAVERT_OWNED_DBS`. For the requested deployment, the database name MUST be exactly `知识库_work`; `sh_yb_platform` MUST remain read-only and existing `TP_data_hub` authorization MUST NOT implicitly authorize this database.

#### Scenario: Knowledge database is not owned

- **WHEN** an operator requests `--database 知识库_work` but that exact name is absent from `JAVERT_OWNED_DBS`
- **THEN** the command fails before creating a database connection or executing SQL

#### Scenario: Database argument is omitted

- **WHEN** an operator runs a mutating knowledge command without `--database`
- **THEN** argument parsing fails and no default database is selected

### Requirement: Connected database is verified before first mutation

After connecting and before any mutation, the system MUST query `DB_NAME()` and require an exact match with the requested database. DDL execution SHALL also require the connection database and a `KB_DATABASE` script variable to match before the first schema or table statement. Identifiers MUST be safely quoted and data values MUST be parameterized.

#### Scenario: Login defaults to another database

- **WHEN** connection settings silently place the session in `TP_data_hub` while `--database 知识库_work` was requested
- **THEN** the preflight aborts before DDL or DML and reports a target mismatch without exposing credentials

#### Scenario: DDL variable and connection disagree

- **WHEN** `sqlcmd -d` connects to one database but `KB_DATABASE` names another
- **THEN** the DDL script exits non-zero before creating `[kb]` or `[kb_stg]`

### Requirement: Knowledge schema is normalized and version-aware

`[知识库_work]` SHALL contain separate staging and business schemas. Business tables MUST represent versioned sources, drug concepts/products/codes, curated knowledge atoms and their migration mappings, eligibility rule revisions/branches/nodes, regimen revisions/aliases/contexts/components, append-only review events, immutable releases, and release membership using typed columns and referential constraints. The permanent business model MUST NOT store the entire workbook only as an opaque JSON or binary object.

#### Scenario: Knowledge schema is created twice

- **WHEN** the schema migration is executed twice against an authorized empty-or-current database
- **THEN** the second execution succeeds without dropping data or duplicating constraints

#### Scenario: Orphan condition node is inserted

- **WHEN** materialization attempts to insert a condition node whose branch, parent, source fragment, or referenced concept does not exist
- **THEN** the transaction fails and no partial revision is committed

#### Scenario: Curated knowledge mapping has no target

- **WHEN** materialization attempts to mark a knowledge atom mapped or verified without a valid typed target identity and source checksum
- **THEN** the transaction fails and preserves the atom only in its prior migration state

#### Scenario: Planned curated target is not live yet

- **WHEN** a `MAPPED` atom has a stable typed target identity but its planned `CONDITION` or `DICTIONARY` target has not yet been materialized
- **THEN** the mapping is preserved as non-verified authoring backlog
- **AND** any transition to `VERIFIED` fails until exactly one mapping has non-empty verification evidence and a live target authority

### Requirement: Workbook upload is staged and idempotent

Validated workbooks SHALL be uploaded as an import batch with workbook SHA-256, template version, safe basename, uploader, timestamps, expected counts, and per-row canonical payload/checksum. The same workbook checksum and template version MUST resolve to the existing batch instead of creating duplicate revisions. Excel binaries, absolute client paths, and credentials MUST NOT be stored in SQL Server.

#### Scenario: Same workbook is uploaded twice

- **WHEN** an operator repeats an upload with identical workbook bytes and template schema version
- **THEN** the second operation returns the original batch ID and current status
- **AND** it creates no duplicate staging rows or authoring revisions

#### Scenario: One staging row is invalid

- **WHEN** a batch contains a row that fails reference, date, enum, checksum, or tree validation
- **THEN** the batch status becomes validation-failed with safe row diagnostics
- **AND** no row from the batch is materialized into `[kb]`

### Requirement: Materialization is transactional and reconciled

A fully valid staging batch SHALL materialize into draft/review revisions in one database transaction. The process MUST report staged, inserted, reused, updated-draft, and rejected counts for every entity type and verify that logical IDs and row checksums reconcile. Any exception or count mismatch MUST roll back all formal writes while preserving the failed batch audit record.

#### Scenario: Failure occurs halfway through materialization

- **WHEN** source rows are inserted but a later condition-node constraint fails
- **THEN** the entire authoring transaction is rolled back
- **AND** the import batch retains a failed status and error summary for diagnosis

#### Scenario: Materialization completes

- **WHEN** all rows pass and the transaction commits
- **THEN** the reconciliation report accounts for every staging row and formal revision
- **AND** upload still leaves those revisions non-released

### Requirement: Validation and dry-run perform no database mutation

The toolchain SHALL provide local workbook validation without any database connection and a database preflight/dry-run that performs only read-only capability and target checks. Dry-run output MUST identify the target database and schemas, safe source basename, checksums, and expected counts, while omitting host credentials and workbook cell contents.

#### Scenario: Local validation is used offline

- **WHEN** an operator validates a workbook without SQL Server access
- **THEN** schema, row, tree, date, reference, and privacy checks run locally and no connection is attempted

#### Scenario: Dry-run is requested

- **WHEN** an authorized operator runs upload with `--dry-run`
- **THEN** the command reports planned staging and materialization counts
- **AND** no import batch, staging row, revision, review event, or release row is created

### Requirement: Credentials and patient data never enter knowledge artifacts

SQL host credentials, passwords, env-file contents, patient identifiers, original patient notes, unsalted run identifiers, and ownership identifiers MUST NOT be written to workbooks, SQL payloads, manifests, reports, logs, or commit messages. Credentials SHALL come only from process environment or a controlled env file and MUST be redacted from exceptions.

#### Scenario: Database connection fails

- **WHEN** authentication or network connection raises an exception
- **THEN** logs identify only safe target context and error class
- **AND** no password, connection URL, raw env value, or workbook contents are emitted

#### Scenario: Regimen candidate came from clinical text

- **WHEN** the candidate is uploaded to `[知识库_work]`
- **THEN** only its normalized alias, aggregate frequency, allowed context, source-corpus checksum, and review state are stored
- **AND** patient-level anchors are absent
