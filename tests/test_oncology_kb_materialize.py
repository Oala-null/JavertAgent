from __future__ import annotations

import copy
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from javert.oncology.authoring.ids import checksum, stable_id
from javert.oncology.authoring.sqlserver import KnowledgeDatabaseError, materialize_staging
from javert.oncology.authoring.staging import StagingRow, build_staging_rows
from javert.oncology.authoring.workbooks import EXPERT_COLUMNS, parse_workbook


ROOT = Path(__file__).resolve().parents[1]
ELIGIBILITY = ROOT / "outputs/add-oncology-kb-authoring/肿瘤药指南适应证与医保限定条件树KB.xlsx"
REGIMEN = ROOT / "outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx"
DDL = ROOT / "scripts/sql/create_oncology_kb_schema.sql"

_FROZEN_LIFECYCLES = frozenset({"APPROVED", "RELEASED", "RETIRED"})
_ACTIVE_LIFECYCLES = frozenset({"APPROVED", "RELEASED"})
_FOREIGN_KEYS = {
    "kb.source_fragment": (("source_document_id", "kb.source_document"),),
    "kb.drug_product": (("drug_concept_id", "kb.drug_concept"),),
    "kb.drug_code_xref": (("drug_product_id", "kb.drug_product"),),
    "kb.eligibility_rule_revision": (
        ("drug_concept_id", "kb.drug_concept"),
        ("source_fragment_id", "kb.source_fragment"),
        ("supersedes_revision_id", "kb.eligibility_rule_revision"),
    ),
    "kb.eligibility_branch": (
        ("rule_revision_id", "kb.eligibility_rule_revision"),
        ("source_fragment_id", "kb.source_fragment"),
    ),
    "kb.condition_node": (
        ("branch_id", "kb.eligibility_branch"),
        ("parent_node_id", "kb.condition_node"),
        ("source_fragment_id", "kb.source_fragment"),
    ),
    "kb.curated_knowledge_mapping": (("atom_id", "kb.curated_knowledge_atom"),),
    "kb.regimen_revision": (("supersedes_revision_id", "kb.regimen_revision"),),
    "kb.regimen_alias": (("regimen_revision_id", "kb.regimen_revision"),),
    "kb.regimen_context": (("regimen_revision_id", "kb.regimen_revision"),),
    "kb.regimen_component": (
        ("regimen_revision_id", "kb.regimen_revision"),
        ("source_fragment_id", "kb.source_fragment"),
    ),
    "kb.regimen_schedule_component": (
        ("regimen_revision_id", "kb.regimen_revision"),
        ("component_id", "kb.regimen_component"),
    ),
    "kb.review_event": (("previous_event_id", "kb.review_event"),),
}


class StorageConstraintError(RuntimeError):
    """严格测试适配器模拟的 SQL Server constraint/trigger 失败。"""


class RecordingCursor:
    def __init__(self, connection: "RecordingConnection") -> None:
        self.connection = connection
        self.result: list[tuple[Any, ...]] = []

    def execute(self, sql: str, *params: Any) -> "RecordingCursor":
        self.connection.executed.append((sql, params))
        normalized = " ".join(sql.split())
        if self.connection.fail_on and self.connection.fail_on in normalized:
            self.connection.fail_on = None
            raise RuntimeError("PWD=secret; /Users/private/clinical.xlsx; 病历原文")
        if "FROM [kb_stg].[import_batch]" in normalized and normalized.startswith("SELECT"):
            batch_id = str(params[0])
            batch = self.connection.batches.get(batch_id)
            self.result = [] if batch is None else [(batch["status"], len(batch["rows"]))]
            return self
        if "FROM [kb_stg].[import_row]" in normalized:
            batch = self.connection.batches[str(params[0])]
            self.result = [
                (
                    row.sheet_name,
                    row.excel_row_no,
                    row.stable_row_id,
                    json.dumps(row.canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    row.row_checksum,
                )
                for row in batch["rows"]
            ]
            return self
        if normalized.startswith("SELECT TOP (1) [review_event_id]"):
            entity_type, entity_id, field_name = map(str, params)
            events = [
                row for row in self.connection.tables.get("kb.review_event", {}).values()
                if row["entity_type"] == entity_type
                and row["entity_id"] == entity_id
                and row["field_name"] == field_name
            ]
            events.sort(key=lambda row: (str(row["reviewed_at"]), str(row["review_event_id"])), reverse=True)
            self.result = [] if not events else [(events[0]["review_event_id"],)]
            return self
        table_match = re.search(r"FROM \[kb\]\.\[([A-Za-z0-9_]+)\]", normalized)
        if normalized.startswith("SELECT") and table_match:
            table = f"kb.{table_match.group(1)}"
            key = params[0]
            existing = self.connection.tables.get(table, {}).get(key)
            if existing is None:
                self.result = []
                return self
            select_part = normalized.split(" FROM ", 1)[0].removeprefix("SELECT ")
            columns = re.findall(r"\[([A-Za-z0-9_]+)\]", select_part)
            self.result = [tuple(existing.get(column) for column in columns)]
            return self
        insert_match = re.match(r"INSERT INTO \[kb\]\.\[([A-Za-z0-9_]+)\] \((.*?)\) VALUES", normalized)
        if insert_match:
            table = f"kb.{insert_match.group(1)}"
            columns = re.findall(r"\[([A-Za-z0-9_]+)\]", insert_match.group(2))
            row = dict(zip(columns, params))
            key = params[0]
            self.connection.insert_row(table, key, row)
            self.result = []
            return self
        update_table = re.match(r"UPDATE \[kb\]\.\[([A-Za-z0-9_]+)\] SET (.*?) WHERE", normalized)
        if update_table:
            table = f"kb.{update_table.group(1)}"
            columns = re.findall(r"\[([A-Za-z0-9_]+)\] = \?", update_table.group(2))
            key = params[-1]
            self.connection.update_row(table, key, dict(zip(columns, params[:-1])))
            self.result = []
            return self
        if normalized.startswith("UPDATE [kb_stg].[import_batch]"):
            batch_id = str(params[-1])
            batch = self.connection.batches[batch_id]
            if "'MATERIALIZED'" in normalized:
                batch.update(status="MATERIALIZED", reconciliation_json=params[0], error_summary=None)
            elif "'FAILED'" in normalized:
                batch.update(status="FAILED", error_summary=params[0], reconciliation_json=params[1])
            self.result = []
            return self
        raise AssertionError(f"recording connection 不认识 SQL: {normalized}")

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.result)


