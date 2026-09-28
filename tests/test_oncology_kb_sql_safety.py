from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from javert.cli import main
from javert.oncology.authoring.sqlserver import (
    KNOWLEDGE_DATABASE,
    _REQUIRED_SCHEMA_TRIGGERS,
    _REQUIRED_SCHEMA_VIEWS,
    KnowledgeDatabaseError,
    apply_transaction,
    connect_knowledge_database,
    mark_server_validation,
    preflight_connection,
    safe_database_error,
    upload_staging,
    validate_staging_rows,
)
from javert.oncology.authoring.ids import checksum
from javert.oncology.authoring.staging import build_staging_rows
from javert.oncology.authoring.workbooks import parse_workbook
from javert.store.write_safety import validate_owned_database


ROOT = Path(__file__).resolve().parents[1]
DDL = ROOT / "scripts/sql/create_oncology_kb_schema.sql"
ELIGIBILITY = ROOT / "outputs/add-oncology-kb-authoring/肿瘤药指南适应证与医保限定条件树KB.xlsx"


def test_knowledge_database_requires_exact_owned_target_before_connect() -> None:
    calls = []

    def connector(database: str):
        calls.append(database)
        return object()

    with pytest.raises(ValueError):
        connect_knowledge_database(KNOWLEDGE_DATABASE, connector, owned_raw="TP_data_hub")
    with pytest.raises(ValueError):
        connect_knowledge_database("TP_data_hub", connector, owned_raw="TP_data_hub,知识库_work")
    assert calls == []
    assert validate_owned_database(KNOWLEDGE_DATABASE, required_exact=KNOWLEDGE_DATABASE, owned_raw="知识库_work") == KNOWLEDGE_DATABASE


def test_schema_apply_requires_database_and_rejects_unknown_before_connect(monkeypatch) -> None:
    runner = CliRunner()
    missing = runner.invoke(main, ["oncology-kb", "schema-apply"])
    assert missing.exit_code == 2 and "--database" in missing.output
    calls = []
    monkeypatch.setattr(
        "javert.commands.oncology_kb._open_connection",
        lambda database: calls.append(database),
    )
    rejected = runner.invoke(
        main,
        ["oncology-kb", "schema-apply", "--database", KNOWLEDGE_DATABASE],
        env={"JAVERT_OWNED_DBS": "TP_data_hub"},
    )
    assert rejected.exit_code != 0 and calls == []


def test_ddl_fails_closed_before_first_schema_or_table_statement() -> None:
    sql = DDL.read_text(encoding="utf-8")
    executable = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert re.search(r"(?im)^\s*:on\s+error\s+exit\s*$", executable)
    assert not re.search(r"(?im)^\s*:setvar\s+KB_DATABASE\b", executable)
    db_guard = re.search(r"(?is)IF\s+DB_NAME\s*\(\s*\)\s*<>\s*N'\$\(KB_DATABASE\)'", executable)
    var_guard = re.search(r"(?is)IF\s+N'\$\(KB_DATABASE\)'\s*<>\s*N'知识库_work'", executable)
    first_ddl = re.search(r"(?is)\b(?:CREATE\s+(?:TABLE|SCHEMA)|ALTER\s+TABLE|DROP\s+)\b", executable)
    assert db_guard and var_guard and first_ddl
    assert var_guard.start() < db_guard.start() < first_ddl.start()


