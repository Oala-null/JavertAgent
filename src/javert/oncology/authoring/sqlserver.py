"""知识库 SQL Server 预检、迁移边界与去敏错误。"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from javert.store.write_safety import connect_owned_database, validate_owned_database

from .ids import canonical_json_bytes, checksum, stable_id
from .regimens import regimen_revision_typed_values
from .staging import (
    StagingRow,
    build_staging_rows,
    safe_basename,
    workbook_sha256,
)
from . import workbooks as workbook_contract
from .workbooks import (
    ELIGIBILITY_SHEETS,
    EXPERT_COLUMNS,
    REGIMEN_SHEETS,
    REQUIRED_COLUMNS,
    parse_workbook,
    structured_expert_value,
    validate_workbook,
)


KNOWLEDGE_DATABASE = "知识库_work"
SCHEMA_VERSION = 1
TEMPLATE_SCHEMA_VERSION = "1.0.0"
_REQUIRED_SCHEMA_TRIGGERS = (
    ("tr_review_event_append_only", "review_event"),
    ("tr_release_immutable", "release_item"),
    ("tr_eligibility_revision_no_overlap", "eligibility_rule_revision"),
    ("tr_regimen_revision_no_overlap", "regimen_revision"),
    ("tr_eligibility_revision_immutable", "eligibility_rule_revision"),
    ("tr_regimen_revision_immutable", "regimen_revision"),
    ("tr_eligibility_branch_parent_immutable", "eligibility_branch"),
    ("tr_condition_node_parent_immutable", "condition_node"),
    ("tr_regimen_alias_parent_immutable", "regimen_alias"),
    ("tr_regimen_context_parent_immutable", "regimen_context"),
    ("tr_regimen_component_parent_immutable", "regimen_component"),
    ("tr_regimen_component_target_authority", "regimen_component"),
    ("tr_condition_node_target_authority", "condition_node"),
    ("tr_eligibility_revision_target_authority", "eligibility_rule_revision"),
    ("tr_regimen_revision_target_authority", "regimen_revision"),
    ("tr_regimen_schedule_parent_immutable", "regimen_schedule_component"),
    ("tr_source_fragment_frozen_reference", "source_fragment"),
    ("tr_drug_concept_frozen_reference", "drug_concept"),
    ("tr_drug_class_frozen_reference", "drug_class"),
    ("tr_curated_atom_verified_immutable", "curated_knowledge_atom"),
    ("tr_curated_mapping_verified_immutable", "curated_knowledge_mapping"),
    ("tr_curated_mapping_target_authority", "curated_knowledge_mapping"),
    ("tr_curated_atom_target_authority", "curated_knowledge_atom"),
)
_REQUIRED_SCHEMA_VIEWS = (
    "vw_eligibility_rule_overview",
    "vw_eligibility_condition_detail",
    "vw_regimen_composition",
    "vw_latest_review_status",
    "vw_import_reconciliation",
    "vw_release_overview",
)


class KnowledgeDatabaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class StagingUploadResult:
    import_batch_id: str
    reused: bool
    row_count: int
    status: str


@dataclass
class ReconciliationCount:
    staged: int = 0
    inserted: int = 0
    reused: int = 0
    updated_draft: int = 0
    rejected: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "staged": self.staged,
            "inserted": self.inserted,
            "reused": self.reused,
            "updated_draft": self.updated_draft,
            "rejected": self.rejected,
        }

    @property
    def accounted(self) -> int:
        return self.inserted + self.reused + self.updated_draft + self.rejected


@dataclass(frozen=True)
class MaterializationResult:
    import_batch_id: str
    kind: str
    status: str
    staging_row_count: int
    projected_entity_count: int
    reconciliation: dict[str, ReconciliationCount]

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_batch_id": self.import_batch_id,
            "kind": self.kind,
            "status": self.status,
            "staging_row_count": self.staging_row_count,
            "projected_entity_count": self.projected_entity_count,
            "reconciliation": {
                entity: counts.as_dict()
                for entity, counts in sorted(self.reconciliation.items())
            },
        }


@dataclass(frozen=True, order=True)
class ServerValidationIssue:
    sheet_name: str
    excel_row_no: int
    safe_error_code: str


@dataclass(frozen=True)
class KnowledgePreflight:
    requested_database: str
    connected_database: str
    principal: str = field(repr=False)
    can_select: bool
    can_insert: bool
    can_update: bool
    schema_version: int | None

    @property
    def ready(self) -> bool:
        return (
            self.requested_database == self.connected_database
            and self.can_select
            and self.can_insert
            and self.can_update
            and self.schema_version == SCHEMA_VERSION
        )


def connect_knowledge_database(
    database: str,
    connector: Callable[[str], Any],
    *,
    owned_raw: str | None = None,
) -> Any:
    return connect_owned_database(
        database,
        connector,
        required_exact=KNOWLEDGE_DATABASE,
        owned_raw=owned_raw,
    )


def preflight_connection(connection: Any, database: str) -> KnowledgePreflight:
    validate_owned_database(database, required_exact=KNOWLEDGE_DATABASE)
    cursor = connection.cursor()
    connected = str(cursor.execute("SELECT DB_NAME()").fetchone()[0])
    if connected != database:
        raise KnowledgeDatabaseError("连接目标与请求数据库不一致；已在第一条 DDL/DML 前中止")
    principal = str(cursor.execute("SELECT SUSER_SNAME()").fetchone()[0])
    permissions = cursor.execute(
        "SELECT "
        "CASE WHEN "
        "HAS_PERMS_BY_NAME(N'kb_meta', N'SCHEMA', N'SELECT') = 1 AND "
        "HAS_PERMS_BY_NAME(N'kb_stg', N'SCHEMA', N'SELECT') = 1 AND "
        "HAS_PERMS_BY_NAME(N'kb', N'SCHEMA', N'SELECT') = 1 THEN 1 ELSE 0 END, "
        "CASE WHEN "
        "HAS_PERMS_BY_NAME(N'kb_stg', N'SCHEMA', N'INSERT') = 1 AND "
        "HAS_PERMS_BY_NAME(N'kb', N'SCHEMA', N'INSERT') = 1 THEN 1 ELSE 0 END, "
        "CASE WHEN "
        "HAS_PERMS_BY_NAME(N'kb_stg', N'SCHEMA', N'UPDATE') = 1 AND "
        "HAS_PERMS_BY_NAME(N'kb', N'SCHEMA', N'UPDATE') = 1 THEN 1 ELSE 0 END"
    ).fetchone()
    can_select, can_insert, can_update = map(bool, permissions)
    if not (can_select and can_insert and can_update):
        raise KnowledgeDatabaseError("知识库 SELECT/INSERT/UPDATE 权限预检失败")
    has_version_table = bool(
        cursor.execute("SELECT CASE WHEN OBJECT_ID(N'kb_meta.schema_version', N'U') IS NULL THEN 0 ELSE 1 END")
        .fetchone()[0]
    )
    version_row = (
        cursor.execute("SELECT MAX(version_no) FROM kb_meta.schema_version").fetchone()
        if has_version_table and can_select
        else None
    )
    schema_version = (
        None if version_row is None or version_row[0] is None else int(version_row[0])
    )
    if schema_version != SCHEMA_VERSION:
        raise KnowledgeDatabaseError("知识库 schema version 缺失或不匹配")
    trigger_values = ", ".join("(?, ?)" for _ in _REQUIRED_SCHEMA_TRIGGERS)
    trigger_params = tuple(
        value
        for trigger_name, parent_table in _REQUIRED_SCHEMA_TRIGGERS
        for value in (trigger_name, parent_table)
    )
    installed_trigger_count = int(
        cursor.execute(
            "SELECT COUNT(*) "
            f"FROM (VALUES {trigger_values}) AS required(trigger_name, parent_table) "
            "JOIN sys.triggers AS installed "
            "ON installed.name = required.trigger_name AND installed.is_disabled = 0 "
            "JOIN sys.tables AS parent_table "
            "ON parent_table.object_id = installed.parent_id "
            "AND parent_table.name = required.parent_table "
            "JOIN sys.schemas AS parent_schema "
            "ON parent_schema.schema_id = parent_table.schema_id "
            "AND parent_schema.name = N'kb'",
            *trigger_params,
        ).fetchone()[0]
    )
    if installed_trigger_count != len(_REQUIRED_SCHEMA_TRIGGERS):
        raise KnowledgeDatabaseError("知识库 schema capability 不完整：必需触发器缺失或未启用")
    view_values = ", ".join("(?)" for _ in _REQUIRED_SCHEMA_VIEWS)
    installed_view_count = int(
        cursor.execute(
            "SELECT COUNT(*) "
            f"FROM (VALUES {view_values}) AS required(view_name) "
            "JOIN sys.views AS installed ON installed.name = required.view_name "
            "JOIN sys.schemas AS view_schema ON view_schema.schema_id = installed.schema_id "
            "AND view_schema.name = N'kb'",
            *_REQUIRED_SCHEMA_VIEWS,
        ).fetchone()[0]
    )
    if installed_view_count != len(_REQUIRED_SCHEMA_VIEWS):
        raise KnowledgeDatabaseError("知识库 schema capability 不完整：必需人工审核视图缺失")
    result = KnowledgePreflight(
        requested_database=database,
        connected_database=connected,
        principal=principal,
        can_select=can_select,
        can_insert=can_insert,
        can_update=can_update,
        schema_version=schema_version,
    )
    return result


def validate_staging_rows(rows: list[StagingRow], *, kind: str) -> list[ServerValidationIssue]:
    """在服务端 materialize 前重做引用/状态/日期检查，不信任客户端校验结果。"""
    by_sheet: dict[str, list[StagingRow]] = {}
    for row in rows:
        by_sheet.setdefault(row.sheet_name, []).append(row)
    issues: list[ServerValidationIssue] = []

    def ids(sheet: str, key: str) -> set[str]:
        return {str(row.canonical_payload.get(key) or "") for row in by_sheet.get(sheet, [])}

    def issue(row: StagingRow, code: str) -> None:
        issues.append(ServerValidationIssue(row.sheet_name, row.excel_row_no, code))

    if kind == "eligibility":
        concept_ids = ids("02_药品产品", "drug_concept_id")
        source_ids = ids("03_来源原文", "source_fragment_id")
        branches = ids("04_适应证分支", "branch_id")
        nodes = ids("05_条件节点", "node_id")
        for row in by_sheet.get("04_适应证分支", []):
            payload = row.canonical_payload
            if str(payload.get("drug_concept_id") or "") not in concept_ids:
                issue(row, "DRUG_CONCEPT_REFERENCE_MISSING")
            if str(payload.get("source_fragment_id") or "") not in source_ids:
                issue(row, "SOURCE_FRAGMENT_REFERENCE_MISSING")
            if str(payload.get("lifecycle") or "") not in {"DRAFT", "IN_REVIEW", "CHANGES_REQUESTED"}:
                issue(row, "UPLOAD_LIFECYCLE_NOT_DRAFT")
            if str(payload.get("effective_from") or "") > str(payload.get("effective_to") or ""):
                issue(row, "EFFECTIVE_WINDOW_INVALID")
        for row in by_sheet.get("05_条件节点", []):
            payload = row.canonical_payload
            if str(payload.get("branch_id") or "") not in branches:
                issue(row, "BRANCH_REFERENCE_MISSING")
            parent = str(payload.get("parent_node_id") or "")
            if parent and parent not in nodes:
                issue(row, "PARENT_NODE_REFERENCE_MISSING")
            if str(payload.get("source_fragment_id") or "") not in source_ids:
                issue(row, "SOURCE_FRAGMENT_REFERENCE_MISSING")
    elif kind == "regimen":
        revisions = ids("02_方案主表", "regimen_revision_id")
        components = ids("05_方案组分", "component_id")
        for sheet in ("03_方案别名", "04_方案上下文", "05_方案组分", "06_预留给药字段"):
            for row in by_sheet.get(sheet, []):
                revision = str(row.canonical_payload.get("regimen_revision_id") or "")
                if revision and revision not in revisions:
                    issue(row, "REGIMEN_REVISION_REFERENCE_MISSING")
        for row in by_sheet.get("06_预留给药字段", []):
            if str(row.canonical_payload.get("component_id") or "") not in components:
                issue(row, "REGIMEN_COMPONENT_REFERENCE_MISSING")
            if bool(row.canonical_payload.get("publishing_enabled")) or bool(
                row.canonical_payload.get("inference_enabled")
            ):
                issue(row, "SCHEDULE_PHASE1_MUST_BE_DISABLED")
    else:
        raise ValueError(f"未知 workbook kind: {kind}")
    return sorted(set(issues))


def upload_staging(
    connection: Any,
    path: Path,
    *,
    kind: str,
    uploaded_by: str,
) -> StagingUploadResult:
    """幂等写入 workbook 元数据和规范化逐行 payload；不保存二进制或绝对路径。"""
    local_issues = validate_workbook(path, kind=kind)
    if local_issues:
        raise KnowledgeDatabaseError(f"workbook validation failed: errors={len(local_issues)}")
    parsed = parse_workbook(path, kind=kind)
    rows = build_staging_rows(parsed)
    digest = workbook_sha256(path)
    version = str(parsed["metadata"].get("template_schema_version") or "")
    if version != TEMPLATE_SCHEMA_VERSION:
        raise KnowledgeDatabaseError("workbook template schema version 缺失或不匹配")
    expected_counts = {sheet: len(values) for sheet, values in parsed["sheets"].items()}
    from .ids import import_batch_id

    batch_id = import_batch_id(digest, version)
    cursor = connection.cursor()
    try:
        existing = cursor.execute(
            "SELECT import_batch_id, status, expected_row_count FROM kb_stg.import_batch "
            "WHERE workbook_sha256 = ? AND template_schema_version = ?",
            digest,
            version,
        ).fetchone()
        if existing is not None:
            return StagingUploadResult(str(existing[0]), True, int(existing[2]), str(existing[1]))
        cursor.execute(
            "INSERT INTO kb_stg.import_batch "
            "(import_batch_id, workbook_sha256, template_schema_version, safe_basename, uploaded_by, "
            "status, expected_row_count, expected_counts_json) VALUES (?, ?, ?, ?, ?, 'UPLOADED', ?, ?)",
            batch_id,
            digest,
            version,
            safe_basename(path),
            uploaded_by,
            len(rows),
            json.dumps(expected_counts, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        for row in rows:
            cursor.execute(
                "INSERT INTO kb_stg.import_row "
                "(import_batch_id, sheet_name, excel_row_no, stable_row_id, canonical_payload, "
                "row_checksum, validation_status) VALUES (?, ?, ?, ?, ?, ?, 'PENDING')",
                batch_id,
                row.sheet_name,
                row.excel_row_no,
                row.stable_row_id,
                canonical_json_bytes(row.canonical_payload).decode("utf-8"),
                row.row_checksum,
            )
        connection.commit()
    except Exception as exc:
        connection.rollback()
        raise KnowledgeDatabaseError(f"staging upload failed: {type(exc).__name__}") from None
    return StagingUploadResult(batch_id, False, len(rows), "UPLOADED")


def _staging_contract_issues(
    rows: list[StagingRow],
    *,
    kind: str,
    template_schema_version: str,
    expected_row_count: int,
    expected_counts: Mapping[str, int],
) -> list[ServerValidationIssue]:
    """把 DB canonical payload 投影回工作簿合同并复用同一组语义校验器。"""
    expected_sheets = ELIGIBILITY_SHEETS if kind == "eligibility" else REGIMEN_SHEETS
    data_sheets = expected_sheets[2:]
    by_sheet: dict[str, list[StagingRow]] = {sheet: [] for sheet in data_sheets}
    issues: list[ServerValidationIssue] = []
    for row in rows:
        if row.sheet_name not in by_sheet:
            issues.append(
                ServerValidationIssue(row.sheet_name, row.excel_row_no, "SHEET_UNEXPECTED")
            )
            continue
        by_sheet[row.sheet_name].append(row)
        try:
            _verify_staging_row_checksum(row)
        except KnowledgeDatabaseError:
            issues.append(
                ServerValidationIssue(
                    row.sheet_name,
                    row.excel_row_no,
                    "ROW_CHECKSUM_MISMATCH",
                )
            )

    if template_schema_version != TEMPLATE_SCHEMA_VERSION:
        issues.append(
            ServerValidationIssue(
                "01_批次元数据", 0, "TEMPLATE_VERSION_UNSUPPORTED"
            )
        )
    if len(rows) != expected_row_count:
        issues.append(
            ServerValidationIssue("01_批次元数据", 0, "EXPECTED_ROW_COUNT_MISMATCH")
        )
    actual_counts = Counter(row.sheet_name for row in rows)
    for sheet in data_sheets:
        if sheet not in expected_counts:
            issues.append(ServerValidationIssue(sheet, 0, "EXPECTED_SHEET_MISSING"))
        elif actual_counts.get(sheet, 0) != int(expected_counts[sheet]):
            issues.append(ServerValidationIssue(sheet, 0, "EXPECTED_COUNT_MISMATCH"))
    for sheet in sorted(set(expected_counts) - set(data_sheets)):
        issues.append(ServerValidationIssue(sheet, 0, "EXPECTED_SHEET_UNEXPECTED"))

    missing_columns = False
    for sheet, required in REQUIRED_COLUMNS[kind].items():
        for row in by_sheet.get(sheet, []):
            if not required.issubset(row.canonical_payload):
                missing_columns = True
                issues.append(
                    ServerValidationIssue(sheet, row.excel_row_no, "COLUMN_MISSING")
                )
    issues.extend(_review_staging_binding_issues(rows, kind=kind))

    parsed = {
        "kind": kind,
        "metadata": {"template_schema_version": template_schema_version},
        "sheets": {
            sheet: [
                {"_excel_row": row.excel_row_no, **row.canonical_payload}
                for row in by_sheet[sheet]
            ]
            for sheet in data_sheets
        },
    }
    safe_path = Path("staged-authoring-workbook.xlsx")
    for sheet, sheet_rows in parsed["sheets"].items():
        client_issues = [
            *workbook_contract._review_issues(safe_path, sheet, sheet_rows),
            *workbook_contract._privacy_issues(safe_path, sheet, sheet_rows),
        ]
        issues.extend(
            ServerValidationIssue(item.sheet, item.excel_row, item.error_code)
            for item in client_issues
        )
    if not missing_columns:
        client_issues = (
            workbook_contract._eligibility_issues(safe_path, parsed)
            if kind == "eligibility"
            else workbook_contract._regimen_issues(safe_path, parsed)
        )
        issues.extend(
            ServerValidationIssue(item.sheet, item.excel_row, item.error_code)
            for item in client_issues
        )
        issues.extend(validate_staging_rows(rows, kind=kind))
    return sorted(set(issues))


def mark_server_validation(
    connection: Any,
    batch_id: str,
    *,
    kind: str,
) -> tuple[str, list[ServerValidationIssue]]:
    """仅从 staging 回读 canonical payload 后校验；既有批次状态永不回退。"""
    if kind not in {"eligibility", "regimen"}:
        raise ValueError(f"未知 workbook kind: {kind}")
    cursor = connection.cursor()
    try:
        batch = cursor.execute(
            "SELECT status, template_schema_version, expected_row_count, expected_counts_json "
            "FROM kb_stg.import_batch WHERE import_batch_id = ?",
            batch_id,
        ).fetchone()
        if batch is None:
            raise KnowledgeDatabaseError("import batch 不存在")
        existing_status = str(batch[0])
        if existing_status != "UPLOADED":
            return existing_status, []
        template_schema_version = str(batch[1] or "")
        expected_row_count = int(batch[2])
        try:
            decoded_counts = json.loads(str(batch[3] or ""))
            if not isinstance(decoded_counts, dict):
                raise ValueError
            expected_counts = {
                str(sheet): int(count) for sheet, count in decoded_counts.items()
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            expected_counts = {}
        staged = cursor.execute(
            "SELECT sheet_name, excel_row_no, stable_row_id, canonical_payload, row_checksum "
            "FROM kb_stg.import_row WHERE import_batch_id = ? "
            "ORDER BY sheet_name, excel_row_no",
            batch_id,
        ).fetchall()
        rows: list[StagingRow] = []
        parse_issues: list[ServerValidationIssue] = []
        for item in staged:
            try:
                payload = json.loads(str(item[3]))
                if not isinstance(payload, dict):
                    raise ValueError
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
                parse_issues.append(
                    ServerValidationIssue(str(item[0]), int(item[1]), "CANONICAL_PAYLOAD_INVALID")
                )
            rows.append(
                StagingRow(
                    sheet_name=str(item[0]),
                    excel_row_no=int(item[1]),
                    stable_row_id=str(item[2]),
                    canonical_payload=payload,
                    row_checksum=str(item[4]),
                )
            )
        issues = [
            *parse_issues,
            *_staging_contract_issues(
                rows,
                kind=kind,
                template_schema_version=template_schema_version,
                expected_row_count=expected_row_count,
                expected_counts=expected_counts,
            ),
        ]
        issues = sorted(set(issues))
        by_row: dict[tuple[str, int], str] = {}
        for item in issues:
            by_row.setdefault((item.sheet_name, item.excel_row_no), item.safe_error_code)
        for row in rows:
            error = by_row.get((row.sheet_name, row.excel_row_no))
            cursor.execute(
                "UPDATE kb_stg.import_row SET validation_status = ?, safe_error_code = ? "
                "WHERE import_batch_id = ? AND sheet_name = ? AND excel_row_no = ?",
                "INVALID" if error else "VALID",
                error,
                batch_id,
                row.sheet_name,
                row.excel_row_no,
            )
        status = "VALIDATION_FAILED" if issues else "VALIDATED"
        cursor.execute(
            "UPDATE kb_stg.import_batch SET status = ?, error_summary = ? WHERE import_batch_id = ?",
            status,
            None if not issues else f"server_validation_errors={len(issues)}",
            batch_id,
        )
        connection.commit()
    except Exception as exc:
        connection.rollback()
        raise KnowledgeDatabaseError(f"server validation failed: {type(exc).__name__}") from None
    return status, issues


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MUTABLE_LIFECYCLES = frozenset({"DRAFT", "CHANGES_REQUESTED"})
_AUTHORING_LIFECYCLES = frozenset({"DRAFT", "IN_REVIEW", "CHANGES_REQUESTED"})
_REVIEW_ENTITY_LOCATORS = {
    "drug_product": ("kb.drug_product", "drug_product_id"),
    "drug_class": ("kb.drug_class", "drug_class_id"),
    "source_fragment": ("kb.source_fragment", "source_fragment_id"),
    "branch": ("kb.eligibility_branch", "branch_id"),
    "condition_node": ("kb.condition_node", "node_id"),
    "eligibility_revision": ("kb.eligibility_rule_revision", "rule_revision_id"),
    "curated_knowledge_atom": ("kb.curated_knowledge_atom", "atom_id"),
    "regimen": ("kb.regimen_revision", "regimen_revision_id"),
    "alias": ("kb.regimen_alias", "alias_id"),
    "context": ("kb.regimen_context", "context_id"),
    "component": ("kb.regimen_component", "component_id"),
    "schedule_component": ("kb.regimen_schedule_component", "schedule_component_id"),
}
_REVIEW_ENTITY_BY_TABLE = {
    table: entity_type
    for entity_type, (table, _key_column) in _REVIEW_ENTITY_LOCATORS.items()
}


@dataclass(frozen=True)
class _Guard:
    table: str
    key_column: str
    key_value: Any
    state_column: str
    mutable_states: frozenset[str]


@dataclass(frozen=True)
class _TypedRecord:
    entity_type: str
    table: str | None
    key_column: str
    values: Mapping[str, Any]
    checksum_column: str = "content_checksum"
    state_column: str | None = None
    mutable_states: frozenset[str] = _MUTABLE_LIFECYCLES
    guard: _Guard | None = None
    append_only: bool = False
    no_op: bool = False
    after_review: bool = False
    expected_content_checksum: str | None = None
    projection_rank: int = 0

    @property
    def key_value(self) -> Any:
        return self.values[self.key_column]


def _quoted_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"非法 SQL 标识符: {value!r}")
    return f"[{value}]"


def _quoted_table(value: str) -> str:
    schema, table = value.split(".", 1)
    return f"{_quoted_identifier(schema)}.{_quoted_identifier(table)}"


def _none_if_blank(value: Any) -> Any:
    return None if value in (None, "") else value


def _int_if_present(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


def _decimal_if_present(value: Any) -> Any:
    return None if value in (None, "") else value


def _as_bit(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _json_text(value: Any, *, blank_is_null: bool = True) -> str | None:
    if value in (None, "") and blank_is_null:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
    else:
        parsed = value
    return canonical_json_bytes(parsed).decode("utf-8")


def _typed_checksum(values: Mapping[str, Any], *excluded: str) -> str:
    omitted = {"content_checksum", *excluded}
    return checksum({key: value for key, value in values.items() if key not in omitted})


def _authoring_lifecycle(value: Any) -> str:
    lifecycle = str(value or "DRAFT")
    return lifecycle if lifecycle in _AUTHORING_LIFECYCLES else "DRAFT"


def _verify_staging_row_checksum(row: StagingRow) -> None:
    payload = row.canonical_payload
    embedded = str(payload.get("row_checksum") or "")
    if embedded:
        machine = {
            key: value
            for key, value in payload.items()
            if key not in EXPERT_COLUMNS and key != "row_checksum"
        }
        actual = checksum(machine)
        expected = embedded
    else:
        actual = checksum(payload)
        expected = row.row_checksum
    if expected != row.row_checksum or actual != expected:
        raise KnowledgeDatabaseError(
            f"staging row checksum mismatch: sheet={row.sheet_name!r} row={row.excel_row_no}"
        )


def _record(
    entity_type: str,
    table: str,
    key_column: str,
    values: dict[str, Any],
    *,
    checksum_column: str = "content_checksum",
    state_column: str | None = None,
    mutable_states: frozenset[str] = _MUTABLE_LIFECYCLES,
    guard: _Guard | None = None,
    append_only: bool = False,
    after_review: bool = False,
    expected_content_checksum: str | None = None,
    projection_rank: int = 0,
) -> _TypedRecord:
    return _TypedRecord(
        entity_type=entity_type,
        table=table,
        key_column=key_column,
        values=values,
        checksum_column=checksum_column,
        state_column=state_column,
        mutable_states=mutable_states,
        guard=guard,
        append_only=append_only,
        after_review=after_review,
        expected_content_checksum=expected_content_checksum,
        projection_rank=projection_rank,
    )


def _review_record(
    row: StagingRow,
    *,
    fallback_type: str,
    fallback_id: str,
    current_content_checksum: str | None = None,
) -> _TypedRecord | None:
    payload = row.canonical_payload
    decision = str(payload.get("review_decision") or "").strip()
    if not decision:
        return None
    if decision not in {"APPROVE", "APPROVE_WITH_EDIT", "REJECT", "UNABLE_TO_DETERMINE"}:
        raise KnowledgeDatabaseError("review decision 非法")
    entity_type = str(payload.get("entity_type") or fallback_type)
    entity_id = str(payload.get("entity_id") or fallback_id)
    field_name = str(payload.get("field_name") or "row")
    source_checksum = str(current_content_checksum or payload.get("source_checksum") or "")
    if not _SHA256_RE.fullmatch(source_checksum):
        raise KnowledgeDatabaseError("review source checksum 缺失或格式非法")
    if current_content_checksum and source_checksum != current_content_checksum:
        raise KnowledgeDatabaseError("review source checksum 与当前实体内容不一致")
    try:
        expert_patch = structured_expert_value(payload)
    except ValueError as exc:
        raise KnowledgeDatabaseError("结构化 expert_value 非法") from exc
    expert_value_json = _json_text(expert_patch) if expert_patch else None
    event_values = {
        "review_event_id": stable_id(
            "review",
            entity_type,
            entity_id,
            field_name,
            source_checksum,
            decision,
            expert_value_json,
            payload.get("expert_comment"),
            payload.get("evidence_reference"),
            payload.get("reviewer_id"),
            payload.get("reviewed_at"),
        ),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "field_name": field_name,
        "decision": decision,
        "expert_value_json": expert_value_json,
        "comment": str(payload.get("expert_comment") or ""),
        "evidence_reference": _none_if_blank(payload.get("evidence_reference")),
        "reviewer_id": str(payload.get("reviewer_id") or ""),
        "reviewed_at": str(payload.get("reviewed_at") or ""),
        "reviewed_content_checksum": source_checksum,
        "previous_event_id": None,
    }
    return _record(
        "review_event",
        "kb.review_event",
        "review_event_id",
        event_values,
        checksum_column="",
        append_only=True,
    )


def _projected_content_checksum(
    records: Sequence[_TypedRecord],
    *,
    entity_type: str,
    entity_id: str,
) -> str | None:
    locator = _REVIEW_ENTITY_LOCATORS.get(entity_type)
    if locator is None:
        return None
    table, _ = locator
    for record in records:
        if (
            record.table == table
            and str(record.key_value) == entity_id
            and record.checksum_column
        ):
            return str(record.values[record.checksum_column])
    return None


def _event_patch(event: _TypedRecord) -> dict[str, Any]:
    raw = event.values.get("expert_value_json")
    if raw in (None, ""):
        return {}
    try:
        decoded = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise KnowledgeDatabaseError("review expert_value_json 非法") from exc
    if not isinstance(decoded, dict):
        raise KnowledgeDatabaseError("review expert_value_json 必须为 object")
    return decoded


def _review_projection(
    target: _TypedRecord,
    values: Mapping[str, Any],
    *,
    reviewed_checksum: str,
    rank: int = 100,
) -> _TypedRecord:
    return replace(
        target,
        values=dict(values),
        append_only=False,
        no_op=False,
        after_review=True,
        expected_content_checksum=reviewed_checksum,
        projection_rank=rank,
    )


def _content_checksum_after_edit(table: str, values: Mapping[str, Any]) -> str:
    excluded = {
        "kb.eligibility_rule_revision": ("lifecycle",),
        "kb.eligibility_branch": ("disposition",),
        "kb.condition_node": ("disposition",),
        "kb.regimen_revision": ("lifecycle",),
        "kb.regimen_alias": ("review_status",),
        "kb.drug_class": ("lifecycle",),
        "kb.curated_knowledge_atom": ("migration_status",),
    }.get(table, ())
    return _typed_checksum(values, *excluded)


def _validate_effective_edit(values: Mapping[str, Any]) -> None:
    try:
        effective_from = str(values["effective_from"])
        effective_to = str(values["effective_to"])
        if effective_from > effective_to:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise KnowledgeDatabaseError("expert effective window 非法") from exc
    if (
        str(values.get("effective_date_basis") or "") != "EXPERT_OVERRIDE"
        or not str(values.get("date_override_reason") or "").strip()
        or not str(values.get("date_review_comment") or "").strip()
    ):
        raise KnowledgeDatabaseError("expert effective override 信息不完整")


def _edited_target_projection(
    target: _TypedRecord,
    *,
    entity_type: str,
    patch: Mapping[str, Any],
    reviewed_checksum: str,
) -> _TypedRecord:
    values = dict(target.values)
    if entity_type in {"eligibility_revision", "regimen"}:
        if str(values.get("lifecycle") or "") not in _MUTABLE_LIFECYCLES:
            raise KnowledgeDatabaseError("immutable revision edit rejected")
        for field, value in patch.items():
            values[field] = str(value)
        if set(patch) & {
            "effective_from", "effective_to", "effective_date_basis",
            "date_override_reason", "date_review_comment",
        }:
            _validate_effective_edit(values)
        values["lifecycle"] = "DRAFT"
    elif entity_type == "condition_node":
        if str(values.get("node_kind") or "") != "LEAF":
            raise KnowledgeDatabaseError("一期仅允许修订 LEAF 的类型化条件字段")
        for field, value in patch.items():
            typed_field = "expected_value_json" if field == "expected_value" else field
            values[typed_field] = (
                _json_text(value) if field == "expected_value" else _none_if_blank(value)
            )
        if (
            str(values.get("criterion_type") or "") not in {
                "diagnosis", "histology", "stage", "disease_status", "resectability",
                "biomarker", "age", "sex", "menopausal_status", "prior_therapy",
                "therapy_count", "line_of_therapy", "treatment_status",
                "combination_requirement", "surgery_status", "radiotherapy_status",
                "transplant_eligibility", "time_window", "clinician_assessment", "unsupported",
            }
            or str(values.get("operator") or "") not in {
                "EQUALS", "NOT_EQUALS", "IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS",
                "EXISTS", "NOT_EXISTS", "GTE", "LTE", "WITHIN_DAYS",
            }
            or str(values.get("target_kind") or "") not in {"CONCEPT", "CLASS", "REGIMEN", "VALUE"}
        ):
            raise KnowledgeDatabaseError("condition expert edit 枚举非法")
        combination = str(values.get("combination_requirement") or "")
        if combination and combination not in {"REQUIRED", "OPTIONAL", "WITH_OR_WITHOUT"}:
            raise KnowledgeDatabaseError("condition combination requirement 非法")
        values["disposition"] = "in_review"
    elif entity_type == "alias":
        for field, value in patch.items():
            values[field] = _as_bit(value) if field == "is_ambiguous" else str(value)
        if not str(values.get("original_alias") or "").strip() or not str(
            values.get("normalized_alias") or ""
        ).strip():
            raise KnowledgeDatabaseError("alias expert edit 缺少名称")
        values["review_status"] = "needs_review"
    elif entity_type == "context":
        for field, value in patch.items():
            values[field] = _none_if_blank(value)
        if not str(values.get("cancer_context") or "").strip():
            raise KnowledgeDatabaseError("context expert edit 缺少 cancer_context")
    elif entity_type == "component":
        for field, value in patch.items():
            values[field] = int(value) if field == "sibling_order" else str(value)
        if (
            str(values.get("target_kind") or "") not in {"CONCEPT", "CLASS"}
            or str(values.get("requirement") or "") not in {"REQUIRED", "OPTIONAL", "WITH_OR_WITHOUT"}
            or not str(values.get("target_id") or "").strip()
            or not str(values.get("token") or "").strip()
        ):
            raise KnowledgeDatabaseError("component expert edit 非法")
    else:
        raise KnowledgeDatabaseError("entity 不支持 APPROVE_WITH_EDIT 投影")
    assert target.table is not None
    values[target.checksum_column] = _content_checksum_after_edit(target.table, values)
    return _review_projection(
        target,
        values,
        reviewed_checksum=reviewed_checksum,
    )


def _clone_after_review(
    target: _TypedRecord,
    values: Mapping[str, Any],
    *,
    guard: _Guard | None = None,
    rank: int,
) -> _TypedRecord:
    return replace(
        target,
        values=dict(values),
        guard=target.guard if guard is None else guard,
        append_only=False,
        no_op=False,
        after_review=True,
        expected_content_checksum=None,
        projection_rank=rank,
    )


def _eligibility_superseding_projections(
    event: _TypedRecord,
    target: _TypedRecord,
    records: Sequence[_TypedRecord],
) -> list[_TypedRecord]:
    branches = {
        str(record.key_value): record
        for record in records
        if record.table == "kb.eligibility_branch" and not record.after_review
    }
    if target.table == "kb.eligibility_rule_revision":
        revision = target
        revision_id = str(target.key_value)
    elif target.table == "kb.condition_node":
        branch = branches.get(str(target.values.get("branch_id") or ""))
        if branch is None:
            raise KnowledgeDatabaseError("condition branch 不存在")
        revision_id = str(branch.values.get("rule_revision_id") or "")
        revision = next(
            (
                record
                for record in records
                if record.table == "kb.eligibility_rule_revision"
                and str(record.key_value) == revision_id
                and not record.after_review
            ),
            None,
        )
    else:
        raise KnowledgeDatabaseError("eligibility 内容修订 entity 非法")
    if revision is None or str(revision.values.get("lifecycle") or "") not in _MUTABLE_LIFECYCLES:
        raise KnowledgeDatabaseError("eligibility revision 不可修订")
    patch = _event_patch(event)
    revision_values = (
        dict(
            _edited_target_projection(
                revision,
                entity_type="eligibility_revision",
                patch=patch,
                reviewed_checksum=str(event.values["reviewed_content_checksum"]),
            ).values
        )
        if target is revision
        else dict(revision.values)
    )
    selected_branches = sorted(
        (
            record
            for record in branches.values()
            if str(record.values.get("rule_revision_id") or "") == revision_id
        ),
        key=lambda item: str(item.key_value),
    )
    old_branch_ids = {str(record.key_value) for record in selected_branches}
    source_nodes = sorted(
        (
            record
            for record in records
            if record.table == "kb.condition_node"
            and str(record.values.get("branch_id") or "") in old_branch_ids
            and not record.after_review
        ),
        key=lambda item: str(item.key_value),
    )
    patched_node_values = (
        dict(
            _edited_target_projection(
                target,
                entity_type="condition_node",
                patch=patch,
                reviewed_checksum=str(event.values["reviewed_content_checksum"]),
            ).values
        )
        if target.table == "kb.condition_node"
        else None
    )
    semantic_payload = {
        "revision": {
            key: value
            for key, value in revision_values.items()
            if key not in {"rule_revision_id", "lifecycle", "supersedes_revision_id", "content_checksum"}
        },
        "branches": [
            {
                key: value
                for key, value in record.values.items()
                if key not in {"rule_revision_id", "disposition", "content_checksum"}
            }
            for record in selected_branches
        ],
        "nodes": [
            {
                key: value
                for key, value in (
                    patched_node_values if record is target else record.values
                ).items()
                if key not in {"disposition", "content_checksum"}
            }
            for record in source_nodes
        ],
    }
    new_revision_id = stable_id(
        "eligibilityrevision",
        revision.values.get("logical_rule_id"),
        checksum(semantic_payload),
    )
    revision_values.update(
        rule_revision_id=new_revision_id,
        lifecycle="DRAFT",
        supersedes_revision_id=revision_id,
    )
    revision_values["content_checksum"] = _content_checksum_after_edit(
        "kb.eligibility_rule_revision", revision_values
    )
    projections = [
        _clone_after_review(revision, revision_values, rank=10)
    ]

    branch_ids = {
        str(record.key_value): stable_id(
            "branch", new_revision_id, record.key_value
        )
        for record in selected_branches
    }
    for record in selected_branches:
        values = dict(record.values)
        values.update(
            branch_id=branch_ids[str(record.key_value)],
            rule_revision_id=new_revision_id,
            disposition="in_review",
        )
        values["content_checksum"] = _content_checksum_after_edit(
            "kb.eligibility_branch", values
        )
        projections.append(
            _clone_after_review(
                record,
                values,
                guard=_Guard(
                    "kb.eligibility_rule_revision", "rule_revision_id", new_revision_id,
                    "lifecycle", _MUTABLE_LIFECYCLES,
                ),
                rank=20,
            )
        )

    node_ids = {
        str(record.key_value): stable_id(
            "node", branch_ids[str(record.values["branch_id"])], record.key_value
        )
        for record in source_nodes
    }
    depth_cache: dict[str, int] = {}

    def depth(record: _TypedRecord) -> int:
        identity = str(record.key_value)
        if identity in depth_cache:
            return depth_cache[identity]
        parent_id = str(record.values.get("parent_node_id") or "")
        if not parent_id:
            value = 0
        else:
            parent = next(
                (item for item in source_nodes if str(item.key_value) == parent_id),
                None,
            )
            if parent is None:
                raise KnowledgeDatabaseError("condition parent 不在 superseding 子图")
            value = depth(parent) + 1
        depth_cache[identity] = value
        return value

    for record in source_nodes:
        values = dict(
            patched_node_values if record is target else record.values
        )
        old_parent = str(record.values.get("parent_node_id") or "")
        values.update(
            node_id=node_ids[str(record.key_value)],
            branch_id=branch_ids[str(record.values["branch_id"])],
            parent_node_id=node_ids.get(old_parent),
            disposition="in_review",
        )
        values["content_checksum"] = _content_checksum_after_edit(
            "kb.condition_node", values
        )
        projections.append(
            _clone_after_review(
                record,
                values,
                guard=_Guard(
                    "kb.eligibility_rule_revision", "rule_revision_id", new_revision_id,
                    "lifecycle", _MUTABLE_LIFECYCLES,
                ),
                rank=30 + depth(record),
            )
        )
    return projections


def _regimen_superseding_projections(
    event: _TypedRecord,
    target: _TypedRecord,
    records: Sequence[_TypedRecord],
) -> list[_TypedRecord]:
    if target.table == "kb.regimen_revision":
        revision = target
    else:
        revision_id = str(target.values.get("regimen_revision_id") or "")
        revision = next(
            (
                record
                for record in records
                if record.table == "kb.regimen_revision"
                and str(record.key_value) == revision_id
                and not record.after_review
            ),
            None,
        )
        if revision is None:
            raise KnowledgeDatabaseError("regimen parent revision 不存在")
    old_revision_id = str(revision.key_value)
    if str(revision.values.get("lifecycle") or "") not in _MUTABLE_LIFECYCLES:
        raise KnowledgeDatabaseError("regimen revision 不可修订")
    patch = _event_patch(event)
    if target is revision:
        revision_values = dict(
            _edited_target_projection(
                revision,
                entity_type="regimen",
                patch=patch,
                reviewed_checksum=str(event.values["reviewed_content_checksum"]),
            ).values
        )
    else:
        revision_values = dict(revision.values)
    child_tables = {
        "kb.regimen_alias": ("alias_id", "alias", 20),
        "kb.regimen_context": ("context_id", "context", 20),
        "kb.regimen_component": ("component_id", "component", 20),
    }
    child_ids: dict[tuple[str, str], str] = {}
    children: list[_TypedRecord] = []
    for record in records:
        if (
            record.table in child_tables
            and str(record.values.get("regimen_revision_id") or "") == old_revision_id
            and not record.after_review
        ):
            children.append(record)
            prefix = child_tables[str(record.table)][1]
            child_ids[(str(record.table), str(record.key_value))] = stable_id(
                prefix, old_revision_id, record.key_value
            )
    patched_values = (
        _edited_target_projection(
            target,
            entity_type=str(event.values["entity_type"]),
            patch=patch,
            reviewed_checksum=str(event.values["reviewed_content_checksum"]),
        ).values
        if target is not revision
        else None
    )
    schedules = sorted(
        (
            item
            for item in records
            if item.table == "kb.regimen_schedule_component"
            and str(item.values.get("regimen_revision_id") or "") == old_revision_id
            and not item.after_review
        ),
        key=lambda item: str(item.key_value),
    )
    semantic_payload = {
        "revision": {
            key: value
            for key, value in revision_values.items()
            if key not in {"regimen_revision_id", "lifecycle", "supersedes_revision_id", "content_checksum"}
        },
        "children": [
            {
                key: value
                for key, value in (
                    patched_values if record is target else record.values
                ).items()
                if key not in {"regimen_revision_id", "review_status", "content_checksum"}
            }
            for record in sorted(children, key=lambda item: (str(item.table), str(item.key_value)))
        ],
        "schedules": [
            {
                key: value
                for key, value in record.values.items()
                if key not in {"regimen_revision_id", "content_checksum"}
            }
            for record in schedules
        ],
    }
    new_revision_id = stable_id(
        "regimenrevision",
        revision.values.get("logical_regimen_id"),
        checksum(semantic_payload),
    )
    revision_values.update(
        regimen_revision_id=new_revision_id,
        lifecycle="DRAFT",
        supersedes_revision_id=old_revision_id,
    )
    revision_values["content_checksum"] = _content_checksum_after_edit(
        "kb.regimen_revision", revision_values
    )
    projections = [_clone_after_review(revision, revision_values, rank=10)]
    child_ids = {
        (str(record.table), str(record.key_value)): stable_id(
            child_tables[str(record.table)][1], new_revision_id, record.key_value
        )
        for record in children
    }
    for record in sorted(children, key=lambda item: (str(item.table), str(item.key_value))):
        key_column, _prefix, rank = child_tables[str(record.table)]
        values = dict(patched_values if record is target else record.values)
        values.update(
            {key_column: child_ids[(str(record.table), str(record.key_value))]},
            regimen_revision_id=new_revision_id,
        )
        if record.table == "kb.regimen_alias":
            values["review_status"] = "needs_review"
        values["content_checksum"] = _content_checksum_after_edit(str(record.table), values)
        projections.append(
            _clone_after_review(
                record,
                values,
                guard=_Guard(
                    "kb.regimen_revision", "regimen_revision_id", new_revision_id,
                    "lifecycle", _MUTABLE_LIFECYCLES,
                ),
                rank=rank,
            )
        )
    for record in schedules:
        old_component = str(record.values.get("component_id") or "")
        new_component = child_ids.get(("kb.regimen_component", old_component))
        if not new_component:
            raise KnowledgeDatabaseError("schedule component 不在 superseding 子图")
        values = dict(record.values)
        values.update(
            schedule_component_id=stable_id("schedule", new_revision_id, record.key_value),
            regimen_revision_id=new_revision_id,
            component_id=new_component,
        )
        values["content_checksum"] = _content_checksum_after_edit(
            "kb.regimen_schedule_component", values
        )
        projections.append(
            _clone_after_review(
                record,
                values,
                guard=_Guard(
                    "kb.regimen_revision", "regimen_revision_id", new_revision_id,
                    "lifecycle", _MUTABLE_LIFECYCLES,
                ),
                rank=30,
            )
        )
    return projections


def _curated_review_projections(
    event: _TypedRecord,
    atom: _TypedRecord,
    mappings: Sequence[_TypedRecord],
    records: Sequence[_TypedRecord],
    *,
    require_materialized_mapping: bool = False,
) -> list[_TypedRecord]:
    decision = str(event.values["decision"])
    patch = _event_patch(event)
    reviewed_checksum = str(event.values["reviewed_content_checksum"])
    if len(mappings) > 1:
        raise KnowledgeDatabaseError("curated atom 存在多个 mapping")
    existing = mappings[0] if mappings else None
    current_kind = "" if existing is None else str(existing.values.get("target_kind") or "")
    current_id = "" if existing is None else str(existing.values.get("target_id") or "")
    target_kind = str(patch.get("target_kind") or current_kind)
    target_id = str(patch.get("target_id") or current_id)
    evidence = str(
        patch.get("verification_evidence")
        or event.values.get("evidence_reference")
        or ("" if existing is None else existing.values.get("verification_evidence") or "")
        or ""
    ).strip()
    requested_status = str(patch.get("migration_status") or "")

    if decision == "APPROVE":
        if requested_status not in {"", "VERIFIED"}:
            raise KnowledgeDatabaseError("curated APPROVE 状态非法")
        if not existing or not target_kind or not target_id or not evidence:
            raise KnowledgeDatabaseError("curated VERIFIED 缺少 target 或 evidence")
        if not require_materialized_mapping and (
            target_kind != current_kind or target_id != current_id
        ):
            raise KnowledgeDatabaseError("curated target 修订必须使用 APPROVE_WITH_EDIT")
        live_conditions = {
            str(record.key_value)
            for record in records
            if record.table in {
                "kb.eligibility_rule_revision", "kb.eligibility_branch", "kb.condition_node"
            }
            and not record.after_review
        }
        live_dictionaries = {
            str(record.key_value)
            for record in records
            if record.table in {"kb.term_dictionary_entry", "kb.drug_concept"}
            and not record.after_review
        }
        if target_kind == "CONDITION" and target_id not in live_conditions:
            raise KnowledgeDatabaseError("curated CONDITION target 不存在")
        if target_kind == "DICTIONARY" and target_id not in live_dictionaries:
            raise KnowledgeDatabaseError("curated DICTIONARY target 不存在")
        if target_kind in {"EVALUATOR_POLICY", "REVIEW_GUIDANCE", "REGRESSION_CASE"} and not re.fullmatch(
            r"(?:sha256:[0-9a-f]{64}|(?:test|case|manifest|policy|regression):[^\s]+)",
            evidence,
        ):
            raise KnowledgeDatabaseError("curated external target evidence 不可核验")
        mapping_values = dict(existing.values)
        mapping_values.update(target_kind=target_kind, target_id=target_id)
        mapping_values["verification_evidence"] = evidence
        mapping_values["content_checksum"] = _typed_checksum(mapping_values)
        atom_values = dict(atom.values)
        atom_values["migration_status"] = "VERIFIED"
        return [
            replace(
                _review_projection(
                    existing,
                    mapping_values,
                    reviewed_checksum=str(existing.values["content_checksum"]),
                    rank=10,
                ),
                expected_content_checksum=(
                    None
                    if require_materialized_mapping
                    else str(existing.values["content_checksum"])
                ),
            ),
            _review_projection(
                atom,
                atom_values,
                reviewed_checksum=reviewed_checksum,
                rank=20,
            ),
        ]

    if decision != "APPROVE_WITH_EDIT":
        return []
    if requested_status == "VERIFIED" or not target_kind or not target_id:
        raise KnowledgeDatabaseError("curated edit 必须生成带 target 的 MAPPED 草稿")
    atom_values = dict(atom.values)
    atom_values["migration_status"] = "MAPPED"
    if existing is None:
        mapping_values = {
            "mapping_id": stable_id("mapping", atom.key_value, target_kind, target_id),
            "atom_id": atom.key_value,
            "target_kind": target_kind,
            "target_id": target_id,
            "verification_evidence": _none_if_blank(evidence),
        }
        mapping_values["content_checksum"] = _typed_checksum(mapping_values)
        mapping_projection = _record(
            "curated_knowledge_mapping",
            "kb.curated_knowledge_mapping",
            "mapping_id",
            mapping_values,
            guard=_Guard(
                "kb.curated_knowledge_atom", "atom_id", atom.key_value,
                "migration_status", frozenset({"DISCOVERED", "MAPPED"}),
            ),
            after_review=True,
            projection_rank=10,
        )
    else:
        mapping_values = dict(existing.values)
        mapping_values.update(
            target_kind=target_kind,
            target_id=target_id,
            verification_evidence=_none_if_blank(evidence),
        )
        mapping_values["content_checksum"] = _typed_checksum(mapping_values)
        mapping_projection = _review_projection(
            existing,
            mapping_values,
            reviewed_checksum=str(existing.values["content_checksum"]),
            rank=10,
        )
    return [
        mapping_projection,
        _review_projection(
            atom,
            atom_values,
            reviewed_checksum=reviewed_checksum,
            rank=20,
        ),
    ]


def _review_projection_records(
    records: Sequence[_TypedRecord],
) -> tuple[list[_TypedRecord], set[tuple[str, str]]]:
    targets: dict[tuple[str, str], _TypedRecord] = {}
    mappings_by_atom: dict[str, list[_TypedRecord]] = {}
    for record in records:
        entity_type = _REVIEW_ENTITY_BY_TABLE.get(record.table or "")
        if entity_type and not record.append_only and not record.no_op:
            targets[(entity_type, str(record.key_value))] = record
        if record.table == "kb.curated_knowledge_mapping":
            mappings_by_atom.setdefault(str(record.values["atom_id"]), []).append(record)

    projections: list[_TypedRecord] = []
    seen: set[tuple[str, str, str]] = set()
    edited_aggregates: set[tuple[str, str]] = set()
    suppressed_base_records: set[tuple[str, str]] = set()
    branch_parent = {
        str(record.key_value): str(record.values.get("rule_revision_id") or "")
        for record in records
        if record.table == "kb.eligibility_branch" and not record.after_review
    }
    for event in (record for record in records if record.table == "kb.review_event"):
        entity_type = str(event.values["entity_type"])
        entity_id = str(event.values["entity_id"])
        identity = (entity_type, entity_id, str(event.values["field_name"]))
        if identity in seen:
            raise KnowledgeDatabaseError("同批次存在重复 review target")
        seen.add(identity)
        target = targets.get((entity_type, entity_id))
        if target is None:
            raise KnowledgeDatabaseError("review target entity 不存在")
        decision = str(event.values["decision"])
        reviewed_checksum = str(event.values["reviewed_content_checksum"])
        if entity_type == "curated_knowledge_atom":
            patch = _event_patch(event)
            require_materialized_mapping = (
                decision == "APPROVE"
                and bool(str(patch.get("target_kind") or "").strip())
                and bool(str(patch.get("target_id") or "").strip())
            )
            atom_mappings = mappings_by_atom.get(entity_id, [])
            if require_materialized_mapping:
                if len(atom_mappings) != 1:
                    raise KnowledgeDatabaseError("curated APPROVE 缺少待核验 mapping")
                suppressed_base_records.add(
                    ("kb.curated_knowledge_mapping", str(atom_mappings[0].key_value))
                )
            projections.extend(
                _curated_review_projections(
                    event,
                    target,
                    atom_mappings,
                    records,
                    require_materialized_mapping=require_materialized_mapping,
                )
            )
            continue
        if decision == "APPROVE_WITH_EDIT":
            if entity_type == "eligibility_revision":
                aggregate = ("eligibility", entity_id)
            elif entity_type == "condition_node":
                aggregate = (
                    "eligibility",
                    branch_parent.get(str(target.values.get("branch_id") or ""), ""),
                )
            elif entity_type == "regimen":
                aggregate = ("regimen", entity_id)
            elif entity_type in {"alias", "context", "component"}:
                aggregate = (
                    "regimen", str(target.values.get("regimen_revision_id") or "")
                )
            else:
                raise KnowledgeDatabaseError("entity 不支持 APPROVE_WITH_EDIT 投影")
            if not aggregate[1] or aggregate in edited_aggregates:
                raise KnowledgeDatabaseError("同一 aggregate 每批次只允许一个结构化修订")
            edited_aggregates.add(aggregate)
            if aggregate[0] == "eligibility":
                projections.extend(
                    _eligibility_superseding_projections(event, target, records)
                )
            else:
                projections.extend(
                    _regimen_superseding_projections(event, target, records)
                )
            continue
        if decision != "APPROVE":
            continue
        status_field = {
            "branch": ("disposition", "approved"),
            "condition_node": ("disposition", "approved"),
            "alias": ("review_status", "approved"),
            "drug_class": ("lifecycle", "APPROVED"),
        }.get(entity_type)
        if status_field:
            values = dict(target.values)
            values[status_field[0]] = status_field[1]
            projections.append(
                _review_projection(
                    target,
                    values,
                    reviewed_checksum=reviewed_checksum,
                )
            )
    return projections, suppressed_base_records


def _eligibility_records(row: StagingRow, *, batch_id: str, branch_revisions: Mapping[str, str]) -> list[_TypedRecord]:
    payload = row.canonical_payload
    records: list[_TypedRecord] = []
    fallback_type = row.sheet_name
    fallback_id = row.stable_row_id
    if row.sheet_name == "02_药品产品":
        lifecycle = _authoring_lifecycle(payload.get("concept_lifecycle"))
        concept_values = {
            "drug_concept_id": str(payload["drug_concept_id"]),
            "canonical_name": str(payload["canonical_name"]),
            "normalized_name": str(payload["normalized_name"]),
            "lifecycle": lifecycle,
        }
        concept_values["content_checksum"] = _typed_checksum(concept_values, "lifecycle")
        concept_guard = _Guard(
            "kb.drug_concept", "drug_concept_id", concept_values["drug_concept_id"],
            "lifecycle", _MUTABLE_LIFECYCLES,
        )
        records.append(_record(
            "drug_concept", "kb.drug_concept", "drug_concept_id", concept_values,
            state_column="lifecycle",
        ))
        product_values = {
            "drug_product_id": str(payload["drug_product_id"]),
            "drug_concept_id": concept_values["drug_concept_id"],
            "product_name": str(payload.get("product_name") or payload["canonical_name"]),
            "dosage_form": _none_if_blank(payload.get("dosage_form")),
            "manufacturer": _none_if_blank(payload.get("manufacturer")),
            "source_kind": str(payload.get("source_kind") or "unknown"),
        }
        product_values["content_checksum"] = _typed_checksum(product_values)
        records.append(_record(
            "drug_product", "kb.drug_product", "drug_product_id", product_values,
            guard=concept_guard,
        ))
        for code_system, field in (("INSURANCE", "insurance_code"), ("HOSPITAL", "hospital_code")):
            code_value = str(payload.get(field) or "").strip()
            if not code_value:
                continue
            xref_values = {
                "drug_code_xref_id": stable_id(
                    "drugxref", product_values["drug_product_id"], code_system, code_value
                ),
                "drug_product_id": product_values["drug_product_id"],
                "code_system": code_system,
                "code_value": code_value,
            }
            xref_values["content_checksum"] = _typed_checksum(xref_values)
            records.append(_record(
                "drug_code_xref", "kb.drug_code_xref", "drug_code_xref_id", xref_values,
                guard=concept_guard,
            ))
        fallback_type = "drug_product"
        fallback_id = product_values["drug_product_id"]
    elif row.sheet_name == "03_来源原文":
        document_values = {
            "source_document_id": str(payload["source_document_id"]),
            "source_type": str(payload["source_type"]),
            "title": str(payload["title"]),
            "document_version": str(payload["document_version"]),
            "document_year": _int_if_present(payload.get("document_year")),
            "retrieval_date": str(payload["retrieval_date"]),
            "content_checksum": str(payload["document_checksum"]),
        }
        records.append(_record(
            "source_document", "kb.source_document", "source_document_id", document_values,
        ))
        fragment_values = {
            "source_fragment_id": str(payload["source_fragment_id"]),
            "source_document_id": document_values["source_document_id"],
            "anchor": str(payload["anchor"]),
            "page_numbers_json": _json_text(payload.get("page_numbers"), blank_is_null=False),
            "original_text": str(payload["original_text"]),
            "content_checksum": str(payload["content_checksum"]),
        }
        records.append(_record(
            "source_fragment", "kb.source_fragment", "source_fragment_id", fragment_values,
        ))
        fallback_type = "source_fragment"
        fallback_id = fragment_values["source_fragment_id"]
    elif row.sheet_name == "04_适应证分支":
        lifecycle = _authoring_lifecycle(payload.get("lifecycle"))
        revision_values = {
            "rule_revision_id": str(payload["rule_revision_id"]),
            "logical_rule_id": str(payload["logical_rule_id"]),
            "drug_concept_id": str(payload["drug_concept_id"]),
            "source_fragment_id": str(payload["source_fragment_id"]),
            "policy_scope": str(payload["policy_scope"]),
            "lifecycle": lifecycle,
            "effective_from": str(payload["effective_from"]),
            "effective_to": str(payload["effective_to"]),
            "effective_date_basis": str(payload["effective_date_basis"]),
            "date_override_reason": _none_if_blank(payload.get("date_override_reason")),
            "date_review_comment": _none_if_blank(payload.get("date_review_comment")),
            "historical_application_policy": str(payload["historical_application_policy"]),
            "supersedes_revision_id": _none_if_blank(payload.get("supersedes_revision_id")),
            "content_checksum": str(payload["revision_content_checksum"]),
        }
        records.append(_record(
            "eligibility_rule_revision", "kb.eligibility_rule_revision", "rule_revision_id",
            revision_values, state_column="lifecycle",
        ))
        revision_guard = _Guard(
            "kb.eligibility_rule_revision", "rule_revision_id", revision_values["rule_revision_id"],
            "lifecycle", _MUTABLE_LIFECYCLES,
        )
        branch_values = {
            "branch_id": str(payload["branch_id"]),
            "rule_revision_id": revision_values["rule_revision_id"],
            "source_fragment_id": str(payload["source_fragment_id"]),
            "ordinal_no": int(payload["ordinal"]),
            "source_text": str(payload["source_text"]),
            "source_span_start": int(payload["source_span_start"]),
            "source_span_end": int(payload["source_span_end"]),
            "disposition": str(payload["disposition"]),
        }
        branch_values["content_checksum"] = _typed_checksum(
            branch_values, "disposition"
        )
        records.append(_record(
            "eligibility_branch", "kb.eligibility_branch", "branch_id", branch_values,
            state_column="disposition",
            mutable_states=frozenset({"in_review", "rejected", "unsupported"}),
            guard=revision_guard,
        ))
        fallback_type = "branch"
        fallback_id = branch_values["branch_id"]
    elif row.sheet_name == "05_条件节点":
        revision_id = branch_revisions.get(str(payload["branch_id"]))
        if not revision_id:
            raise KnowledgeDatabaseError("条件节点无法解析所属 rule revision")
        node_values = {
            "node_id": str(payload["node_id"]),
            "branch_id": str(payload["branch_id"]),
            "parent_node_id": _none_if_blank(payload.get("parent_node_id")),
            "sibling_order": int(payload["sibling_order"]),
            "node_kind": str(payload["node_kind"]),
            "criterion_type": _none_if_blank(payload.get("criterion_type")),
            "operator": _none_if_blank(payload.get("operator")),
            "target_kind": _none_if_blank(payload.get("target_kind")),
            "target_id": _none_if_blank(payload.get("target_id")),
            "expected_value_json": _json_text(payload.get("expected_value")),
            "combination_requirement": _none_if_blank(payload.get("combination_requirement")),
            "source_fragment_id": str(payload["source_fragment_id"]),
            "source_span_start": int(payload["source_span_start"]),
            "source_span_end": int(payload["source_span_end"]),
            "disposition": str(payload["disposition"]),
        }
        node_values["content_checksum"] = _typed_checksum(
            node_values, "disposition"
        )
        records.append(_record(
            "condition_node", "kb.condition_node", "node_id", node_values,
            state_column="disposition",
            mutable_states=frozenset({"in_review", "rejected", "unsupported"}),
            guard=_Guard(
                "kb.eligibility_rule_revision", "rule_revision_id", revision_id,
                "lifecycle", _MUTABLE_LIFECYCLES,
            ),
        ))
        fallback_type = "condition_node"
        fallback_id = node_values["node_id"]
    elif row.sheet_name == "06_专家审核":
        review = _review_record(row, fallback_type="review", fallback_id=row.stable_row_id)
        return [review] if review else [
            _TypedRecord("review_event", None, "", {}, no_op=True)
        ]
    elif row.sheet_name == "07_术语字典":
        if str(payload.get("dictionary") or "") == "drug_class":
            raw_terms = payload.get("match_terms")
            try:
                terms = json.loads(raw_terms) if isinstance(raw_terms, str) else raw_terms
            except json.JSONDecodeError as exc:
                raise KnowledgeDatabaseError("drug_class match_terms 非法") from exc
            if not isinstance(terms, list):
                raise KnowledgeDatabaseError("drug_class match_terms 必须为 list")
            normalized_terms = sorted(
                {str(item).strip() for item in terms if str(item).strip()}
            )
            if not normalized_terms:
                raise KnowledgeDatabaseError("drug_class match_terms 不能为空")
            class_values = {
                "drug_class_id": str(payload["value"]),
                "canonical_name": str(
                    payload.get("canonical_name") or payload.get("description") or ""
                ),
                "match_terms_json": canonical_json_bytes(normalized_terms).decode("utf-8"),
                "lifecycle": _authoring_lifecycle(payload.get("lifecycle")),
            }
            if not class_values["drug_class_id"] or not class_values["canonical_name"]:
                raise KnowledgeDatabaseError("drug_class authority 字段缺失")
            class_values["content_checksum"] = _typed_checksum(
                class_values, "lifecycle"
            )
            records.append(
                _record(
                    "drug_class", "kb.drug_class", "drug_class_id", class_values,
                    state_column="lifecycle",
                )
            )
            fallback_type = "drug_class"
            fallback_id = class_values["drug_class_id"]
        else:
            term_values = {
                "term_entry_id": stable_id("term", payload.get("dictionary"), payload.get("value")),
                "dictionary_name": str(payload["dictionary"]),
                "term_value": str(payload["value"]),
                "description": str(payload.get("description") or ""),
            }
            term_values["content_checksum"] = _typed_checksum(term_values)
            records.append(_record(
                "term_dictionary_entry", "kb.term_dictionary_entry", "term_entry_id", term_values,
            ))
    elif row.sheet_name == "08_QA问题":
        qa_values = {
            "qa_issue_id": stable_id("qa", batch_id, row.sheet_name, row.row_checksum),
            "import_batch_id": batch_id,
            "workbook_kind": "eligibility",
            "qa_code": str(payload["qa_code"]),
            "entity_id": _none_if_blank(payload.get("entity_id")),
            "severity": str(payload["severity"]),
            "detail": str(payload.get("detail") or ""),
            "content_checksum": row.row_checksum,
        }
        records.append(_record(
            "authoring_qa_issue", "kb.authoring_qa_issue", "qa_issue_id", qa_values,
        ))
    elif row.sheet_name == "09_肿瘤知识保全":
        if type(payload.get("oncology")) is not bool:
            raise KnowledgeDatabaseError("curated atom oncology 必须为布尔值")
        rule_status = str(payload.get("rule_status") or "")
        if rule_status not in {"ready", "drafting", "abandoned"}:
            raise KnowledgeDatabaseError("curated atom rule_status 非法")
        atom_values = {
            "atom_id": str(payload["atom_id"]),
            "source_rule_id": str(payload["source_rule_id"]),
            "oncology": _as_bit(payload["oncology"]),
            "rule_status": rule_status,
            "source_field_or_test": str(payload["source_field_or_test"]),
            "atom_type": str(payload["atom_type"]),
            "canonical_payload": _json_text(payload["canonical_payload"], blank_is_null=False),
            "source_checksum": str(payload["source_checksum"]),
            "migration_status": str(payload["migration_status"]),
        }
        atom_values["content_checksum"] = _typed_checksum(atom_values, "migration_status")
        records.append(_record(
            "curated_knowledge_atom", "kb.curated_knowledge_atom", "atom_id", atom_values,
            checksum_column="content_checksum", state_column="migration_status",
            mutable_states=frozenset({"DISCOVERED", "MAPPED"}),
        ))
        target_kind = str(payload.get("target_kind") or "")
        target_id = str(payload.get("target_id") or "")
        if atom_values["migration_status"] in {"MAPPED", "VERIFIED"} and (not target_kind or not target_id):
            raise KnowledgeDatabaseError("mapped curated atom 缺少 typed target")
        if target_kind and target_id:
            mapping_values = {
                "mapping_id": stable_id("mapping", atom_values["atom_id"], target_kind, target_id),
                "atom_id": atom_values["atom_id"],
                "target_kind": target_kind,
                "target_id": target_id,
                "verification_evidence": _none_if_blank(payload.get("verification_evidence")),
            }
            mapping_values["content_checksum"] = _typed_checksum(mapping_values)
            records.append(_record(
                "curated_knowledge_mapping", "kb.curated_knowledge_mapping", "mapping_id",
                mapping_values,
                guard=_Guard(
                    "kb.curated_knowledge_atom", "atom_id", atom_values["atom_id"],
                    "migration_status", frozenset({"DISCOVERED", "MAPPED"}),
                ),
            ))
        fallback_type = "curated_knowledge_atom"
        fallback_id = atom_values["atom_id"]
    else:
        raise KnowledgeDatabaseError(f"不支持的 eligibility sheet: {row.sheet_name!r}")
    review = _review_record(
        row,
        fallback_type=fallback_type,
        fallback_id=fallback_id,
        current_content_checksum=_projected_content_checksum(
            records,
            entity_type=fallback_type,
            entity_id=fallback_id,
        ),
    )
    if review:
        records.append(review)
    return records


def _regimen_records(row: StagingRow, *, batch_id: str) -> list[_TypedRecord]:
    payload = row.canonical_payload
    records: list[_TypedRecord] = []
    fallback_type = row.sheet_name
    fallback_id = row.stable_row_id
    if row.sheet_name == "02_方案主表":
        lifecycle = _authoring_lifecycle(payload.get("lifecycle"))
        try:
            revision_values = regimen_revision_typed_values(
                payload,
                lifecycle=lifecycle,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise KnowledgeDatabaseError("方案 effective_window 非法") from exc
        records.append(_record(
            "regimen_revision", "kb.regimen_revision", "regimen_revision_id", revision_values,
            state_column="lifecycle",
        ))
        fallback_type = "regimen"
        fallback_id = revision_values["regimen_revision_id"]
    elif row.sheet_name == "03_方案别名":
        revision_id = _none_if_blank(payload.get("regimen_revision_id"))
        alias_values = {
            "alias_id": str(payload["alias_id"]),
            "regimen_revision_id": revision_id,
            "original_alias": str(payload["original_alias"]),
            "normalized_alias": str(payload["normalized_alias"]),
            "alias_type": str(payload["alias_type"]),
            "language_code": str(payload.get("language") or "und"),
            "aggregate_frequency": _int_if_present(payload.get("aggregate_frequency")),
            "source_corpus_checksum": _none_if_blank(payload.get("source_corpus_checksum")),
            "review_status": str(payload.get("review_status") or "needs_review"),
            "is_ambiguous": _as_bit(payload.get("ambiguous")),
        }
        alias_values["content_checksum"] = _typed_checksum(
            alias_values, "review_status"
        )
        guard = None if revision_id is None else _Guard(
            "kb.regimen_revision", "regimen_revision_id", revision_id,
            "lifecycle", _MUTABLE_LIFECYCLES,
        )
        records.append(_record(
            "regimen_alias", "kb.regimen_alias", "alias_id", alias_values,
            state_column="review_status",
            mutable_states=frozenset({"draft", "needs_review", "changes_requested"}),
            guard=guard,
        ))
        fallback_type = "alias"
        fallback_id = alias_values["alias_id"]
    elif row.sheet_name == "04_方案上下文":
        revision_id = str(payload["regimen_revision_id"])
        context_values = {
            "context_id": str(payload["context_id"]),
            "regimen_revision_id": revision_id,
            "cancer_context": str(payload["cancer_context"]),
            "histology": _none_if_blank(payload.get("histology")),
            "clinical_setting": _none_if_blank(payload.get("clinical_setting")),
        }
        context_values["content_checksum"] = _typed_checksum(context_values)
        records.append(_record(
            "regimen_context", "kb.regimen_context", "context_id", context_values,
            guard=_Guard(
                "kb.regimen_revision", "regimen_revision_id", revision_id,
                "lifecycle", _MUTABLE_LIFECYCLES,
            ),
        ))
        fallback_type = "context"
        fallback_id = context_values["context_id"]
    elif row.sheet_name == "05_方案组分":
        revision_id = str(payload["regimen_revision_id"])
        component_values = {
            "component_id": str(payload["component_id"]),
            "regimen_revision_id": revision_id,
            "target_kind": str(payload["target_kind"]),
            "target_id": str(payload["target_id"]),
            "token": str(payload["token"]),
            "component_role": str(payload["component_role"]),
            "requirement": str(payload["requirement"]),
            "sibling_order": int(payload["sibling_order"]),
            "source_fragment_id": _none_if_blank(payload.get("source_reference")),
        }
        component_values["content_checksum"] = _typed_checksum(component_values)
        records.append(_record(
            "regimen_component", "kb.regimen_component", "component_id", component_values,
            guard=_Guard(
                "kb.regimen_revision", "regimen_revision_id", revision_id,
                "lifecycle", _MUTABLE_LIFECYCLES,
            ),
        ))
        fallback_type = "component"
        fallback_id = component_values["component_id"]
    elif row.sheet_name == "06_预留给药字段":
        revision_id = str(payload["regimen_revision_id"])
        schedule_values = {
            "schedule_component_id": str(payload["schedule_component_id"]),
            "regimen_revision_id": revision_id,
            "component_id": str(payload["component_id"]),
            "dose_value": _decimal_if_present(payload.get("dose_value")),
            "dose_unit": _none_if_blank(payload.get("dose_unit")),
            "dose_basis": _none_if_blank(payload.get("dose_basis")),
            "route": _none_if_blank(payload.get("route")),
            "administration_days": _none_if_blank(payload.get("administration_days")),
            "cycle_length_days": _int_if_present(payload.get("cycle_length_days")),
            "max_cycles": _int_if_present(payload.get("max_cycles")),
            "treatment_phase": _none_if_blank(payload.get("treatment_phase")),
            "sequence_no": _int_if_present(payload.get("sequence_no")),
            "publishing_enabled": _as_bit(payload.get("publishing_enabled")),
            "inference_enabled": _as_bit(payload.get("inference_enabled")),
            "content_checksum": str(payload["row_checksum"]),
        }
        records.append(_record(
            "regimen_schedule_component", "kb.regimen_schedule_component",
            "schedule_component_id", schedule_values,
            guard=_Guard(
                "kb.regimen_revision", "regimen_revision_id", revision_id,
                "lifecycle", _MUTABLE_LIFECYCLES,
            ),
        ))
        fallback_type = "schedule_component"
        fallback_id = schedule_values["schedule_component_id"]
    elif row.sheet_name == "07_专家审核":
        review = _review_record(row, fallback_type="review", fallback_id=row.stable_row_id)
        return [review] if review else [
            _TypedRecord("review_event", None, "", {}, no_op=True)
        ]
    elif row.sheet_name == "08_QA问题":
        qa_values = {
            "qa_issue_id": stable_id("qa", batch_id, row.sheet_name, row.row_checksum),
            "import_batch_id": batch_id,
            "workbook_kind": "regimen",
            "qa_code": str(payload["qa_code"]),
            "entity_id": _none_if_blank(payload.get("entity_id")),
            "severity": str(payload["severity"]),
            "detail": str(payload.get("detail") or ""),
            "content_checksum": row.row_checksum,
        }
        records.append(_record(
            "authoring_qa_issue", "kb.authoring_qa_issue", "qa_issue_id", qa_values,
        ))
    else:
        raise KnowledgeDatabaseError(f"不支持的 regimen sheet: {row.sheet_name!r}")
    review = _review_record(
        row,
        fallback_type=fallback_type,
        fallback_id=fallback_id,
        current_content_checksum=_projected_content_checksum(
            records,
            entity_type=fallback_type,
            entity_id=fallback_id,
        ),
    )
    if review:
        records.append(review)
    return records


_TABLE_ORDER = {
    name: index
    for index, name in enumerate((
        "kb.source_document",
        "kb.source_fragment",
        "kb.drug_concept",
        "kb.drug_class",
        "kb.drug_product",
        "kb.drug_code_xref",
        "kb.eligibility_rule_revision",
        "kb.eligibility_branch",
        "kb.condition_node",
        "kb.curated_knowledge_atom",
        "kb.curated_knowledge_mapping",
        "kb.regimen_revision",
        "kb.regimen_alias",
        "kb.regimen_context",
        "kb.regimen_component",
        "kb.regimen_schedule_component",
        "kb.term_dictionary_entry",
        "kb.authoring_qa_issue",
        "kb.review_event",
    ))
}


def _ordered_records(records: Sequence[_TypedRecord]) -> list[_TypedRecord]:
    review_projections = sorted(
        (item for item in records if item.after_review),
        key=lambda item: (item.projection_rank, str(item.table), str(item.key_value)),
    )
    base = sorted(
        (item for item in records if not item.after_review),
        key=lambda item: (
            _TABLE_ORDER.get(item.table or "", len(_TABLE_ORDER)),
            str(item.key_value) if item.values else "",
        ),
    )
    nodes = [item for item in base if item.table == "kb.condition_node"]
    if not nodes:
        return base + review_projections
    non_nodes = [item for item in base if item.table != "kb.condition_node"]
    pending = list(enumerate(nodes))
    ordered_nodes: list[_TypedRecord] = []
    emitted: set[str] = set()
    while pending:
        pending_keys = {str(item.key_value) for _, item in pending}
        ready = sorted(
            (
                (sequence, item)
                for sequence, item in pending
                if not item.values.get("parent_node_id")
                or str(item.values["parent_node_id"]) in emitted
                or str(item.values["parent_node_id"]) not in pending_keys
            ),
            key=lambda pair: (str(pair[1].key_value), pair[0]),
        )
        if not ready:
            raise KnowledgeDatabaseError("condition node materialization dependency cycle")
        ready_sequences = {sequence for sequence, _ in ready}
        for _, item in ready:
            key = str(item.key_value)
            ordered_nodes.append(item)
            emitted.add(key)
        pending = [pair for pair in pending if pair[0] not in ready_sequences]
    insertion_index = next(
        (
            index
            for index, item in enumerate(non_nodes)
            if _TABLE_ORDER.get(item.table or "", 999)
            > _TABLE_ORDER["kb.condition_node"]
        ),
        len(non_nodes),
    )
    return (
        non_nodes[:insertion_index]
        + ordered_nodes
        + non_nodes[insertion_index:]
        + review_projections
    )


def _assert_in_batch_review_bindings(records: Sequence[_TypedRecord]) -> None:
    targets = {
        (str(record.table), str(record.key_value)): str(
            record.values[record.checksum_column]
        )
        for record in records
        if record.table in _REVIEW_ENTITY_BY_TABLE
        and not record.append_only
        and not record.no_op
        and record.checksum_column
    }
    for record in records:
        if record.table != "kb.review_event" or record.no_op:
            continue
        entity_type = str(record.values["entity_type"])
        locator = _REVIEW_ENTITY_LOCATORS.get(entity_type)
        if locator is None:
            raise KnowledgeDatabaseError("review entity type 不受支持")
        table, _ = locator
        expected = targets.get((table, str(record.values["entity_id"])))
        if expected is not None and expected != str(
            record.values["reviewed_content_checksum"]
        ):
            raise KnowledgeDatabaseError("review source checksum 与批次实体内容不一致")


def _review_staging_binding_issues(
    rows: Sequence[StagingRow],
    *,
    kind: str,
) -> list[ServerValidationIssue]:
    review_sheet = "06_专家审核" if kind == "eligibility" else "07_专家审核"
    branch_revisions = {
        str(row.canonical_payload.get("branch_id") or ""): str(
            row.canonical_payload.get("rule_revision_id") or ""
        )
        for row in rows
        if row.sheet_name == "04_适应证分支"
    }
    targets: dict[tuple[str, str], str] = {}
    for row in rows:
        if row.sheet_name == review_sheet:
            continue
        try:
            projected = (
                _eligibility_records(
                    row,
                    batch_id="server-validation",
                    branch_revisions=branch_revisions,
                )
                if kind == "eligibility"
                else _regimen_records(row, batch_id="server-validation")
            )
        except (KeyError, TypeError, ValueError, KnowledgeDatabaseError):
            continue
        for record in projected:
            entity_type = _REVIEW_ENTITY_BY_TABLE.get(record.table or "")
            if (
                entity_type is not None
                and not record.append_only
                and not record.no_op
                and record.checksum_column
            ):
                targets[(entity_type, str(record.key_value))] = str(
                    record.values[record.checksum_column]
                )

    issues: list[ServerValidationIssue] = []
    for row in rows:
        if row.sheet_name != review_sheet:
            continue
        payload = row.canonical_payload
        source_checksum = str(payload.get("source_checksum") or "")
        if not _SHA256_RE.fullmatch(source_checksum):
            issues.append(
                ServerValidationIssue(
                    row.sheet_name,
                    row.excel_row_no,
                    "REVIEW_SOURCE_CHECKSUM_INVALID",
                )
            )
            continue
        entity_type = str(payload.get("entity_type") or "")
        entity_id = str(payload.get("entity_id") or "")
        expected = targets.get((entity_type, entity_id))
        if expected is None:
            issues.append(
                ServerValidationIssue(
                    row.sheet_name,
                    row.excel_row_no,
                    "REVIEW_ENTITY_REFERENCE_MISSING",
                )
            )
        elif expected != source_checksum:
            issues.append(
                ServerValidationIssue(
                    row.sheet_name,
                    row.excel_row_no,
                    "REVIEW_CONTENT_CHECKSUM_MISMATCH",
                )
            )
    return issues


def _build_records(rows: list[StagingRow], *, batch_id: str, kind: str) -> list[_TypedRecord]:
    branch_revisions = {
        str(row.canonical_payload["branch_id"]): str(row.canonical_payload["rule_revision_id"])
        for row in rows
        if row.sheet_name == "04_适应证分支"
    }
    records: list[_TypedRecord] = []
    source_rows_accounted: set[tuple[str, int]] = set()
    for row in rows:
        _verify_staging_row_checksum(row)
        projected = (
            _eligibility_records(row, batch_id=batch_id, branch_revisions=branch_revisions)
            if kind == "eligibility"
            else _regimen_records(row, batch_id=batch_id)
        )
        if not projected:
            raise KnowledgeDatabaseError(
                f"staging row has no typed projection: sheet={row.sheet_name!r} row={row.excel_row_no}"
            )
        records.extend(projected)
        source_rows_accounted.add((row.sheet_name, row.excel_row_no))
    if len(source_rows_accounted) != len(rows):
        raise KnowledgeDatabaseError("staging source-row reconciliation mismatch")
    seen: dict[tuple[str, Any], str] = {}
    for item in records:
        if item.no_op or item.append_only:
            continue
        key = (str(item.table), item.key_value)
        incoming = str(item.values[item.checksum_column])
        prior = seen.setdefault(key, incoming)
        if prior != incoming:
            raise KnowledgeDatabaseError(
                f"conflicting typed checksum: entity={item.entity_type!r} id={item.key_value!r}"
            )
    _assert_in_batch_review_bindings(records)
    review_projections, suppressed_base_records = _review_projection_records(records)
    if suppressed_base_records:
        records = [
            replace(record, no_op=True)
            if record.table is not None
            and record.key_column
            and (str(record.table), str(record.key_value)) in suppressed_base_records
            and not record.after_review
            else record
            for record in records
        ]
    records.extend(review_projections)
    return _ordered_records(records)


def _fetch_state(cursor: Any, guard: _Guard) -> str | None:
    sql = (
        f"SELECT {_quoted_identifier(guard.state_column)} FROM {_quoted_table(guard.table)} "
        f"WITH (UPDLOCK, HOLDLOCK) WHERE {_quoted_identifier(guard.key_column)} = ?"
    )
    row = cursor.execute(sql, guard.key_value).fetchone()
    return None if row is None else str(row[0])


def _assert_reviewed_content_is_current(cursor: Any, values: Mapping[str, Any]) -> None:
    entity_type = str(values.get("entity_type") or "")
    locator = _REVIEW_ENTITY_LOCATORS.get(entity_type)
    if locator is None:
        raise KnowledgeDatabaseError("review entity type 不受支持")
    table, key_column = locator
    row = cursor.execute(
        f"SELECT [content_checksum] FROM {_quoted_table(table)} WITH (UPDLOCK, HOLDLOCK) "
        f"WHERE {_quoted_identifier(key_column)} = ?",
        values["entity_id"],
    ).fetchone()
    if row is None:
        raise KnowledgeDatabaseError("review target entity 不存在")
    reviewed = str(values.get("reviewed_content_checksum") or "")
    if not _SHA256_RE.fullmatch(reviewed) or str(row[0]) != reviewed:
        raise KnowledgeDatabaseError("reviewed content checksum 已过期或不匹配")


def _assert_review_targets_not_stale(
    cursor: Any, records: Sequence[_TypedRecord]
) -> None:
    """在任何 base upsert 前锁住已存在审核目标，禁止旧工作簿先回写再自证。"""

    for record in records:
        if record.table != "kb.review_event" or record.no_op:
            continue
        values = record.values
        locator = _REVIEW_ENTITY_LOCATORS.get(str(values.get("entity_type") or ""))
        if locator is None:
            raise KnowledgeDatabaseError("review entity type 不受支持")
        table, key_column = locator
        row = cursor.execute(
            f"SELECT [content_checksum] FROM {_quoted_table(table)} WITH (UPDLOCK, HOLDLOCK) "
            f"WHERE {_quoted_identifier(key_column)} = ?",
            values["entity_id"],
        ).fetchone()
        if row is not None and str(row[0]) != str(values["reviewed_content_checksum"]):
            raise KnowledgeDatabaseError("reviewed content checksum 已过期或不匹配")
        if str(values.get("entity_type") or "") == "curated_knowledge_atom":
            atom_mappings = [
                item
                for item in records
                if item.table == "kb.curated_knowledge_mapping"
                and str(item.values.get("atom_id") or "") == str(values["entity_id"])
                and not item.after_review
            ]
            patch = _event_patch(record)
            requires_materialized_mapping = (
                str(values.get("decision") or "") == "APPROVE"
                and bool(str(patch.get("target_kind") or "").strip())
                and bool(str(patch.get("target_id") or "").strip())
            )
            if requires_materialized_mapping:
                if len(atom_mappings) != 1:
                    raise KnowledgeDatabaseError("curated APPROVE 缺少待核验 mapping")
                mapping = atom_mappings[0]
                current = cursor.execute(
                    "SELECT [target_kind], [target_id] "
                    "FROM [kb].[curated_knowledge_mapping] WITH (UPDLOCK, HOLDLOCK) "
                    "WHERE [mapping_id] = ?",
                    mapping.key_value,
                ).fetchone()
                if current is None or (
                    str(current[0]) != str(patch["target_kind"])
                    or str(current[1]) != str(patch["target_id"])
                ):
                    raise KnowledgeDatabaseError(
                        "curated APPROVE 必须针对已 materialize 的 mapping"
                    )
                continue
            for mapping in atom_mappings:
                current = cursor.execute(
                    "SELECT [content_checksum] FROM [kb].[curated_knowledge_mapping] "
                    "WITH (UPDLOCK, HOLDLOCK) WHERE [mapping_id] = ?",
                    mapping.key_value,
                ).fetchone()
                if current is not None and str(current[0]) != str(
                    mapping.values["content_checksum"]
                ):
                    raise KnowledgeDatabaseError("curated mapping checksum 已过期或不匹配")


def _merge_typed_record(cursor: Any, record: _TypedRecord) -> str:
    if record.no_op:
        return "reused"
    assert record.table is not None
    table = _quoted_table(record.table)
    key_column = _quoted_identifier(record.key_column)
    select_columns = [] if record.append_only else [_quoted_identifier(record.checksum_column)]
    if record.state_column:
        select_columns.append(_quoted_identifier(record.state_column))
    selected = ", ".join(select_columns) if select_columns else key_column
    select_sql = (
        f"SELECT {selected} FROM {table} WITH (UPDLOCK, HOLDLOCK) "
        f"WHERE {key_column} = ?"
    )
    existing = cursor.execute(select_sql, record.key_value).fetchone()
    if existing is not None:
        if record.append_only:
            return "reused"
        if str(existing[0]) == str(record.values[record.checksum_column]):
            if not record.after_review or record.state_column is None:
                return "reused"
            if str(existing[1]) == str(record.values[record.state_column]):
                return "reused"
        if (
            record.expected_content_checksum is not None
            and str(existing[0]) != record.expected_content_checksum
        ):
            return "rejected"
        mutable_checks: list[bool] = []
        if record.state_column:
            state = str(existing[1])
            mutable_checks.append(state in record.mutable_states)
        if record.guard:
            guard_state = _fetch_state(cursor, record.guard)
            mutable_checks.append(guard_state in record.guard.mutable_states)
        mutable = bool(mutable_checks) and all(mutable_checks)
        if not mutable:
            return "rejected"
        update_columns = [column for column in record.values if column != record.key_column]
        update_sql = (
            f"UPDATE {table} SET "
            + ", ".join(f"{_quoted_identifier(column)} = ?" for column in update_columns)
            + f" WHERE {key_column} = ?"
        )
        cursor.execute(update_sql, *(record.values[column] for column in update_columns), record.key_value)
        return "updated_draft"
    if record.append_only and record.table == "kb.review_event":
        _assert_reviewed_content_is_current(cursor, record.values)
    values = dict(record.values)
    if record.append_only and record.table == "kb.review_event":
        previous = cursor.execute(
            "SELECT TOP (1) [review_event_id] FROM [kb].[review_event] "
            "WHERE [entity_type] = ? AND [entity_id] = ? AND [field_name] = ? "
            "ORDER BY [reviewed_at] DESC, [review_event_id] DESC",
            values["entity_type"],
            values["entity_id"],
            values["field_name"],
        ).fetchone()
        values["previous_event_id"] = None if previous is None else previous[0]
    columns = list(values)
    insert_sql = (
        f"INSERT INTO {table} ("
        + ", ".join(_quoted_identifier(column) for column in columns)
        + ") VALUES ("
        + ", ".join("?" for _ in columns)
        + ")"
    )
    cursor.execute(insert_sql, *(values[column] for column in columns))
    return "inserted"


def _reconciliation_json(
    counts: Mapping[str, ReconciliationCount],
    *,
    staging_row_count: int,
    projected_entity_count: int,
) -> str:
    return canonical_json_bytes({
        "staging_row_count": staging_row_count,
        "projected_entity_count": projected_entity_count,
        "entities": {entity: value.as_dict() for entity, value in sorted(counts.items())},
    }).decode("utf-8")


def _persist_failed_materialization(
    connection: Any,
    batch_id: str,
    counts: Mapping[str, ReconciliationCount],
    *,
    staging_row_count: int,
    projected_entity_count: int,
    error_code: str,
) -> None:
    try:
        connection.cursor().execute(
            "UPDATE [kb_stg].[import_batch] SET [status] = 'FAILED', [error_summary] = ?, "
            "[reconciliation_json] = ? WHERE [import_batch_id] = ?",
            error_code,
            _reconciliation_json(
                counts,
                staging_row_count=staging_row_count,
                projected_entity_count=projected_entity_count,
            ),
            batch_id,
        )
        connection.commit()
    except Exception:
        connection.rollback()


def materialize_staging(connection: Any, batch_id: str, *, kind: str) -> MaterializationResult:
    """将一个完整 VALIDATED 批次在单事务内投影到类型化 authoring 表。"""
    if kind not in {"eligibility", "regimen"}:
        raise ValueError(f"未知 workbook kind: {kind}")
    cursor = connection.cursor()
    counts: dict[str, ReconciliationCount] = {}
    rows: list[StagingRow] = []
    records: list[_TypedRecord] = []
    transaction_started = False
    try:
        batch = cursor.execute(
            "SELECT [status], [expected_row_count] FROM [kb_stg].[import_batch] "
            "WITH (UPDLOCK, HOLDLOCK) WHERE [import_batch_id] = ?",
            batch_id,
        ).fetchone()
        transaction_started = True
        if batch is None:
            raise KnowledgeDatabaseError("import batch 不存在")
        if str(batch[0]) != "VALIDATED":
            raise KnowledgeDatabaseError("只有 VALIDATED 批次可 materialize")
        staged = cursor.execute(
            "SELECT [sheet_name], [excel_row_no], [stable_row_id], [canonical_payload], "
            "[row_checksum] FROM [kb_stg].[import_row] WHERE [import_batch_id] = ? "
            "AND [validation_status] = 'VALID' ORDER BY [sheet_name], [excel_row_no]",
            batch_id,
        ).fetchall()
        rows = [
            StagingRow(
                sheet_name=str(item[0]),
                excel_row_no=int(item[1]),
                stable_row_id=str(item[2]),
                canonical_payload=json.loads(str(item[3])),
                row_checksum=str(item[4]),
            )
            for item in staged
        ]
        if len(rows) != int(batch[1]):
            raise KnowledgeDatabaseError("validated staging row count mismatch")
        records = _build_records(rows, batch_id=batch_id, kind=kind)
        _assert_review_targets_not_stale(cursor, records)
        for record in records:
            item = counts.setdefault(record.entity_type, ReconciliationCount())
            item.staged += 1
            action = _merge_typed_record(cursor, record)
            setattr(item, action, getattr(item, action) + 1)
            if action == "rejected":
                raise KnowledgeDatabaseError(
                    f"immutable authoring entity rejected: entity={record.entity_type!r}"
                )
        if any(item.staged != item.accounted or item.rejected for item in counts.values()):
            raise KnowledgeDatabaseError("staging→authoring reconciliation mismatch")
        reconciliation_json = _reconciliation_json(
            counts,
            staging_row_count=len(rows),
            projected_entity_count=len(records),
        )
        cursor.execute(
            "UPDATE [kb_stg].[import_batch] SET [status] = 'MATERIALIZED', "
            "[error_summary] = NULL, [reconciliation_json] = ? WHERE [import_batch_id] = ?",
            reconciliation_json,
            batch_id,
        )
        connection.commit()
    except Exception as exc:
        if transaction_started:
            connection.rollback()
        is_state_rejection = isinstance(exc, KnowledgeDatabaseError) and str(exc) in {
            "import batch 不存在",
            "只有 VALIDATED 批次可 materialize",
        }
        if not is_state_rejection and transaction_started:
            _persist_failed_materialization(
                connection,
                batch_id,
                counts,
                staging_row_count=len(rows),
                projected_entity_count=len(records),
                error_code=f"materialization_failed:{type(exc).__name__}",
            )
        if isinstance(exc, KnowledgeDatabaseError):
            raise
        raise KnowledgeDatabaseError(f"materialization failed: {type(exc).__name__}") from None
    return MaterializationResult(
        import_batch_id=batch_id,
        kind=kind,
        status="MATERIALIZED",
        staging_row_count=len(rows),
        projected_entity_count=len(records),
        reconciliation=counts,
    )


def apply_transaction(connection: Any, statements: Iterable[tuple[str, tuple[Any, ...]]]) -> None:
    """测试适配器和正式 materialization 共用的全有或全无执行边界。"""
    cursor = connection.cursor()
    try:
        for sql, params in statements:
            cursor.execute(sql, *params)
        connection.commit()
    except Exception as exc:
        connection.rollback()
        raise KnowledgeDatabaseError(f"知识事务失败: {type(exc).__name__}") from None


def safe_database_error(exc: BaseException, *, database: str) -> str:
    """异常只保留类别和安全目标，不回显连接 URL、密码或 SQL payload。"""
    return f"knowledge database operation failed: database={database!r} error={type(exc).__name__}"