class RecordingConnection:
    """执行真实 materializer SQL 的严格内存适配器，不声称替代 SQL Server 实机。"""

    def __init__(
        self,
        batch_id: str,
        rows: list[StagingRow],
        *,
        status: str = "VALIDATED",
        initial_tables: dict[str, dict[Any, dict[str, Any]]] | None = None,
        fail_on: str | None = None,
    ) -> None:
        self.batches = {
            batch_id: {
                "status": status,
                "rows": rows,
                "error_summary": None,
                "reconciliation_json": None,
            }
        }
        self.tables = copy.deepcopy(initial_tables or {})
        self.fail_on = fail_on
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0
        self.rollbacks = 0
        self._snapshot = self._state()

    def _state(self):
        return copy.deepcopy((self.batches, self.tables))

    def _assert_foreign_keys(self, table: str, row: dict[str, Any]) -> None:
        for column, parent_table in _FOREIGN_KEYS.get(table, ()):
            value = row.get(column)
            if value in (None, ""):
                continue
            if value not in self.tables.get(parent_table, {}):
                raise StorageConstraintError(
                    f"foreign key missing: {table}.{column}->{parent_table}"
                )
        if table == "kb.authoring_qa_issue":
            batch_id = row.get("import_batch_id")
            if batch_id not in self.batches:
                raise StorageConstraintError("authoring QA import batch missing")

    def _revision_for_child(self, table: str, row: dict[str, Any]) -> dict[str, Any] | None:
        if table == "kb.eligibility_branch":
            revision_id = row.get("rule_revision_id")
            return self.tables.get("kb.eligibility_rule_revision", {}).get(revision_id)
        if table == "kb.condition_node":
            branch = self.tables.get("kb.eligibility_branch", {}).get(row.get("branch_id"))
            if branch is None:
                return None
            return self.tables.get("kb.eligibility_rule_revision", {}).get(
                branch.get("rule_revision_id")
            )
        if table in {
            "kb.regimen_alias",
            "kb.regimen_context",
            "kb.regimen_component",
            "kb.regimen_schedule_component",
        }:
            revision_id = row.get("regimen_revision_id")
            if revision_id in (None, ""):
                return None
            return self.tables.get("kb.regimen_revision", {}).get(revision_id)
        return None

    def _assert_child_mutable(self, table: str, row: dict[str, Any]) -> None:
        parent = self._revision_for_child(table, row)
        if parent and str(parent.get("lifecycle")) in _FROZEN_LIFECYCLES:
            raise StorageConstraintError(f"frozen parent blocks child mutation: {table}")

    def _assert_revision_uniqueness_and_dates(
        self,
        table: str,
        key: Any,
        row: dict[str, Any],
    ) -> None:
        definitions = {
            "kb.eligibility_rule_revision": (
                "logical_rule_id",
                "rule_revision_id",
            ),
            "kb.regimen_revision": (
                "logical_regimen_id",
                "regimen_revision_id",
            ),
        }
        if table not in definitions:
            return
        logical_column, _ = definitions[table]
        for existing_key, existing in self.tables.get(table, {}).items():
            if existing_key == key:
                continue
            if (
                existing.get(logical_column) == row.get(logical_column)
                and existing.get("content_checksum") == row.get("content_checksum")
            ):
                raise StorageConstraintError("duplicate logical revision checksum")
            if (
                str(existing.get("lifecycle")) in _ACTIVE_LIFECYCLES
                and str(row.get("lifecycle")) in _ACTIVE_LIFECYCLES
                and existing.get(logical_column) == row.get(logical_column)
                and str(existing.get("effective_from")) <= str(row.get("effective_to"))
                and str(row.get("effective_from")) <= str(existing.get("effective_to"))
            ):
                raise StorageConstraintError("inclusive effective window overlap")

    def _assert_frozen_reference_mutable(self, table: str, key: Any) -> None:
        if table == "kb.source_fragment":
            eligibility_revisions = self.tables.get("kb.eligibility_rule_revision", {}).values()
            if any(
                row.get("source_fragment_id") == key
                and str(row.get("lifecycle")) in _FROZEN_LIFECYCLES
                for row in eligibility_revisions
            ):
                raise StorageConstraintError("frozen eligibility revision references source")
            components = self.tables.get("kb.regimen_component", {}).values()
            revisions = self.tables.get("kb.regimen_revision", {})
            if any(
                row.get("source_fragment_id") == key
                and str(revisions.get(row.get("regimen_revision_id"), {}).get("lifecycle"))
                in _FROZEN_LIFECYCLES
                for row in components
            ):
                raise StorageConstraintError("frozen regimen revision references source")
        if table == "kb.drug_concept":
            eligibility_revisions = self.tables.get("kb.eligibility_rule_revision", {}).values()
            if any(
                row.get("drug_concept_id") == key
                and str(row.get("lifecycle")) in _FROZEN_LIFECYCLES
                for row in eligibility_revisions
            ):
                raise StorageConstraintError("frozen eligibility revision references concept")
            components = self.tables.get("kb.regimen_component", {}).values()
            revisions = self.tables.get("kb.regimen_revision", {})
            if any(
                row.get("target_kind") == "CONCEPT"
                and row.get("target_id") == key
                and str(revisions.get(row.get("regimen_revision_id"), {}).get("lifecycle"))
                in _FROZEN_LIFECYCLES
                for row in components
            ):
                raise StorageConstraintError("frozen regimen revision references concept")

    def insert_row(self, table: str, key: Any, row: dict[str, Any]) -> None:
        if key in self.tables.get(table, {}):
            raise StorageConstraintError(f"duplicate primary key: {table}")
        self._assert_foreign_keys(table, row)
        self._assert_child_mutable(table, row)
        self._assert_revision_uniqueness_and_dates(table, key, row)
        self.tables.setdefault(table, {})[key] = copy.deepcopy(row)

    def update_row(self, table: str, key: Any, updates: dict[str, Any]) -> None:
        old = self.tables[table][key]
        self._assert_child_mutable(table, old)
        self._assert_frozen_reference_mutable(table, key)
        if (
            table in {"kb.eligibility_rule_revision", "kb.regimen_revision"}
            and str(old.get("lifecycle")) in _FROZEN_LIFECYCLES
            and any(column != "lifecycle" for column in updates)
        ):
            raise StorageConstraintError("frozen revision content is immutable")
        new = {**old, **updates}
        self._assert_foreign_keys(table, new)
        self._assert_child_mutable(table, new)
        self._assert_revision_uniqueness_and_dates(table, key, new)
        old.update(copy.deepcopy(updates))

    def delete_row(self, table: str, key: Any) -> None:
        row = self.tables[table][key]
        self._assert_child_mutable(table, row)
        self._assert_frozen_reference_mutable(table, key)
        if (
            table in {"kb.eligibility_rule_revision", "kb.regimen_revision"}
            and str(row.get("lifecycle")) in _FROZEN_LIFECYCLES
        ):
            raise StorageConstraintError("frozen revision cannot be deleted")
        for child_table, constraints in _FOREIGN_KEYS.items():
            for column, parent_table in constraints:
                if parent_table == table and any(
                    child.get(column) == key
                    for child in self.tables.get(child_table, {}).values()
                ):
                    raise StorageConstraintError("foreign key blocks parent delete")
        del self.tables[table][key]

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self)

    def commit(self) -> None:
        self.commits += 1
        self._snapshot = self._state()

    def rollback(self) -> None:
        self.rollbacks += 1
        self.batches, self.tables = copy.deepcopy(self._snapshot)