def test_ddl_covers_all_required_tables_constraints_indexes_and_immutability() -> None:
    sql = DDL.read_text(encoding="utf-8")
    tables = {
        "import_batch", "import_row", "source_document", "source_fragment",
        "drug_concept", "drug_product", "drug_code_xref", "curated_knowledge_atom",
        "curated_knowledge_mapping", "eligibility_rule_revision", "eligibility_branch",
        "condition_node", "regimen_revision", "regimen_alias", "regimen_context",
        "regimen_component", "regimen_schedule_component", "review_event",
        "knowledge_release", "release_item", "schema_version", "deployment_pointer",
    }
    for table in tables:
        assert f"[{table}]" in sql
    assert "FOREIGN KEY" in sql and "CHECK" in sql and "UNIQUE" in sql and "CREATE INDEX" in sql
    assert "append-only" in sql and "immutable" in sql
    assert "DROP TABLE" not in sql.upper()
    assert "expected_counts_json" in sql
    assert "tr_eligibility_revision_no_overlap" in sql
    assert "tr_regimen_revision_no_overlap" in sql
    assert "tr_eligibility_revision_immutable" in sql
    assert "tr_regimen_revision_immutable" in sql
    assert "APPLY_CURRENT_RELEASE_WITH_WARNING" in sql
    review = re.search(
        r"CREATE TABLE \[kb\]\.\[review_event\] \((.*?)\n    \);",
        sql,
        re.S,
    )
    assert review
    assert "[reviewed_content_checksum] char(71) NOT NULL" in review.group(1)
    assert "CK_kb_reviewed_content_checksum" in review.group(1)
    assert "LEN([reviewed_content_checksum]) = 71" in review.group(1)
    assert "COL_LENGTH(N'kb.review_event', N'reviewed_content_checksum')" in sql
    assert "既有 review_event 必须人工补齐 reviewed_content_checksum" in sql
    assert "禁止自动猜测" in sql
    assert "ALTER COLUMN [reviewed_content_checksum] char(71) NOT NULL" in sql
    assert "ALTER TABLE [kb].[review_event] WITH CHECK" in sql


def test_ddl_creates_human_readable_review_views() -> None:
    sql = DDL.read_text(encoding="utf-8")
    created = set(
        re.findall(r"CREATE OR ALTER VIEW \[kb\]\.\[([^]]+)\]", sql)
    )
    assert created == set(_REQUIRED_SCHEMA_VIEWS)
    for view_name in _REQUIRED_SCHEMA_VIEWS:
        assert f"(N'{view_name}')" in sql


def test_ddl_registers_schema_version_only_after_all_triggers_are_verified() -> None:
    sql = DDL.read_text(encoding="utf-8")
    last_trigger = sql.rindex("CREATE OR ALTER TRIGGER")
    capability_check = sql.rindex("AS required([trigger_name], [parent_table])")
    version_registration = sql.rindex(
        "INSERT INTO [kb_meta].[schema_version]([version_no], [migration_checksum])"
    )
    commit = sql.rindex("COMMIT TRANSACTION;")
    assert last_trigger < capability_check < version_registration < commit
    assert sql.count("COMMIT TRANSACTION;") == 1
    assert "installed.[is_disabled] = 0" in sql[capability_check:version_registration]
    for trigger, _parent in _REQUIRED_SCHEMA_TRIGGERS:
        assert f"(N'{trigger}'," in sql[capability_check - 5000:version_registration]