def workbook_rows(path: Path, kind: str) -> list[StagingRow]:
    return build_staging_rows(parse_workbook(path, kind=kind))


def _with_expert_values(row: StagingRow, **values: Any) -> StagingRow:
    payload = {**row.canonical_payload, **values}
    return replace(
        row,
        canonical_payload=payload,
        row_checksum=(
            row.row_checksum
            if payload.get("row_checksum")
            else checksum(payload)
        ),
    )


@pytest.mark.parametrize(
    ("path", "kind", "expected_tables"),
    [
        (
            ELIGIBILITY,
            "eligibility",
            {
                "kb.source_document",
                "kb.source_fragment",
                "kb.drug_concept",
                "kb.drug_product",
                "kb.drug_code_xref",
                "kb.eligibility_rule_revision",
                "kb.eligibility_branch",
                "kb.condition_node",
                "kb.curated_knowledge_atom",
                "kb.curated_knowledge_mapping",
                "kb.term_dictionary_entry",
                "kb.authoring_qa_issue",
            },
        ),
        (
            REGIMEN,
            "regimen",
            {
                "kb.regimen_revision",
                "kb.regimen_alias",
                "kb.regimen_context",
                "kb.regimen_component",
                "kb.regimen_schedule_component",
                "kb.authoring_qa_issue",
            },
        ),
    ],
)
def test_real_workbook_materializes_every_sheet_to_typed_tables(
    path: Path,
    kind: str,
    expected_tables: set[str],
) -> None:
    rows = workbook_rows(path, kind)
    connection = RecordingConnection(f"batch-{kind}", rows)
    result = materialize_staging(connection, f"batch-{kind}", kind=kind)

    assert result.status == "MATERIALIZED"
    assert result.staging_row_count == len(rows)
    assert result.projected_entity_count >= len(rows)
    assert expected_tables <= set(connection.tables)
    assert all(value.staged == value.accounted and value.rejected == 0 for value in result.reconciliation.values())
    assert connection.batches[f"batch-{kind}"]["status"] == "MATERIALIZED"
    report = json.loads(connection.batches[f"batch-{kind}"]["reconciliation_json"])
    assert report["staging_row_count"] == len(rows)
    assert report["projected_entity_count"] == result.projected_entity_count
    assert connection.commits == 1 and connection.rollbacks == 0
    assert "kb.knowledge_release" not in connection.tables
    assert "kb.release_item" not in connection.tables
    assert "kb_meta.deployment_pointer" not in connection.tables
    if kind == "regimen":
        assert {row["lifecycle"] for row in connection.tables["kb.regimen_revision"].values()} == {"DRAFT"}
    assert "kb.review_event" not in connection.tables  # 空白审核行不产生事件
    sql_text = "\n".join(sql for sql, _ in connection.executed)
    assert "病历原文" not in sql_text
    assert any(params for _, params in connection.executed)