def test_ddl_freezes_children_and_referenced_sources_for_all_terminal_revisions() -> None:
    sql = DDL.read_text(encoding="utf-8")
    child_triggers = {
        "tr_eligibility_branch_parent_immutable",
        "tr_condition_node_parent_immutable",
        "tr_regimen_alias_parent_immutable",
        "tr_regimen_context_parent_immutable",
        "tr_regimen_component_parent_immutable",
        "tr_regimen_schedule_parent_immutable",
    }
    reference_triggers = {
        "tr_source_fragment_frozen_reference",
        "tr_drug_concept_frozen_reference",
    }
    for trigger in child_triggers:
        block = re.search(
            rf"CREATE OR ALTER TRIGGER \[kb\]\.\[{trigger}\](.*?)\nGO",
            sql,
            re.S,
        )
        assert block
        assert "AFTER INSERT, UPDATE, DELETE" in block.group(1)
        assert "IN ('APPROVED','RELEASED','RETIRED')" in block.group(1)
    for trigger in reference_triggers:
        block = re.search(
            rf"CREATE OR ALTER TRIGGER \[kb\]\.\[{trigger}\](.*?)\nGO",
            sql,
            re.S,
        )
        assert block
        assert "AFTER UPDATE, DELETE" in block.group(1)
        assert "IN ('APPROVED','RELEASED','RETIRED')" in block.group(1)
    for trigger in (
        "tr_eligibility_revision_immutable",
        "tr_regimen_revision_immutable",
    ):
        block = re.search(
            rf"CREATE OR ALTER TRIGGER \[kb\]\.\[{trigger}\](.*?)\nGO",
            sql,
            re.S,
        )
        assert block
        assert "cannot be deleted" in block.group(1)
        assert "lifecycle cannot regress" in block.group(1)

    atom = re.search(
        r"CREATE OR ALTER TRIGGER \[kb\]\.\[tr_curated_atom_verified_immutable\](.*?)\nGO",
        sql,
        re.S,
    )
    mapping = re.search(
        r"CREATE OR ALTER TRIGGER \[kb\]\.\[tr_curated_mapping_verified_immutable\](.*?)\nGO",
        sql,
        re.S,
    )
    authority = re.search(
        r"CREATE OR ALTER TRIGGER \[kb\]\.\[tr_curated_mapping_target_authority\](.*?)\nGO",
        sql,
        re.S,
    )
    atom_authority = re.search(
        r"CREATE OR ALTER TRIGGER \[kb\]\.\[tr_curated_atom_target_authority\](.*?)\nGO",
        sql,
        re.S,
    )
    assert atom and "migration_status] = 'VERIFIED'" in atom.group(1)
    assert mapping and "migration_status] = 'VERIFIED'" in mapping.group(1)
    assert authority and atom_authority
    assert "atom.[migration_status] = 'VERIFIED'" in authority.group(1)
    assert "target_kind] = 'CONDITION'" in authority.group(1)
    assert "target_kind] = 'DICTIONARY'" in authority.group(1)
    assert "atom.[migration_status] = 'VERIFIED'" in atom_authority.group(1)
    assert "COUNT_BIG(*)" in atom_authority.group(1)
    assert "verification_evidence" in atom_authority.group(1)
    assert "target_kind] = 'CONDITION'" in atom_authority.group(1)
    assert "target_kind] = 'DICTIONARY'" in atom_authority.group(1)


def test_ddl_blocks_unresolved_typed_combination_targets() -> None:
    sql = DDL.read_text(encoding="utf-8")
    node_authority = re.search(
        r"CREATE OR ALTER TRIGGER \[kb\]\.\[tr_condition_node_target_authority\](.*?)\nGO",
        sql,
        re.S,
    )
    assert node_authority
    assert "[disposition]" not in node_authority.group(1)
    assert "[lifecycle]" not in node_authority.group(1)
    for target_kind in ("CONCEPT", "CLASS", "REGIMEN"):
        assert f"[target_kind] = '{target_kind}'" in node_authority.group(1)
    assert "r.[logical_regimen_id] = i.[target_id]" in node_authority.group(1)


def test_ddl_rechecks_publishable_target_authority_on_parent_approval() -> None:
    sql = DDL.read_text(encoding="utf-8")
    eligibility = re.search(
        r"CREATE OR ALTER TRIGGER \[kb\]\.\[tr_eligibility_revision_target_authority\](.*?)\nGO",
        sql,
        re.S,
    )
    regimen = re.search(
        r"CREATE OR ALTER TRIGGER \[kb\]\.\[tr_regimen_revision_target_authority\](.*?)\nGO",
        sql,
        re.S,
    )
    assert eligibility and regimen
    for block in (eligibility.group(1), regimen.group(1)):
        assert "IN ('APPROVED','RELEASED')" in block
        assert "NOT IN ('REJECTED','RETIRED')" in block
        assert not re.search(
            r"i\.\[lifecycle\]\s+IN\s+\('APPROVED','RELEASED','RETIRED'\)",
            block,
        )
    assert "n.[target_kind] NOT IN ('CONCEPT','CLASS','REGIMEN')" in eligibility.group(1)
    assert "c.[lifecycle] IN ('APPROVED','RELEASED')" in eligibility.group(1)
    assert "r.[lifecycle] IN ('APPROVED','RELEASED')" in eligibility.group(1)
    assert "dc.[lifecycle] IN ('APPROVED','RELEASED')" in regimen.group(1)


def test_condition_detail_view_resolves_logical_regimen_without_row_duplication() -> None:
    sql = DDL.read_text(encoding="utf-8")
    view = re.search(
        r"CREATE OR ALTER VIEW \[kb\]\.\[vw_eligibility_condition_detail\](.*?)\nGO",
        sql,
        re.S,
    )
    assert view
    block = view.group(1)
    assert "OUTER APPLY" in block
    assert "candidate.[logical_regimen_id] = node.[target_id]" in block
    assert "SELECT TOP (1) candidate.[canonical_name]" in block
    assert "target_regimen.[regimen_revision_id] = node.[target_id]" not in block


def test_ddl_is_additive_and_every_object_creation_is_idempotently_guarded() -> None:
    sql = DDL.read_text(encoding="utf-8")
    executable = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    assert not re.search(r"(?i)\b(?:DROP|TRUNCATE)\b", executable)
    assert not re.search(r"(?im)^\s*SELECT\b[^\n]*\bINTO\s+\[", executable)
    created_tables = set(re.findall(r"CREATE TABLE \[([^]]+)\]\.\[([^]]+)\]", executable))
    guarded_tables = set(re.findall(
        r"IF OBJECT_ID\(N'\[([^]]+)\]\.\[([^]]+)\]', N'U'\) IS NULL\s+BEGIN\s+CREATE TABLE",
        executable,
        re.S,
    ))
    assert created_tables and created_tables == guarded_tables
    assert executable.count("CREATE INDEX ") == executable.count(
        "IF NOT EXISTS (SELECT 1 FROM sys.indexes"
    )
    assert "CREATE TRIGGER " not in executable
    assert executable.count("CREATE OR ALTER TRIGGER ") >= 12


def test_server_validation_rechecks_references_and_draft_lifecycle() -> None:
    rows = build_staging_rows(parse_workbook(ELIGIBILITY, kind="eligibility"))
    assert validate_staging_rows(rows, kind="eligibility") == []
    branch = next(row for row in rows if row.sheet_name == "04_适应证分支")
    branch.canonical_payload["drug_concept_id"] = "missing"
    branch.canonical_payload["lifecycle"] = "APPROVED"
    codes = {item.safe_error_code for item in validate_staging_rows(rows, kind="eligibility")}
    assert codes == {"DRUG_CONCEPT_REFERENCE_MISSING", "UPLOAD_LIFECYCLE_NOT_DRAFT"}


class _UploadCursor:
    def __init__(self, existing=None, fail_on: str | None = None) -> None:
        self.existing = existing
        self.fail_on = fail_on
        self.executed = []
        self._last_sql = ""

    def execute(self, sql, *params):
        self._last_sql = sql
        self.executed.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("PWD=secret; /Users/name/private.xlsx; 原始病历")
        return self

    def fetchone(self):
        return self.existing if "SELECT import_batch_id" in self._last_sql else None


class _UploadConnection:
    def __init__(self, existing=None, fail_on: str | None = None) -> None:
        self.cursor_value = _UploadCursor(existing, fail_on)
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def test_staging_upload_is_parameterized_idempotent_and_path_safe() -> None:
    connection = _UploadConnection()
    result = upload_staging(connection, ELIGIBILITY, kind="eligibility", uploaded_by="operator-a")
    assert result.reused is False and result.row_count > 0 and result.status == "UPLOADED"
    assert connection.committed == 1 and connection.rolled_back == 0
    all_params = [value for _, params in connection.cursor_value.executed for value in params]
    assert str(ELIGIBILITY.parent) not in repr(all_params)
    assert ELIGIBILITY.name in all_params
    assert not any(isinstance(value, (bytes, bytearray)) for value in all_params)

    existing = _UploadConnection(existing=(result.import_batch_id, "VALIDATED", result.row_count))
    reused = upload_staging(existing, ELIGIBILITY, kind="eligibility", uploaded_by="operator-b")
    assert reused.reused is True and reused.import_batch_id == result.import_batch_id
    assert existing.committed == 0