def test_curated_authority_fields_materialize_to_typed_columns_and_checksum() -> None:
    preservation = next(
        row
        for row in workbook_rows(ELIGIBILITY, "eligibility")
        if row.sheet_name == "09_肿瘤知识保全"
    )
    first = RecordingConnection("batch-curated-first", [preservation])
    materialize_staging(first, "batch-curated-first", kind="eligibility")
    stored = first.tables["kb.curated_knowledge_atom"][preservation.stable_row_id]
    assert type(stored["oncology"]) is bool
    assert stored["oncology"] is preservation.canonical_payload["oncology"]
    assert stored["rule_status"] == preservation.canonical_payload["rule_status"]

    changed = copy.deepcopy(preservation)
    changed.canonical_payload["oncology"] = not changed.canonical_payload["oncology"]
    machine = {
        key: value
        for key, value in changed.canonical_payload.items()
        if key not in EXPERT_COLUMNS and key != "row_checksum"
    }
    changed.row_checksum = checksum(machine)
    changed.canonical_payload["row_checksum"] = changed.row_checksum
    second = RecordingConnection("batch-curated-changed", [changed])
    materialize_staging(second, "batch-curated-changed", kind="eligibility")
    changed_stored = second.tables["kb.curated_knowledge_atom"][changed.stable_row_id]
    assert changed_stored["content_checksum"] != stored["content_checksum"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("oncology", "", "oncology 必须为布尔值"),
        ("rule_status", "", "rule_status 非法"),
    ],
)
def test_curated_materializer_rejects_missing_authority_fields(
    field: str,
    value: Any,
    message: str,
) -> None:
    preservation = next(
        row
        for row in workbook_rows(ELIGIBILITY, "eligibility")
        if row.sheet_name == "09_肿瘤知识保全"
    )
    changed = copy.deepcopy(preservation)
    changed.canonical_payload[field] = value
    machine = {
        key: item
        for key, item in changed.canonical_payload.items()
        if key not in EXPERT_COLUMNS and key != "row_checksum"
    }
    changed.row_checksum = checksum(machine)
    changed.canonical_payload["row_checksum"] = changed.row_checksum
    connection = RecordingConnection(f"batch-curated-missing-{field}", [changed])
    with pytest.raises(KnowledgeDatabaseError, match=message):
        materialize_staging(
            connection,
            f"batch-curated-missing-{field}",
            kind="eligibility",
        )
    assert connection.tables == {}
    assert connection.rollbacks == 1


def test_only_validated_batch_is_accepted_without_changing_other_states() -> None:
    rows = workbook_rows(REGIMEN, "regimen")[:1]
    connection = RecordingConnection("batch-uploaded", rows, status="UPLOADED")
    with pytest.raises(KnowledgeDatabaseError, match="VALIDATED"):
        materialize_staging(connection, "batch-uploaded", kind="regimen")
    assert connection.batches["batch-uploaded"]["status"] == "UPLOADED"
    assert connection.tables == {}
    assert connection.commits == 0 and connection.rollbacks == 1


def test_mid_transaction_error_rolls_back_formal_writes_and_preserves_failed_batch() -> None:
    rows = workbook_rows(ELIGIBILITY, "eligibility")
    connection = RecordingConnection(
        "batch-fail",
        rows,
        fail_on="INSERT INTO [kb].[condition_node]",
    )
    with pytest.raises(KnowledgeDatabaseError, match="RuntimeError") as error:
        materialize_staging(connection, "batch-fail", kind="eligibility")
    assert "secret" not in str(error.value) and "/Users/" not in str(error.value)
    assert connection.tables == {}
    assert connection.batches["batch-fail"]["status"] == "FAILED"
    assert connection.batches["batch-fail"]["error_summary"] == "materialization_failed:RuntimeError"
    report = json.loads(connection.batches["batch-fail"]["reconciliation_json"])
    assert report["entities"]["source_document"]["inserted"] > 0
    assert connection.rollbacks == 1 and connection.commits == 1