def test_staging_upload_rolls_back_and_redacts_driver_error() -> None:
    connection = _UploadConnection(fail_on="INSERT INTO kb_stg.import_row")
    with pytest.raises(KnowledgeDatabaseError) as error:
        upload_staging(connection, ELIGIBILITY, kind="eligibility", uploaded_by="operator-a")
    assert connection.rolled_back == 1 and connection.committed == 0
    assert "secret" not in str(error.value) and "/Users/" not in str(error.value)


class _ServerValidationCursor:
    def __init__(
        self,
        rows,
        *,
        status: str = "UPLOADED",
        template_schema_version: str | None = "1.0.0",
    ) -> None:
        self.rows = rows
        self.status = status
        self.template_schema_version = template_schema_version
        self.executed = []
        self.result = []

    def execute(self, sql, *params):
        self.executed.append((sql, params))
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT") and "FROM kb_stg.import_batch" in normalized:
            counts = Counter(row.sheet_name for row in self.rows)
            self.result = [
                (
                    self.status,
                    self.template_schema_version,
                    len(self.rows),
                    json.dumps(counts, ensure_ascii=False, sort_keys=True),
                )
            ]
        elif normalized.startswith("SELECT") and "FROM kb_stg.import_row" in normalized:
            self.result = [
                (
                    row.sheet_name,
                    row.excel_row_no,
                    row.stable_row_id,
                    json.dumps(row.canonical_payload, ensure_ascii=False, sort_keys=True),
                    row.row_checksum,
                )
                for row in self.rows
            ]
        elif normalized.startswith("UPDATE kb_stg.import_row"):
            self.result = []
        elif normalized.startswith("UPDATE kb_stg.import_batch"):
            self.status = str(params[0])
            self.result = []
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")
        return self

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return list(self.result)


class _ServerValidationConnection:
    def __init__(self, rows, **kwargs) -> None:
        self.cursor_value = _ServerValidationCursor(rows, **kwargs)
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def test_server_validation_reads_canonical_payload_from_staging_and_reuses_review_contract() -> None:
    rows = copy.deepcopy(build_staging_rows(parse_workbook(
        ROOT / "outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx",
        kind="regimen",
    )))
    review = next(row for row in rows if row.sheet_name == "07_专家审核")
    review.canonical_payload.update(
        review_decision="APPROVE",
        reviewer_id="domain-reviewer",
        reviewed_at="2026-07-21T12:00:00Z",
        expert_comment="",
    )
    review.row_checksum = checksum(review.canonical_payload)
    connection = _ServerValidationConnection(rows)

    status, issues = mark_server_validation(connection, "batch-regimen", kind="regimen")

    assert status == "VALIDATION_FAILED"
    assert "REVIEW_FIELD_REQUIRED" in {issue.safe_error_code for issue in issues}
    assert any(
        "FROM kb_stg.import_row" in " ".join(sql.split())
        for sql, _ in connection.cursor_value.executed
    )