def test_staging_checksum_mismatch_fails_before_any_formal_write() -> None:
    source = next(
        row for row in workbook_rows(REGIMEN, "regimen")
        if row.sheet_name == "02_方案主表"
    )
    corrupted = copy.deepcopy(source)
    corrupted.canonical_payload["canonical_name"] = "被篡改但未重算 checksum"
    connection = RecordingConnection("batch-checksum", [corrupted])
    with pytest.raises(KnowledgeDatabaseError, match="checksum mismatch"):
        materialize_staging(connection, "batch-checksum", kind="regimen")
    assert connection.tables == {}
    assert connection.batches["batch-checksum"]["status"] == "FAILED"


def test_draft_can_update_but_approved_revision_is_immutable() -> None:
    revision_row = next(
        row for row in workbook_rows(REGIMEN, "regimen")
        if row.sheet_name == "02_方案主表"
    )
    revision_id = revision_row.canonical_payload["regimen_revision_id"]
    old = {
        "regimen_revision_id": revision_id,
        "logical_regimen_id": "old",
        "canonical_name": "old",
        "lifecycle": "DRAFT",
        "content_checksum": "sha256:" + "0" * 64,
    }
    draft_connection = RecordingConnection(
        "batch-draft",
        [revision_row],
        initial_tables={"kb.regimen_revision": {revision_id: old}},
    )
    draft = materialize_staging(draft_connection, "batch-draft", kind="regimen")
    assert draft.reconciliation["regimen_revision"].updated_draft == 1
    assert draft_connection.tables["kb.regimen_revision"][revision_id]["canonical_name"] != "old"

    approved = {**old, "lifecycle": "APPROVED"}
    approved_connection = RecordingConnection(
        "batch-approved",
        [revision_row],
        initial_tables={"kb.regimen_revision": {revision_id: approved}},
    )
    with pytest.raises(KnowledgeDatabaseError, match="immutable"):
        materialize_staging(approved_connection, "batch-approved", kind="regimen")
    assert approved_connection.tables["kb.regimen_revision"][revision_id] == approved
    assert approved_connection.batches["batch-approved"]["status"] == "FAILED"


def test_checksum_match_reuses_entity_and_does_not_duplicate_revision() -> None:
    rows = workbook_rows(REGIMEN, "regimen")
    first_connection = RecordingConnection("batch-first", rows)
    first = materialize_staging(first_connection, "batch-first", kind="regimen")
    formal_snapshot = copy.deepcopy(first_connection.tables)

    second_connection = RecordingConnection(
        "batch-second",
        rows,
        initial_tables=formal_snapshot,
    )
    second = materialize_staging(second_connection, "batch-second", kind="regimen")
    assert second.reconciliation["regimen_revision"].inserted == 0
    assert second.reconciliation["regimen_revision"].reused == first.reconciliation["regimen_revision"].staged
    assert len(second_connection.tables["kb.regimen_revision"]) == len(formal_snapshot["kb.regimen_revision"])


def test_review_event_is_appended_only_when_decision_exists_and_does_not_approve() -> None:
    rows = workbook_rows(REGIMEN, "regimen")
    source = next(
        row for row in rows
        if row.sheet_name == "07_专家审核"
        and row.canonical_payload["entity_type"] == "regimen"
    )
    target = next(
        row for row in rows
        if row.sheet_name == "02_方案主表"
        and row.canonical_payload["regimen_revision_id"]
        == source.canonical_payload["entity_id"]
    )
    payload = dict(source.canonical_payload)
    payload.update(
        review_decision="APPROVE",
        expert_comment="结构化核对通过",
        reviewer_id="expert-a",
        reviewed_at="2026-07-21T12:00:00",
    )
    reviewed = StagingRow(
        sheet_name=source.sheet_name,
        excel_row_no=source.excel_row_no,
        stable_row_id=source.stable_row_id,
        canonical_payload=payload,
        row_checksum=checksum(payload),
    )
    connection = RecordingConnection("batch-review", [target, reviewed])
    result = materialize_staging(connection, "batch-review", kind="regimen")
    assert result.reconciliation["review_event"].inserted == 1
    event = next(iter(connection.tables["kb.review_event"].values()))
    assert event["decision"] == "APPROVE"
    assert event["reviewed_content_checksum"] == source.canonical_payload["source_checksum"]
    assert event["reviewed_content_checksum"] == next(
        iter(connection.tables["kb.regimen_revision"].values())
    )["content_checksum"]
    assert event["review_event_id"] == stable_id(
        "review",
        event["entity_type"],
        event["entity_id"],
        event["field_name"],
        event["reviewed_content_checksum"],
        event["decision"],
        event["expert_value_json"],
        event["comment"],
        event["evidence_reference"],
        event["reviewer_id"],
        event["reviewed_at"],
    )
    assert event["previous_event_id"] is None
    assert "kb.knowledge_release" not in connection.tables


def test_approve_with_edit_creates_complete_superseding_eligibility_graph() -> None:
    rows = workbook_rows(ELIGIBILITY, "eligibility")
    review = next(
        row
        for row in rows
        if row.sheet_name == "06_专家审核"
        and row.canonical_payload["entity_type"] == "eligibility_revision"
    )
    original_id = str(review.canonical_payload["entity_id"])
    patch = {
        "effective_from": "2026-02-01",
        "effective_to": "2027-11-30",
        "effective_date_basis": "EXPERT_OVERRIDE",
        "date_override_reason": "专家核对来源版本",
        "date_review_comment": "按受控证据调整声明窗口",
    }
    edited = _with_expert_values(
        review,
        review_decision="APPROVE_WITH_EDIT",
        expert_value=json.dumps(patch, ensure_ascii=False),
        expert_comment="同意修订后复核",
        evidence_reference="case:effective-window",
        reviewer_id="expert-a",
        reviewed_at="2026-07-21T12:00:00+08:00",
    )
    batch_rows = [edited if row is review else row for row in rows]
    connection = RecordingConnection("batch-effective-edit", batch_rows)
    materialize_staging(connection, "batch-effective-edit", kind="eligibility")

    revisions = connection.tables["kb.eligibility_rule_revision"]
    original_revision_count = len({
        str(row.canonical_payload["rule_revision_id"])
        for row in rows
        if row.sheet_name == "04_适应证分支"
    })
    assert len(revisions) == original_revision_count + 1
    superseding = next(
        value
        for value in revisions.values()
        if value.get("supersedes_revision_id") == original_id
    )
    new_id = str(superseding["rule_revision_id"])
    assert superseding["lifecycle"] == "DRAFT"
    assert superseding["effective_from"] == "2026-02-01"
    assert superseding["effective_to"] == "2027-11-30"
    assert revisions[original_id]["effective_from"] == "2026-01-01"
    old_branches = [
        row for row in connection.tables["kb.eligibility_branch"].values()
        if row["rule_revision_id"] == original_id
    ]
    new_branches = [
        row for row in connection.tables["kb.eligibility_branch"].values()
        if row["rule_revision_id"] == new_id
    ]
    assert len(new_branches) == len(old_branches) and new_branches
    new_branch_ids = {row["branch_id"] for row in new_branches}
    old_branch_ids = {row["branch_id"] for row in old_branches}
    nodes = connection.tables["kb.condition_node"].values()
    assert sum(row["branch_id"] in new_branch_ids for row in nodes) == sum(
        row["branch_id"] in old_branch_ids for row in nodes
    )
    assert all(row["disposition"] == "in_review" for row in new_branches)


def test_curated_mapping_requires_edit_then_fresh_approval() -> None:
    rows = workbook_rows(ELIGIBILITY, "eligibility")
    atom = next(
        row
        for row in rows
        if row.sheet_name == "09_肿瘤知识保全"
        and row.canonical_payload["target_kind"] == "EVALUATOR_POLICY"
    )
    atom_id = str(atom.canonical_payload["atom_id"])
    corrected_target = "evaluator-policy_corrected_synthetic"
    common = {
        "expert_target_kind": "EVALUATOR_POLICY",
        "expert_target_id": corrected_target,
        "expert_verification_evidence": "policy:synthetic-reviewed",
        "expert_comment": "核对保全目标",
        "evidence_reference": "policy:synthetic-reviewed",
        "reviewer_id": "expert-a",
    }
    first_review = _with_expert_values(
        atom,
        **common,
        review_decision="APPROVE_WITH_EDIT",
        expert_migration_status="MAPPED",
        reviewed_at="2026-07-21T12:00:00+08:00",
    )
    first_rows = [first_review if row is atom else row for row in rows]
    first = RecordingConnection("batch-curated-edit", first_rows)
    materialize_staging(first, "batch-curated-edit", kind="eligibility")
    stored_atom = first.tables["kb.curated_knowledge_atom"][atom_id]
    stored_mapping = next(
        row
        for row in first.tables["kb.curated_knowledge_mapping"].values()
        if row["atom_id"] == atom_id
    )
    assert stored_atom["migration_status"] == "MAPPED"
    assert stored_mapping["target_id"] == corrected_target

    second_review = _with_expert_values(
        atom,
        **common,
        review_decision="APPROVE",
        expert_migration_status="VERIFIED",
        reviewed_at="2026-07-21T13:00:00+08:00",
    )
    second_rows = [second_review if row is atom else row for row in rows]
    second = RecordingConnection(
        "batch-curated-approve",
        second_rows,
        initial_tables=first.tables,
    )
    materialize_staging(second, "batch-curated-approve", kind="eligibility")
    assert second.tables["kb.curated_knowledge_atom"][atom_id]["migration_status"] == "VERIFIED"
    assert next(
        row
        for row in second.tables["kb.curated_knowledge_mapping"].values()
        if row["atom_id"] == atom_id
    )["target_id"] == corrected_target
    events = [
        row
        for row in second.tables["kb.review_event"].values()
        if row["entity_type"] == "curated_knowledge_atom" and row["entity_id"] == atom_id
    ]
    assert {row["decision"] for row in events} == {"APPROVE_WITH_EDIT", "APPROVE"}