@pytest.mark.parametrize(
    ("replacement", "expected_code"),
    [
        (None, "REVIEW_SOURCE_CHECKSUM_INVALID"),
        ("sha256:" + "0" * 64, "REVIEW_CONTENT_CHECKSUM_MISMATCH"),
    ],
)
def test_server_validation_rejects_missing_or_tampered_review_checksum(
    replacement: str | None,
    expected_code: str,
) -> None:
    rows = copy.deepcopy(build_staging_rows(parse_workbook(
        ROOT / "outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx",
        kind="regimen",
    )))
    review = next(row for row in rows if row.sheet_name == "07_专家审核")
    if replacement is None:
        review.canonical_payload.pop("source_checksum")
    else:
        review.canonical_payload["source_checksum"] = replacement
    review.row_checksum = checksum(review.canonical_payload)
    connection = _ServerValidationConnection(rows)

    status, issues = mark_server_validation(
        connection,
        "batch-regimen-review-checksum",
        kind="regimen",
    )

    assert status == "VALIDATION_FAILED"
    assert expected_code in {issue.safe_error_code for issue in issues}


@pytest.mark.parametrize(
    ("workbook", "kind"),
    [
        (ELIGIBILITY, "eligibility"),
        (
            ROOT / "outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx",
            "regimen",
        ),
    ],
)
def test_server_validation_accepts_complete_staged_canonical_contract(
    workbook: Path,
    kind: str,
) -> None:
    rows = build_staging_rows(parse_workbook(workbook, kind=kind))
    connection = _ServerValidationConnection(rows)
    status, issues = mark_server_validation(connection, "batch-valid", kind=kind)
    assert status == "VALIDATED" and issues == []
    assert connection.committed == 1 and connection.rolled_back == 0


def test_server_validation_fails_closed_on_template_version_and_never_regresses_existing_batch() -> None:
    rows = build_staging_rows(parse_workbook(
        ROOT / "outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx",
        kind="regimen",
    ))
    missing = _ServerValidationConnection(rows, template_schema_version=None)
    status, issues = mark_server_validation(missing, "batch-missing-version", kind="regimen")
    assert status == "VALIDATION_FAILED"
    assert "TEMPLATE_VERSION_UNSUPPORTED" in {issue.safe_error_code for issue in issues}

    existing = _ServerValidationConnection(rows, status="VALIDATED")
    status, issues = mark_server_validation(existing, "batch-existing", kind="regimen")
    assert status == "VALIDATED" and issues == []
    assert not any(
        "UPDATE kb_stg" in " ".join(sql.split())
        for sql, _ in existing.cursor_value.executed
    )


class _PreflightCursor:
    def __init__(
        self,
        *,
        version: int | None,
        permissions=(1, 1, 1),
        trigger_count: int = len(_REQUIRED_SCHEMA_TRIGGERS),
        view_count: int = len(_REQUIRED_SCHEMA_VIEWS),
    ) -> None:
        self.version = version
        self.permissions = permissions
        self.trigger_count = trigger_count
        self.view_count = view_count
        self.executed = []
        self.result = []

    def execute(self, sql, *params):
        self.executed.append((sql, params))
        normalized = " ".join(sql.split())
        if normalized == "SELECT DB_NAME()":
            self.result = [(KNOWLEDGE_DATABASE,)]
        elif normalized == "SELECT SUSER_SNAME()":
            self.result = [("principal-should-stay-private",)]
        elif "HAS_PERMS_BY_NAME" in normalized:
            self.result = [self.permissions]
        elif "OBJECT_ID" in normalized:
            self.result = [(1,)]
        elif "MAX(version_no)" in normalized:
            self.result = [(self.version,)]
        elif "sys.triggers" in normalized:
            self.result = [(self.trigger_count,)]
        elif "sys.views" in normalized:
            self.result = [(self.view_count,)]
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")
        return self

    def fetchone(self):
        return self.result[0]


class _PreflightConnection:
    def __init__(self, **kwargs) -> None:
        self.cursor_value = _PreflightCursor(**kwargs)

    def cursor(self):
        return self.cursor_value


@pytest.mark.parametrize("version", [None, 2])
def test_preflight_requires_exact_schema_version(monkeypatch, version) -> None:
    monkeypatch.setenv("JAVERT_OWNED_DBS", KNOWLEDGE_DATABASE)
    with pytest.raises(KnowledgeDatabaseError, match="schema version"):
        preflight_connection(_PreflightConnection(version=version), KNOWLEDGE_DATABASE)