def test_review_event_rejects_stale_content_checksum_before_formal_write() -> None:
    rows = workbook_rows(REGIMEN, "regimen")
    source = next(
        row for row in rows
        if row.sheet_name == "07_专家审核"
        and row.canonical_payload["entity_type"] == "regimen"
    )
    target = next(
        row for row in rows
        if row.sheet_name == "02_方案主表"
        and row.canonical_payload["regimen_revision_id"]
        == source.canonical_payload["entity_id"]
    )
    payload = {
        **source.canonical_payload,
        "source_checksum": "sha256:" + "0" * 64,
        "review_decision": "APPROVE",
        "expert_comment": "结构化核对通过",
        "reviewer_id": "expert-a",
        "reviewed_at": "2026-07-21T12:00:00",
    }
    reviewed = StagingRow(
        sheet_name=source.sheet_name,
        excel_row_no=source.excel_row_no,
        stable_row_id=source.stable_row_id,
        canonical_payload=payload,
        row_checksum=checksum(payload),
    )
    connection = RecordingConnection("batch-stale-review", [target, reviewed])
    with pytest.raises(KnowledgeDatabaseError, match="checksum"):
        materialize_staging(connection, "batch-stale-review", kind="regimen")
    assert connection.tables == {}
    assert connection.batches["batch-stale-review"]["status"] == "FAILED"


@pytest.mark.parametrize(
    ("path", "kind", "review_sheet", "locators"),
    [
        (
            ELIGIBILITY,
            "eligibility",
            "06_专家审核",
                {
                    "eligibility_revision": ("kb.eligibility_rule_revision", "rule_revision_id"),
                    "branch": ("kb.eligibility_branch", "branch_id"),
                    "condition_node": ("kb.condition_node", "node_id"),
                    "drug_class": ("kb.drug_class", "drug_class_id"),
                },
        ),
        (
            REGIMEN,
            "regimen",
            "07_专家审核",
            {
                "regimen": ("kb.regimen_revision", "regimen_revision_id"),
                "alias": ("kb.regimen_alias", "alias_id"),
                "context": ("kb.regimen_context", "context_id"),
                "component": ("kb.regimen_component", "component_id"),
            },
        ),
    ],
)
def test_generated_review_rows_pin_exact_typed_entity_checksums(
    path: Path,
    kind: str,
    review_sheet: str,
    locators: dict[str, tuple[str, str]],
) -> None:
    rows = workbook_rows(path, kind)
    connection = RecordingConnection(f"batch-review-bindings-{kind}", rows)
    materialize_staging(connection, f"batch-review-bindings-{kind}", kind=kind)
    seen: set[str] = set()
    for review in (row for row in rows if row.sheet_name == review_sheet):
        entity_type = str(review.canonical_payload["entity_type"])
        table, _ = locators[entity_type]
        stored = connection.tables[table][review.canonical_payload["entity_id"]]
        assert review.canonical_payload["source_checksum"] == stored["content_checksum"]
        seen.add(entity_type)
    assert seen == set(locators)


def test_storage_adapter_rejects_child_insert_for_approved_regimen_and_rolls_back() -> None:
    alias = next(
        row for row in workbook_rows(REGIMEN, "regimen")
        if row.sheet_name == "03_方案别名" and row.canonical_payload.get("regimen_revision_id")
    )
    revision_id = str(alias.canonical_payload["regimen_revision_id"])
    frozen_parent = {
        "regimen_revision_id": revision_id,
        "logical_regimen_id": "frozen-regimen",
        "canonical_name": "冻结方案",
        "lifecycle": "APPROVED",
        "effective_from": "2026-01-01",
        "effective_to": "2027-12-31",
        "content_checksum": "sha256:" + "1" * 64,
    }
    connection = RecordingConnection(
        "batch-frozen-child",
        [alias],
        initial_tables={"kb.regimen_revision": {revision_id: frozen_parent}},
    )
    with pytest.raises(KnowledgeDatabaseError):
        materialize_staging(connection, "batch-frozen-child", kind="regimen")
    assert "kb.regimen_alias" not in connection.tables
    assert connection.tables["kb.regimen_revision"][revision_id] == frozen_parent
    assert connection.batches["batch-frozen-child"]["status"] == "FAILED"


def test_storage_adapter_rejects_missing_foreign_key_and_rolls_back() -> None:
    context = next(
        row for row in workbook_rows(REGIMEN, "regimen")
        if row.sheet_name == "04_方案上下文"
    )
    connection = RecordingConnection("batch-missing-fk", [context])
    with pytest.raises(KnowledgeDatabaseError):
        materialize_staging(connection, "batch-missing-fk", kind="regimen")
    assert connection.tables == {}
    assert connection.batches["batch-missing-fk"]["status"] == "FAILED"