def test_preflight_checks_required_select_insert_update_permissions(monkeypatch) -> None:
    monkeypatch.setenv("JAVERT_OWNED_DBS", KNOWLEDGE_DATABASE)
    denied = _PreflightConnection(version=1, permissions=(1, 0, 1))
    with pytest.raises(KnowledgeDatabaseError, match="权限"):
        preflight_connection(denied, KNOWLEDGE_DATABASE)
    permission_sql = next(
        sql for sql, _ in denied.cursor_value.executed if "HAS_PERMS_BY_NAME" in sql
    )
    assert all(permission in permission_sql for permission in ("SELECT", "INSERT", "UPDATE"))

    allowed = _PreflightConnection(version=1)
    result = preflight_connection(allowed, KNOWLEDGE_DATABASE)
    assert result.ready
    assert "principal-should-stay-private" not in repr(result)


def test_preflight_rejects_version_marker_without_complete_trigger_capability(monkeypatch) -> None:
    monkeypatch.setenv("JAVERT_OWNED_DBS", KNOWLEDGE_DATABASE)
    incomplete = _PreflightConnection(version=1, trigger_count=13)
    with pytest.raises(KnowledgeDatabaseError, match="schema capability"):
        preflight_connection(incomplete, KNOWLEDGE_DATABASE)
    trigger_sql, trigger_params = next(
        (sql, params)
        for sql, params in incomplete.cursor_value.executed
        if "sys.triggers" in sql
    )
    assert "installed.is_disabled = 0" in trigger_sql
    assert "parent_schema.name = N'kb'" in trigger_sql
    assert "parent_table.name = required.parent_table" in trigger_sql
    preflight_capabilities = set(zip(trigger_params[::2], trigger_params[1::2]))
    ddl_capabilities = set(re.findall(
        r"\(N'(tr_[^']+)', N'([^']+)'\)",
        DDL.read_text(encoding="utf-8").split(
            "AS required([trigger_name], [parent_table])", 1
        )[0].rsplit("FROM (VALUES", 1)[1],
    ))
    assert preflight_capabilities == ddl_capabilities


def test_preflight_rejects_version_marker_without_human_readable_views(monkeypatch) -> None:
    monkeypatch.setenv("JAVERT_OWNED_DBS", KNOWLEDGE_DATABASE)
    incomplete = _PreflightConnection(version=1, view_count=len(_REQUIRED_SCHEMA_VIEWS) - 1)
    with pytest.raises(KnowledgeDatabaseError, match="视图"):
        preflight_connection(incomplete, KNOWLEDGE_DATABASE)
    view_sql, view_params = next(
        (sql, params)
        for sql, params in incomplete.cursor_value.executed
        if "sys.views" in sql
    )
    assert "view_schema.name = N'kb'" in view_sql
    assert set(view_params) == set(_REQUIRED_SCHEMA_VIEWS)


class _Cursor:
    def __init__(self, fail_on: str) -> None:
        self.fail_on = fail_on
        self.executed = []

    def execute(self, sql, *params):
        self.executed.append((sql, params))
        if self.fail_on in sql:
            raise RuntimeError("PWD=secret; original workbook text")
        return self


class _Connection:
    def __init__(self, fail_on: str) -> None:
        self.cursor_value = _Cursor(fail_on)
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def test_transaction_failure_rolls_back_and_redacts_exception() -> None:
    connection = _Connection("bad")
    with pytest.raises(KnowledgeDatabaseError, match="RuntimeError") as error:
        apply_transaction(connection, [("good", (1,)), ("bad", (2,))])
    assert connection.committed == 0 and connection.rolled_back == 1
    assert "secret" not in str(error.value)
    safe = safe_database_error(RuntimeError("PWD=secret /Users/name/private.xlsx"), database=KNOWLEDGE_DATABASE)
    assert "secret" not in safe and "/Users/" not in safe