def test_storage_adapter_enforces_duplicate_checksum_and_inclusive_overlap() -> None:
    revision_row = next(
        row for row in workbook_rows(REGIMEN, "regimen")
        if row.sheet_name == "02_方案主表"
    )
    first_connection = RecordingConnection("batch-first-revision", [revision_row])
    materialize_staging(first_connection, "batch-first-revision", kind="regimen")
    incoming_id = str(revision_row.canonical_payload["regimen_revision_id"])
    incoming = first_connection.tables["kb.regimen_revision"][incoming_id]
    duplicate = {
        **incoming,
        "regimen_revision_id": "rev-duplicate-checksum",
    }
    duplicate_connection = RecordingConnection(
        "batch-duplicate-checksum",
        [revision_row],
        initial_tables={
            "kb.regimen_revision": {"rev-duplicate-checksum": duplicate}
        },
    )
    with pytest.raises(KnowledgeDatabaseError):
        materialize_staging(
            duplicate_connection,
            "batch-duplicate-checksum",
            kind="regimen",
        )
    assert set(duplicate_connection.tables["kb.regimen_revision"]) == {
        "rev-duplicate-checksum"
    }

    overlap_connection = RecordingConnection("batch-overlap", [])
    first = {
        "regimen_revision_id": "rev-overlap-a",
        "logical_regimen_id": "regimen-overlap",
        "lifecycle": "APPROVED",
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
        "content_checksum": "sha256:" + "2" * 64,
        "supersedes_revision_id": None,
    }
    second = {
        **first,
        "regimen_revision_id": "rev-overlap-b",
        "effective_from": "2026-12-31",
        "effective_to": "2027-12-31",
        "content_checksum": "sha256:" + "3" * 64,
    }
    overlap_connection.insert_row(
        "kb.regimen_revision", first["regimen_revision_id"], first
    )
    with pytest.raises(StorageConstraintError, match="inclusive"):
        overlap_connection.insert_row(
            "kb.regimen_revision", second["regimen_revision_id"], second
        )


def test_storage_adapter_blocks_frozen_child_update_delete_and_reference_mutation() -> None:
    revision_id = "rev-frozen"
    parent = {
        "regimen_revision_id": revision_id,
        "logical_regimen_id": "regimen-frozen",
        "lifecycle": "RELEASED",
        "effective_from": "2026-01-01",
        "effective_to": "2027-12-31",
        "content_checksum": "sha256:" + "4" * 64,
    }
    alias = {
        "alias_id": "alias-frozen",
        "regimen_revision_id": revision_id,
        "normalized_alias": "FROZEN",
        "content_checksum": "sha256:" + "5" * 64,
    }
    connection = RecordingConnection(
        "batch-frozen-mutations",
        [],
        initial_tables={
            "kb.regimen_revision": {revision_id: parent},
            "kb.regimen_alias": {"alias-frozen": alias},
        },
    )
    with pytest.raises(StorageConstraintError, match="frozen parent"):
        connection.update_row(
            "kb.regimen_alias", "alias-frozen", {"normalized_alias": "CHANGED"}
        )
    with pytest.raises(StorageConstraintError, match="frozen parent"):
        connection.delete_row("kb.regimen_alias", "alias-frozen")

    source_id = "source-frozen"
    concept_id = "concept-frozen"
    rule_id = "rule-frozen"
    referenced = RecordingConnection(
        "batch-frozen-references",
        [],
        initial_tables={
            "kb.source_fragment": {
                source_id: {
                    "source_fragment_id": source_id,
                    "content_checksum": "sha256:" + "6" * 64,
                }
            },
            "kb.drug_concept": {
                concept_id: {
                    "drug_concept_id": concept_id,
                    "canonical_name": "冻结概念",
                    "lifecycle": "DRAFT",
                    "content_checksum": "sha256:" + "7" * 64,
                }
            },
            "kb.eligibility_rule_revision": {
                rule_id: {
                    "rule_revision_id": rule_id,
                    "source_fragment_id": source_id,
                    "drug_concept_id": concept_id,
                    "lifecycle": "RETIRED",
                    "content_checksum": "sha256:" + "8" * 64,
                }
            },
        },
    )
    with pytest.raises(StorageConstraintError, match="references source"):
        referenced.update_row(
            "kb.source_fragment",
            source_id,
            {"content_checksum": "sha256:" + "9" * 64},
        )
    with pytest.raises(StorageConstraintError, match="references concept"):
        referenced.delete_row("kb.drug_concept", concept_id)


def test_ddl_has_materialization_checksums_support_tables_and_reconciliation_column() -> None:
    sql = DDL.read_text(encoding="utf-8")
    assert "[reconciliation_json]" in sql
    assert "[term_dictionary_entry]" in sql
    assert "[authoring_qa_issue]" in sql
    curated = re.search(
        r"CREATE TABLE \[kb\]\.\[curated_knowledge_atom\] \((.*?)\n    \);",
        sql,
        re.S,
    )
    assert curated
    assert "[oncology] bit NOT NULL" in curated.group(1)
    assert "[rule_status] varchar(16) NOT NULL" in curated.group(1)
    assert "CK_kb_atom_rule_status" in curated.group(1)
    assert "COL_LENGTH(N'kb.curated_knowledge_atom', N'oncology')" in sql
    assert "COL_LENGTH(N'kb.curated_knowledge_atom', N'rule_status')" in sql
    assert "WHERE [oncology] IS NULL OR [rule_status] IS NULL" in sql
    assert "禁止自动回填 authority 字段" in sql
    assert "ALTER COLUMN [oncology] bit NOT NULL" in sql
    assert "ALTER COLUMN [rule_status] varchar(16) NOT NULL" in sql
    assert "ALTER TABLE [kb].[curated_knowledge_atom] WITH CHECK" in sql
    for table in (
        "drug_concept",
        "drug_product",
        "drug_code_xref",
        "eligibility_branch",
        "condition_node",
        "regimen_alias",
        "regimen_context",
        "regimen_component",
        "regimen_schedule_component",
    ):
        block = re.search(rf"CREATE TABLE \[kb\]\.\[{table}\] \((.*?)\n    \);", sql, re.S)
        assert block and "[content_checksum]" in block.group(1)
