"""SQL Server authoring 状态到不可变 oncology release 的受控投影。

本模块不接受调用方提供来源全集或 ``ReleaseRevision``。所有发布输入均由同一
事务内读取的 typed authoring 表、append-only review event 和显式 legacy
pathology bootstrap 推导，随后交给 :mod:`release` 的纯逻辑门禁。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from javert.oncology.knowledge import SourceReference, asset_payload_checksum
from javert.oncology.pathology import PathologyKnowledgeAsset

from .ids import checksum, stable_id
from .release import (
    DRUG_ASSET,
    ELIGIBILITY_ASSET,
    PATHOLOGY_ASSET,
    REGIMEN_ASSET,
    CoverageItem,
    CuratedAtomCoverage,
    ReleaseCandidate,
    ReleaseRevision,
    activate_release,
    build_release_authority_snapshot,
    build_release_candidate,
    compile_release,
    load_release_bundle,
    publish_candidate,
    resolve_active_release_assets,
    rollback_release,
    validate_compiled_release,
    write_release_bundle,
)


_APPROVAL_DECISIONS = frozenset({"APPROVE"})
_PUBLISHABLE_LIFECYCLES = frozenset({"APPROVED", "RELEASED"})
_APPROVABLE_LIFECYCLES = frozenset({"DRAFT", "IN_REVIEW"})
_BOOTSTRAP_REVIEWER = "bootstrap:legacy-approved-pathology"
_REGIMEN_TARGET_KINDS = frozenset({"CONCEPT", "CLASS"})
_REGIMEN_REQUIREMENTS = frozenset({"REQUIRED", "OPTIONAL", "WITH_OR_WITHOUT"})


class ReleaseStoreError(RuntimeError):
    """只携带稳定错误码；不得拼入 driver、路径、principal 或业务原文。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ApprovalResult:
    entity_type: str
    revision_id: str
    lifecycle: str
    reused: bool


@dataclass(frozen=True)
class ReleaseOperationResult:
    release_id: str
    status: str
    item_count: int
    reused: bool = False


@dataclass(frozen=True)
class ReleaseAuthorityInspection:
    source_authority_checksum: str
    curated_authority_checksum: str
    pathology_bootstrap_checksum: str
    source_item_count: int
    curated_atom_count: int


@dataclass(frozen=True)
class _Projection:
    candidate: ReleaseCandidate
    reviewer_ids: frozenset[str]
    domain_reviewer_id: str
    parent_revisions: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class _PathologyBootstrap:
    raw: Mapping[str, Any]
    checksum: str
    approved_entries: tuple[Mapping[str, Any], ...]
    pending_entries: tuple[Mapping[str, Any], ...]


class ReleaseStoreAdapter(Protocol):
    """发布状态存储的最小事务协议。"""

    def load_authoring_tables(self, *, lock: bool) -> Mapping[str, Sequence[Mapping[str, Any]]]: ...

    def get_release(self, release_id: str, *, lock: bool) -> Mapping[str, Any] | None: ...

    def get_release_items(self, release_id: str, *, lock: bool) -> Sequence[Mapping[str, Any]]: ...

    def get_pointer(self, *, lock: bool) -> Mapping[str, Any] | None: ...

    def set_revision_lifecycle(
        self,
        entity_type: str,
        revision_id: str,
        *,
        expected: Sequence[str],
        lifecycle: str,
    ) -> bool: ...

    def insert_release(self, row: Mapping[str, Any]) -> None: ...

    def insert_release_items(self, rows: Sequence[Mapping[str, Any]]) -> None: ...

    def update_release(self, release_id: str, values: Mapping[str, Any]) -> None: ...

    def set_pointer(self, active_release_id: str, previous_release_id: str | None, actor: str) -> None: ...

    def restore_pointer(self, row: Mapping[str, Any]) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


_TABLE_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "source_document": (
        "source_document_id",
        (
            "source_document_id",
            "source_type",
            "title",
            "document_version",
            "document_year",
            "retrieval_date",
            "content_checksum",
        ),
    ),
    "source_fragment": (
        "source_fragment_id",
        (
            "source_fragment_id",
            "source_document_id",
            "anchor",
            "page_numbers_json",
            "original_text",
            "content_checksum",
        ),
    ),
    "drug_concept": (
        "drug_concept_id",
        (
            "drug_concept_id",
            "canonical_name",
            "normalized_name",
            "lifecycle",
            "content_checksum",
        ),
    ),
    "drug_class": (
        "drug_class_id",
        (
            "drug_class_id",
            "canonical_name",
            "match_terms_json",
            "lifecycle",
            "content_checksum",
        ),
    ),
    "drug_product": (
        "drug_product_id",
        (
            "drug_product_id",
            "drug_concept_id",
            "product_name",
            "dosage_form",
            "manufacturer",
            "source_kind",
            "content_checksum",
        ),
    ),
    "drug_code_xref": (
        "drug_code_xref_id",
        (
            "drug_code_xref_id",
            "drug_product_id",
            "code_system",
            "code_value",
            "content_checksum",
        ),
    ),
    "eligibility_rule_revision": (
        "rule_revision_id",
        (
            "rule_revision_id",
            "logical_rule_id",
            "drug_concept_id",
            "source_fragment_id",
            "policy_scope",
            "lifecycle",
            "effective_from",
            "effective_to",
            "effective_date_basis",
            "date_override_reason",
            "date_review_comment",
            "historical_application_policy",
            "supersedes_revision_id",
            "content_checksum",
        ),
    ),
    "eligibility_branch": (
        "branch_id",
        (
            "branch_id",
            "rule_revision_id",
            "source_fragment_id",
            "ordinal_no",
            "source_text",
            "source_span_start",
            "source_span_end",
            "disposition",
            "content_checksum",
        ),
    ),
    "condition_node": (
        "node_id",
        (
            "node_id",
            "branch_id",
            "parent_node_id",
            "sibling_order",
            "node_kind",
            "criterion_type",
            "operator",
            "target_kind",
            "target_id",
            "expected_value_json",
            "combination_requirement",
            "source_fragment_id",
            "source_span_start",
            "source_span_end",
            "disposition",
            "content_checksum",
        ),
    ),
    "curated_knowledge_atom": (
        "atom_id",
        (
            "atom_id",
            "source_rule_id",
            "source_field_or_test",
            "atom_type",
            "canonical_payload",
            "source_checksum",
            "content_checksum",
            "migration_status",
            "oncology",
            "rule_status",
        ),
    ),
    "curated_knowledge_mapping": (
        "mapping_id",
        (
            "mapping_id",
            "atom_id",
            "target_kind",
            "target_id",
            "verification_evidence",
            "content_checksum",
        ),
    ),
    "regimen_revision": (
        "regimen_revision_id",
        (
            "regimen_revision_id",
            "logical_regimen_id",
            "canonical_name",
            "lifecycle",
            "effective_from",
            "effective_to",
            "effective_date_basis",
            "date_override_reason",
            "date_review_comment",
            "historical_application_policy",
            "source_refs_json",
            "supersedes_revision_id",
            "content_checksum",
        ),
    ),
    "regimen_alias": (
        "alias_id",
        (
            "alias_id",
            "regimen_revision_id",
            "original_alias",
            "normalized_alias",
            "alias_type",
            "language_code",
            "aggregate_frequency",
            "source_corpus_checksum",
            "review_status",
            "is_ambiguous",
            "content_checksum",
        ),
    ),
    "regimen_context": (
        "context_id",
        (
            "context_id",
            "regimen_revision_id",
            "cancer_context",
            "histology",
            "clinical_setting",
            "content_checksum",
        ),
    ),
    "regimen_component": (
        "component_id",
        (
            "component_id",
            "regimen_revision_id",
            "target_kind",
            "target_id",
            "token",
            "component_role",
            "requirement",
            "sibling_order",
            "source_fragment_id",
            "content_checksum",
        ),
    ),
    "regimen_schedule_component": (
        "schedule_component_id",
        (
            "schedule_component_id",
            "regimen_revision_id",
            "component_id",
            "publishing_enabled",
            "inference_enabled",
            "content_checksum",
        ),
    ),
    "term_dictionary_entry": (
        "term_entry_id",
        (
            "term_entry_id",
            "dictionary_name",
            "term_value",
            "description",
            "content_checksum",
        ),
    ),
    "authoring_qa_issue": (
        "qa_issue_id",
        (
            "qa_issue_id",
            "import_batch_id",
            "workbook_kind",
            "qa_code",
            "entity_id",
            "severity",
            "detail",
            "content_checksum",
        ),
    ),
    "review_event": (
        "review_event_id",
        (
            "review_event_id",
            "entity_type",
            "entity_id",
            "field_name",
            "decision",
            "expert_value_json",
            "comment",
            "evidence_reference",
            "reviewer_id",
            "reviewed_at",
            "reviewed_content_checksum",
            "previous_event_id",
        ),
    ),
}


class SqlServerReleaseStoreAdapter:
    """参数化 SQL Server adapter；所有快照 SELECT 使用 HOLDLOCK。"""

    def __init__(self, connection: Any):
        self.connection = connection

    @staticmethod
    def _rows(result: Any, columns: Sequence[str]) -> list[dict[str, Any]]:
        return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]

    def load_authoring_tables(
        self, *, lock: bool
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        cursor = self.connection.cursor()
        hint = " WITH (HOLDLOCK)" if lock else ""
        tables: dict[str, list[dict[str, Any]]] = {}
        for table, (key_column, columns) in _TABLE_SPECS.items():
            selected = ", ".join(f"[{column}]" for column in columns)
            sql = (
                f"SELECT {selected} FROM [kb].[{table}]{hint} "
                f"ORDER BY [{key_column}]"
            )
            tables[table] = self._rows(cursor.execute(sql), columns)
        return tables

    def get_release(
        self, release_id: str, *, lock: bool
    ) -> Mapping[str, Any] | None:
        hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
        columns = (
            "release_id",
            "status",
            "source_snapshot_checksum",
            "created_by",
            "domain_reviewer_id",
            "published_by",
            "created_at",
            "published_at",
            "release_checksum",
            "previous_release_id",
        )
        selected = ", ".join(f"[{column}]" for column in columns)
        row = self.connection.cursor().execute(
            f"SELECT {selected} FROM [kb].[knowledge_release]{hint} WHERE [release_id] = ?",
            release_id,
        ).fetchone()
        return None if row is None else dict(zip(columns, row, strict=True))

    def get_release_items(
        self, release_id: str, *, lock: bool
    ) -> Sequence[Mapping[str, Any]]:
        hint = " WITH (HOLDLOCK)" if lock else ""
        columns = ("release_id", "entity_type", "revision_id", "content_checksum")
        selected = ", ".join(f"[{column}]" for column in columns)
        result = self.connection.cursor().execute(
            f"SELECT {selected} FROM [kb].[release_item]{hint} "
            "WHERE [release_id] = ? ORDER BY [entity_type], [revision_id]",
            release_id,
        )
        return self._rows(result, columns)

    def get_pointer(self, *, lock: bool) -> Mapping[str, Any] | None:
        hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
        columns = (
            "pointer_name",
            "active_release_id",
            "previous_release_id",
            "changed_by",
            "changed_at",
        )
        selected = ", ".join(f"[{column}]" for column in columns)
        row = self.connection.cursor().execute(
            f"SELECT {selected} FROM [kb_meta].[deployment_pointer]{hint} "
            "WHERE [pointer_name] = 'oncology'"
        ).fetchone()
        return None if row is None else dict(zip(columns, row, strict=True))

    def set_revision_lifecycle(
        self,
        entity_type: str,
        revision_id: str,
        *,
        expected: Sequence[str],
        lifecycle: str,
    ) -> bool:
        definitions = {
            "eligibility": ("eligibility_rule_revision", "rule_revision_id"),
            "regimen": ("regimen_revision", "regimen_revision_id"),
        }
        if entity_type not in definitions or not expected:
            raise ReleaseStoreError("REVISION_ENTITY_TYPE_INVALID")
        table, key = definitions[entity_type]
        placeholders = ",".join("?" for _ in expected)
        cursor = self.connection.cursor()
        cursor.execute(
            f"UPDATE [kb].[{table}] SET [lifecycle] = ? WHERE [{key}] = ? "
            f"AND [lifecycle] IN ({placeholders})",
            lifecycle,
            revision_id,
            *expected,
        )
        return cursor.rowcount == 1

    def insert_release(self, row: Mapping[str, Any]) -> None:
        columns = tuple(row)
        sql = (
            "INSERT INTO [kb].[knowledge_release] ("
            + ", ".join(f"[{column}]" for column in columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ")"
        )
        self.connection.cursor().execute(sql, *(row[column] for column in columns))

    def insert_release_items(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for row in rows:
            self.connection.cursor().execute(
                "INSERT INTO [kb].[release_item] "
                "([release_id], [entity_type], [revision_id], [content_checksum]) "
                "VALUES (?, ?, ?, ?)",
                row["release_id"],
                row["entity_type"],
                row["revision_id"],
                row["content_checksum"],
            )

    def update_release(self, release_id: str, values: Mapping[str, Any]) -> None:
        allowed = {"status", "published_by", "published_at", "release_checksum"}
        if not values or set(values) - allowed:
            raise ReleaseStoreError("RELEASE_UPDATE_FIELDS_INVALID")
        columns = tuple(values)
        sql = (
            "UPDATE [kb].[knowledge_release] SET "
            + ", ".join(f"[{column}] = ?" for column in columns)
            + " WHERE [release_id] = ?"
        )
        cursor = self.connection.cursor()
        cursor.execute(sql, *(values[column] for column in columns), release_id)
        if cursor.rowcount != 1:
            raise ReleaseStoreError("RELEASE_UPDATE_MISSING")

    def set_pointer(
        self, active_release_id: str, previous_release_id: str | None, actor: str
    ) -> None:
        cursor = self.connection.cursor()
        existing = cursor.execute(
            "SELECT [active_release_id] FROM [kb_meta].[deployment_pointer] "
            "WITH (UPDLOCK, HOLDLOCK) WHERE [pointer_name] = 'oncology'"
        ).fetchone()
        if existing is None:
            cursor.execute(
                "INSERT INTO [kb_meta].[deployment_pointer] "
                "([pointer_name], [active_release_id], [previous_release_id], [changed_by]) "
                "VALUES ('oncology', ?, ?, ?)",
                active_release_id,
                previous_release_id,
                actor,
            )
        else:
            cursor.execute(
                "UPDATE [kb_meta].[deployment_pointer] SET [active_release_id] = ?, "
                "[previous_release_id] = ?, [changed_by] = ?, [changed_at] = SYSUTCDATETIME() "
                "WHERE [pointer_name] = 'oncology'",
                active_release_id,
                previous_release_id,
                actor,
            )

    def restore_pointer(self, row: Mapping[str, Any]) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            "UPDATE [kb_meta].[deployment_pointer] SET [active_release_id] = ?, "
            "[previous_release_id] = ?, [changed_by] = ?, [changed_at] = ? "
            "WHERE [pointer_name] = 'oncology'",
            str(row["active_release_id"]),
            None
            if row.get("previous_release_id") in (None, "")
            else str(row["previous_release_id"]),
            str(row["changed_by"]),
            row["changed_at"],
        )
        if cursor.rowcount != 1:
            raise ReleaseStoreError("DEPLOYMENT_POINTER_RESTORE_MISSING")

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _date_value(value: Any, *, code: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_text(value)[:10])
    except ValueError as exc:
        raise ReleaseStoreError(code) from exc


def _timestamp(value: Any, *, code: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = _text(value)
        if not raw:
            raise ReleaseStoreError(code)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ReleaseStoreError(code) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return parsed.isoformat().replace("+00:00", "Z")


def _json_value(value: Any, *, code: str, expected: type) -> Any:
    if isinstance(value, expected):
        return copy.deepcopy(value)
    try:
        parsed = json.loads(_text(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReleaseStoreError(code) from exc
    if not isinstance(parsed, expected):
        raise ReleaseStoreError(code)
    return parsed


def _bit(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return _text(value).casefold() in {"1", "true", "yes", "y"}


def _rows_by(
    tables: Mapping[str, Sequence[Mapping[str, Any]]], table: str, key: str
) -> dict[str, Mapping[str, Any]]:
    rows = tables.get(table)
    if rows is None:
        raise ReleaseStoreError("AUTHORING_TABLE_MISSING")
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identity = _text(row.get(key))
        if not identity or identity in indexed:
            raise ReleaseStoreError("AUTHORING_PRIMARY_KEY_INVALID")
        indexed[identity] = row
    return indexed


def _latest_review_events(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (
            _text(row.get("entity_type")).casefold(),
            _text(row.get("entity_id")),
            _text(row.get("field_name")),
        )
        if not all(key):
            raise ReleaseStoreError("REVIEW_EVENT_IDENTITY_INVALID")
        grouped.setdefault(key, []).append(row)

    def ordering(row: Mapping[str, Any]) -> tuple[datetime, str]:
        raw_time = row.get("reviewed_at")
        if isinstance(raw_time, datetime):
            reviewed_at = raw_time
        else:
            try:
                reviewed_at = datetime.fromisoformat(
                    _text(raw_time).replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ReleaseStoreError("REVIEW_EVENT_TIME_INVALID") from exc
        if reviewed_at.tzinfo is None:
            reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)
        reviewed_at = reviewed_at.astimezone(timezone.utc)
        return reviewed_at, _text(row.get("review_event_id"))

    latest: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for key, events in grouped.items():
        ordered = sorted(events, key=ordering)
        previous_id = ""
        for event in ordered:
            if _text(event.get("previous_event_id")) != previous_id:
                raise ReleaseStoreError("REVIEW_EVENT_CHAIN_INVALID")
            previous_id = _text(event.get("review_event_id"))
        latest[key] = ordered[-1]
    return latest


def _reviewer_for_targets(
    review_events: Sequence[Mapping[str, Any]],
    targets: Sequence[tuple[str, str, str, str]],
    *,
    expected_reviewer: str | None = None,
) -> str:
    if not targets:
        raise ReleaseStoreError("APPROVAL_TARGETS_EMPTY")
    latest = _latest_review_events(review_events)
    reviewers: set[str] = set()
    for entity_type, entity_id, field_name, content_checksum in targets:
        event = latest.get((entity_type.casefold(), entity_id, field_name))
        if event is None:
            raise ReleaseStoreError("LATEST_APPROVAL_EVENT_MISSING")
        if (
            not content_checksum.startswith("sha256:")
            or len(content_checksum) != 71
            or _text(event.get("reviewed_content_checksum"))
            != content_checksum
        ):
            raise ReleaseStoreError("REVIEW_CONTENT_CHECKSUM_MISMATCH")
        decision = _text(event.get("decision")).upper()
        if decision == "APPROVE_WITH_EDIT":
            raise ReleaseStoreError("APPROVAL_EDIT_NOT_MATERIALIZED")
        if decision not in _APPROVAL_DECISIONS:
            raise ReleaseStoreError("LATEST_REVIEW_NOT_APPROVED")
        reviewer = _text(event.get("reviewer_id"))
        if not reviewer:
            raise ReleaseStoreError("LATEST_REVIEWER_MISSING")
        reviewers.add(reviewer)
    if len(reviewers) != 1:
        raise ReleaseStoreError("REVISION_REVIEWER_MISMATCH")
    reviewer = next(iter(reviewers))
    if expected_reviewer is not None and reviewer != expected_reviewer:
        raise ReleaseStoreError("APPROVAL_REVIEWER_MISMATCH")
    return reviewer


def _approval_subject(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    entity_type: str,
    revision_id: str,
) -> tuple[Mapping[str, Any], list[tuple[str, str, str, str]]]:
    if entity_type == "eligibility":
        revision = _rows_by(
            tables, "eligibility_rule_revision", "rule_revision_id"
        ).get(revision_id)
        if revision is None:
            raise ReleaseStoreError("REVISION_NOT_FOUND")
        branches = [
            row
            for row in tables["eligibility_branch"]
            if _text(row.get("rule_revision_id")) == revision_id
        ]
        branch_ids = {_text(row.get("branch_id")) for row in branches}
        nodes = [
            row
            for row in tables["condition_node"]
            if _text(row.get("branch_id")) in branch_ids
        ]
        if not branches or not nodes:
            raise ReleaseStoreError("ELIGIBILITY_REVISION_INCOMPLETE")
        targets = [
            (
                "eligibility_revision",
                revision_id,
                "effective_window",
                _text(revision.get("content_checksum")),
            ),
        ]
        targets.extend(
            [
            (
                "branch",
                _text(row["branch_id"]),
                "branch",
                _text(row.get("content_checksum")),
            )
            for row in branches
            ]
        )
        targets.extend(
            (
                "condition_node",
                _text(row["node_id"]),
                "condition",
                _text(row.get("content_checksum")),
            )
            for row in nodes
        )
        return revision, targets
    if entity_type == "regimen":
        revision = _rows_by(
            tables, "regimen_revision", "regimen_revision_id"
        ).get(revision_id)
        if revision is None:
            raise ReleaseStoreError("REVISION_NOT_FOUND")
        definitions = (
            ("regimen_alias", "alias", "alias_id"),
            ("regimen_context", "context", "context_id"),
            ("regimen_component", "component", "component_id"),
        )
        targets = [
            (
                "regimen",
                revision_id,
                "revision",
                _text(revision.get("content_checksum")),
            )
        ]
        for table, kind, key in definitions:
            selected = [
                row
                for row in tables[table]
                if _text(row.get("regimen_revision_id")) == revision_id
            ]
            if not selected:
                raise ReleaseStoreError("REGIMEN_REVISION_INCOMPLETE")
            targets.extend(
                (
                    kind,
                    _text(row[key]),
                    kind,
                    _text(row.get("content_checksum")),
                )
                for row in selected
            )
        return revision, targets
    raise ReleaseStoreError("REVISION_ENTITY_TYPE_INVALID")


def _require_concept_graph_complete(
    tables: Mapping[str, Sequence[Mapping[str, Any]]], concept_ids: set[str]
) -> None:
    concepts = _rows_by(tables, "drug_concept", "drug_concept_id")
    products = _rows_by(tables, "drug_product", "drug_product_id")
    codes = _rows_by(tables, "drug_code_xref", "drug_code_xref_id")
    products_by_concept: dict[str, list[str]] = {}
    for product_id, row in products.items():
        products_by_concept.setdefault(
            _text(row.get("drug_concept_id")), []
        ).append(product_id)
    coded_products = {_text(row.get("drug_product_id")) for row in codes.values()}
    for concept_id in concept_ids:
        if concept_id not in concepts:
            raise ReleaseStoreError("DRUG_CONCEPT_NOT_FOUND")
        if _text(concepts[concept_id].get("lifecycle")).upper() in {
            "REJECTED",
            "RETIRED",
        }:
            raise ReleaseStoreError("DRUG_CONCEPT_STATE_BLOCKED")
        product_ids = products_by_concept.get(concept_id, [])
        if not product_ids or not any(item in coded_products for item in product_ids):
            raise ReleaseStoreError("DRUG_CONCEPT_GRAPH_INCOMPLETE")


def _require_approved_class_authority(
    tables: Mapping[str, Sequence[Mapping[str, Any]]], class_ids: set[str]
) -> set[str]:
    classes = _rows_by(tables, "drug_class", "drug_class_id")
    reviewers: set[str] = set()
    for class_id in class_ids:
        row = classes.get(class_id)
        if row is None:
            raise ReleaseStoreError("DRUG_CLASS_NOT_FOUND")
        if _text(row.get("lifecycle")).upper() not in _PUBLISHABLE_LIFECYCLES:
            raise ReleaseStoreError("DRUG_CLASS_NOT_APPROVED")
        terms = _json_value(
            row.get("match_terms_json"),
            code="DRUG_CLASS_MATCH_TERMS_INVALID",
            expected=list,
        )
        if not _text(row.get("canonical_name")) or not any(_text(item) for item in terms):
            raise ReleaseStoreError("DRUG_CLASS_MATCH_TERMS_INVALID")
        reviewers.add(
            _reviewer_for_targets(
                tables["review_event"],
                [
                    (
                        "drug_class",
                        class_id,
                        "drug_class",
                        _text(row.get("content_checksum")),
                    )
                ],
            )
        )
    return reviewers


def _require_approved_regimen_authority(
    tables: Mapping[str, Sequence[Mapping[str, Any]]], logical_ids: set[str]
) -> None:
    states: dict[str, set[str]] = {}
    for row in tables["regimen_revision"]:
        states.setdefault(_text(row.get("logical_regimen_id")), set()).add(
            _text(row.get("lifecycle")).upper()
        )
    for logical_id in logical_ids:
        if not states.get(logical_id, set()) & _PUBLISHABLE_LIFECYCLES:
            raise ReleaseStoreError("REGIMEN_TARGET_NOT_APPROVED")


def _require_combination_target_authority(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    nodes: Sequence[Mapping[str, Any]],
) -> None:
    concepts: set[str] = set()
    classes: set[str] = set()
    regimens: set[str] = set()
    for node in nodes:
        if _text(node.get("criterion_type")) != "combination_requirement":
            continue
        target_kind = _text(node.get("target_kind")).upper()
        target_id = _text(node.get("target_id"))
        requirement = _text(node.get("combination_requirement")).upper()
        if (
            target_kind not in {"CONCEPT", "CLASS", "REGIMEN"}
            or not target_id
            or requirement not in _REGIMEN_REQUIREMENTS
        ):
            raise ReleaseStoreError("COMBINATION_TARGET_INVALID")
        {"CONCEPT": concepts, "CLASS": classes, "REGIMEN": regimens}[
            target_kind
        ].add(target_id)
    _require_concept_graph_complete(tables, concepts)
    _require_approved_class_authority(tables, classes)
    _require_approved_regimen_authority(tables, regimens)


def _validate_approval_readiness(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    entity_type: str,
    revision: Mapping[str, Any],
) -> None:
    """在冻结父 revision 前验证其当前子图可进入 release。"""

    documents = _rows_by(tables, "source_document", "source_document_id")
    fragments = _rows_by(tables, "source_fragment", "source_fragment_id")
    effective_from = _date_value(
        revision.get("effective_from"), code="REVISION_EFFECTIVE_DATE_INVALID"
    )
    effective_to = _date_value(
        revision.get("effective_to"), code="REVISION_EFFECTIVE_DATE_INVALID"
    )
    if effective_from > effective_to:
        raise ReleaseStoreError("REVISION_EFFECTIVE_WINDOW_INVALID")

    if entity_type == "eligibility":
        revision_id = _text(revision.get("rule_revision_id"))
        branches = [
            row
            for row in tables["eligibility_branch"]
            if _text(row.get("rule_revision_id")) == revision_id
        ]
        nodes_by_branch: dict[str, list[Mapping[str, Any]]] = {}
        for node in tables["condition_node"]:
            nodes_by_branch.setdefault(_text(node.get("branch_id")), []).append(node)
        if not branches or any(
            _text(row.get("disposition")).casefold() != "approved"
            for row in branches
        ):
            raise ReleaseStoreError("ELIGIBILITY_REVISION_NOT_READY")
        fragment_ids = {_text(revision.get("source_fragment_id"))}
        for branch in branches:
            branch_id = _text(branch.get("branch_id"))
            nodes = nodes_by_branch.get(branch_id, [])
            if not nodes or any(
                _text(row.get("disposition")).casefold() != "approved"
                for row in nodes
            ):
                raise ReleaseStoreError("ELIGIBILITY_REVISION_NOT_READY")
            _condition_tree(nodes)
            _require_combination_target_authority(tables, nodes)
            fragment_ids.add(_text(branch.get("source_fragment_id")))
            fragment_ids.update(_text(row.get("source_fragment_id")) for row in nodes)
        if "" in fragment_ids:
            raise ReleaseStoreError("SOURCE_FRAGMENT_NOT_FOUND")
        for fragment_id in fragment_ids:
            _source_ref_from_fragment(
                fragment_id, fragments=fragments, documents=documents
            )
        parent_fragment = fragments[_text(revision.get("source_fragment_id"))]
        parent_document = documents[_text(parent_fragment.get("source_document_id"))]
        scope = _text(revision.get("policy_scope"))
        if scope not in {"INSURANCE_PAYMENT", "GUIDELINE_INDICATION"} or _text(
            parent_document.get("source_type")
        ) != scope:
            raise ReleaseStoreError("ELIGIBILITY_SOURCE_SCOPE_MISMATCH")
        _require_concept_graph_complete(
            tables, {_text(revision.get("drug_concept_id"))}
        )
        return

    if entity_type == "regimen":
        revision_id = _text(revision.get("regimen_revision_id"))
        aliases = [
            row
            for row in tables["regimen_alias"]
            if _text(row.get("regimen_revision_id")) == revision_id
        ]
        contexts = [
            row
            for row in tables["regimen_context"]
            if _text(row.get("regimen_revision_id")) == revision_id
        ]
        components = [
            row
            for row in tables["regimen_component"]
            if _text(row.get("regimen_revision_id")) == revision_id
        ]
        if not aliases or not contexts or not components:
            raise ReleaseStoreError("REGIMEN_REVISION_NOT_READY")
        if any(
            _text(row.get("review_status")).casefold() != "approved"
            or _bit(row.get("is_ambiguous"))
            for row in aliases
        ):
            raise ReleaseStoreError("REGIMEN_REVISION_NOT_READY")
        if any(
            _text(row.get("target_kind")).upper() not in _REGIMEN_TARGET_KINDS
            or _text(row.get("requirement")).upper() not in _REGIMEN_REQUIREMENTS
            or not _text(row.get("target_id"))
            or not _text(row.get("token"))
            for row in components
        ):
            raise ReleaseStoreError("REGIMEN_REVISION_NOT_READY")
        for source_ref in _validated_source_refs(revision.get("source_refs_json")):
            _validate_typed_source_ref(
                source_ref, fragments=fragments, documents=documents
            )
        for component in components:
            fragment_id = _text(component.get("source_fragment_id"))
            if fragment_id:
                _source_ref_from_fragment(
                    fragment_id, fragments=fragments, documents=documents
                )
        _require_concept_graph_complete(
            tables,
            {
                _text(row.get("target_id"))
                for row in components
                if _text(row.get("target_kind")).upper() == "CONCEPT"
            },
        )
        _require_approved_class_authority(
            tables,
            {
                _text(row.get("target_id"))
                for row in components
                if _text(row.get("target_kind")).upper() == "CLASS"
            },
        )
        return
    raise ReleaseStoreError("REVISION_ENTITY_TYPE_INVALID")


def approve_authoring_revision(
    adapter: ReleaseStoreAdapter,
    *,
    entity_type: str,
    revision_id: str,
    reviewer_id: str,
) -> ApprovalResult:
    """把 review-event 投影为显式 APPROVED 状态；不创建或修改审核事件。"""

    kind = entity_type.strip().casefold()
    identity = revision_id.strip()
    reviewer = reviewer_id.strip()
    if kind not in {"eligibility", "regimen"} or not identity or not reviewer:
        raise ReleaseStoreError("APPROVAL_ARGUMENT_INVALID")
    try:
        tables = _canonical_authoring_tables(
            adapter.load_authoring_tables(lock=True)
        )
        subject, targets = _approval_subject(tables, kind, identity)
        _reviewer_for_targets(
            tables["review_event"], targets, expected_reviewer=reviewer
        )
        _validate_approval_readiness(tables, kind, subject)
        lifecycle = _text(subject.get("lifecycle")).upper()
        if lifecycle in _PUBLISHABLE_LIFECYCLES:
            adapter.rollback()
            return ApprovalResult(kind, identity, lifecycle, True)
        if lifecycle not in _APPROVABLE_LIFECYCLES:
            raise ReleaseStoreError("REVISION_NOT_APPROVABLE")
        changed = adapter.set_revision_lifecycle(
            kind,
            identity,
            expected=tuple(sorted(_APPROVABLE_LIFECYCLES)),
            lifecycle="APPROVED",
        )
        if not changed:
            raise ReleaseStoreError("REVISION_STATE_RACE")
        adapter.commit()
        return ApprovalResult(kind, identity, "APPROVED", False)
    except ReleaseStoreError:
        adapter.rollback()
        raise
    except Exception as exc:
        adapter.rollback()
        raise ReleaseStoreError("APPROVAL_TRANSACTION_FAILED") from None


def _load_pathology_bootstrap(
    path: Path, *, expected_checksum: str
) -> _PathologyBootstrap:
    pinned_checksum = expected_checksum.strip()
    if not pinned_checksum.startswith("sha256:") or len(pinned_checksum) != 71:
        raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_PIN_INVALID")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError
        asset = PathologyKnowledgeAsset.model_validate(raw)
    except Exception:
        raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_INVALID") from None
    expected = asset_payload_checksum(raw)
    if asset.metadata.checksum != expected or expected != pinned_checksum:
        raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_CHECKSUM_MISMATCH")
    if str(asset.metadata.review_status.value) != "approved":
        raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_NOT_APPROVED")
    if asset.metadata.release_status not in {None, "published"}:
        raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_NOT_PUBLISHED")
    if asset.metadata.release_id and asset.metadata.release_status != "published":
        raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_NOT_PUBLISHED")
    if not asset.metadata.source_refs:
        raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_SOURCE_MISSING")
    approved: list[Mapping[str, Any]] = []
    pending: list[Mapping[str, Any]] = []
    entry_ids: set[str] = set()
    for entry in raw.get("entries") or []:
        metadata = entry.get("metadata") if isinstance(entry, Mapping) else None
        if not isinstance(metadata, Mapping) or not metadata.get("source_refs"):
            raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_ENTRY_SOURCE_MISSING")
        entry_id = _text(entry.get("entry_id"))
        if not entry_id or entry_id in entry_ids:
            raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_ENTRY_ID_INVALID")
        entry_ids.add(entry_id)
        if _text(metadata.get("review_status")).casefold() == "approved":
            approved.append(entry)
        else:
            pending.append(entry)
    if not approved:
        raise ReleaseStoreError("PATHOLOGY_BOOTSTRAP_APPROVED_EMPTY")
    return _PathologyBootstrap(
        raw=raw,
        checksum=expected,
        approved_entries=tuple(approved),
        pending_entries=tuple(pending),
    )


def _source_ref_from_fragment(
    fragment_id: str,
    *,
    fragments: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    fragment = fragments.get(fragment_id)
    if fragment is None:
        raise ReleaseStoreError("SOURCE_FRAGMENT_NOT_FOUND")
    document = documents.get(_text(fragment.get("source_document_id")))
    if document is None:
        raise ReleaseStoreError("SOURCE_DOCUMENT_NOT_FOUND")
    value = {
        "source_id": _text(document.get("source_document_id")),
        "source_fragment_id": fragment_id,
        "title": _text(document.get("title")),
        "version": _text(document.get("document_version")),
        "publication_date": None,
        "effective_date": None,
        "retrieval_date": _date_value(
            document.get("retrieval_date"), code="SOURCE_RETRIEVAL_DATE_INVALID"
        ).isoformat(),
        "checksum": _text(document.get("content_checksum")),
    }
    try:
        return SourceReference.model_validate(value).model_dump(mode="json")
    except Exception:
        raise ReleaseStoreError("SOURCE_REFERENCE_INVALID") from None


def _validate_typed_source_ref(
    source_ref: Mapping[str, Any],
    *,
    fragments: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, Mapping[str, Any]],
) -> None:
    source_id = _text(source_ref.get("source_id"))
    document = documents.get(source_id)
    if document is None:
        raise ReleaseStoreError("SOURCE_DOCUMENT_NOT_FOUND")
    fragment_id = _text(source_ref.get("source_fragment_id"))
    if fragment_id:
        fragment = fragments.get(fragment_id)
        if fragment is None or _text(fragment.get("source_document_id")) != source_id:
            raise ReleaseStoreError("SOURCE_FRAGMENT_NOT_FOUND")
    expected_fields = {
        "title": _text(document.get("title")),
        "version": _text(document.get("document_version")),
        "retrieval_date": _date_value(
            document.get("retrieval_date"), code="SOURCE_RETRIEVAL_DATE_INVALID"
        ).isoformat(),
        "checksum": _text(document.get("content_checksum")),
    }
    actual_fields = {
        "title": _text(source_ref.get("title")),
        "version": _text(source_ref.get("version")),
        "retrieval_date": _date_value(
            source_ref.get("retrieval_date"), code="SOURCE_RETRIEVAL_DATE_INVALID"
        ).isoformat(),
        "checksum": _text(source_ref.get("checksum")),
    }
    if actual_fields != expected_fields:
        raise ReleaseStoreError("SOURCE_REFERENCE_DB_MISMATCH")


def _validated_source_refs(value: Any) -> tuple[dict[str, Any], ...]:
    rows = _json_value(value, code="SOURCE_REFS_JSON_INVALID", expected=list)
    refs: list[dict[str, Any]] = []
    for row in rows:
        try:
            refs.append(SourceReference.model_validate(row).model_dump(mode="json"))
        except Exception:
            raise ReleaseStoreError("SOURCE_REFERENCE_INVALID") from None
    if not refs:
        raise ReleaseStoreError("SOURCE_REFERENCE_MISSING")
    return _dedupe_source_refs(refs)


def _dedupe_source_refs(
    values: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    unique: dict[str, dict[str, Any]] = {}
    identities: dict[tuple[str, str], str] = {}
    for value in values:
        try:
            normalized = SourceReference.model_validate(value).model_dump(mode="json")
        except Exception:
            raise ReleaseStoreError("SOURCE_REFERENCE_INVALID") from None
        encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        identity = (
            _text(normalized.get("source_id")),
            _text(normalized.get("source_fragment_id")),
        )
        previous = identities.setdefault(identity, encoded)
        if previous != encoded:
            raise ReleaseStoreError("SOURCE_REFERENCE_CONFLICT")
        unique[encoded] = normalized
    return tuple(unique[key] for key in sorted(unique))


def _source_item_id(table: str, identity: Any) -> str:
    value = _text(identity)
    if not value:
        raise ReleaseStoreError("AUTHORING_PRIMARY_KEY_INVALID")
    return f"{table}:{value}"


def _default_disposition(table: str, row: Mapping[str, Any]) -> tuple[str, str]:
    if table in {"review_event", "source_document", "source_fragment"}:
        return "excluded", "AUDIT_OR_PROVENANCE_ONLY"
    if table in {"curated_knowledge_atom", "curated_knowledge_mapping"}:
        status = _text(row.get("migration_status")).upper()
        return (
            ("excluded", "CURATED_LEDGER_VERIFIED")
            if status == "VERIFIED"
            else ("migration_pending", "CURATED_LEDGER_PENDING")
        )
    if table == "regimen_schedule_component":
        return "excluded", "PHASE1_NON_PUBLISHING"
    if table == "authoring_qa_issue":
        return "unsupported", "AUTHORING_QA_ISSUE"
    if table in {"eligibility_branch", "condition_node"}:
        disposition = _text(row.get("disposition")).casefold()
        if disposition in {"approved", "in_review", "rejected", "unsupported"}:
            return disposition, "AUTHORING_DISPOSITION"
    lifecycle = _text(row.get("lifecycle")).upper()
    if lifecycle in {"DRAFT", "IN_REVIEW", "CHANGES_REQUESTED"}:
        return "in_review", "REVISION_NOT_APPROVED"
    if lifecycle == "REJECTED":
        return "rejected", "REVISION_REJECTED"
    return "excluded", "NOT_IN_PUBLISHED_PROJECTION"


def _initial_coverage(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    pathology: _PathologyBootstrap,
) -> dict[str, CoverageItem]:
    coverage: dict[str, CoverageItem] = {}
    eligibility_lifecycle = {
        _text(row.get("rule_revision_id")): _text(row.get("lifecycle")).upper()
        for row in tables["eligibility_rule_revision"]
    }
    branch_parent = {
        _text(row.get("branch_id")): _text(row.get("rule_revision_id"))
        for row in tables["eligibility_branch"]
    }
    regimen_lifecycle = {
        _text(row.get("regimen_revision_id")): _text(row.get("lifecycle")).upper()
        for row in tables["regimen_revision"]
    }
    atom_status = {
        _text(row.get("atom_id")): _text(row.get("migration_status")).upper()
        for row in tables["curated_knowledge_atom"]
    }
    for table, (key, _) in _TABLE_SPECS.items():
        for row in tables[table]:
            item_id = _source_item_id(table, row.get(key))
            disposition, reason = _default_disposition(table, row)
            if table == "eligibility_branch":
                parent_state = eligibility_lifecycle.get(
                    _text(row.get("rule_revision_id")), ""
                )
                if disposition == "approved" and parent_state not in _PUBLISHABLE_LIFECYCLES:
                    disposition, reason = "in_review", "PARENT_REVISION_NOT_APPROVED"
            elif table == "condition_node":
                parent_state = eligibility_lifecycle.get(
                    branch_parent.get(_text(row.get("branch_id")), ""), ""
                )
                if disposition == "approved" and parent_state not in _PUBLISHABLE_LIFECYCLES:
                    disposition, reason = "in_review", "PARENT_REVISION_NOT_APPROVED"
            elif table in {
                "regimen_alias",
                "regimen_context",
                "regimen_component",
            }:
                parent_state = regimen_lifecycle.get(
                    _text(row.get("regimen_revision_id")), ""
                )
                if parent_state not in _PUBLISHABLE_LIFECYCLES:
                    disposition, reason = "in_review", "PARENT_REVISION_NOT_APPROVED"
            elif table == "curated_knowledge_mapping":
                disposition, reason = (
                    ("excluded", "CURATED_LEDGER_VERIFIED")
                    if atom_status.get(_text(row.get("atom_id"))) == "VERIFIED"
                    else ("migration_pending", "CURATED_LEDGER_PENDING")
                )
            coverage[item_id] = CoverageItem(
                source_item_id=item_id,
                source_type=table.upper(),
                disposition=disposition,
                reason_code=reason,
                source_checksum=_text(
                    row.get("content_checksum") or row.get("source_checksum")
                ),
            )
    for entry in (*pathology.approved_entries, *pathology.pending_entries):
        entry_id = _text(entry.get("entry_id"))
        metadata = entry.get("metadata") or {}
        disposition = (
            "approved"
            if _text(metadata.get("review_status")).casefold() == "approved"
            else "in_review"
        )
        source_refs = metadata.get("source_refs") or []
        source_checksum = _text(source_refs[0].get("checksum")) if source_refs else ""
        item_id = _source_item_id("pathology_bootstrap", entry_id)
        coverage[item_id] = CoverageItem(
            source_item_id=item_id,
            source_type="PATHOLOGY_BOOTSTRAP",
            disposition=disposition,
            reason_code="" if disposition == "approved" else "LEGACY_ENTRY_NOT_APPROVED",
            source_checksum=source_checksum,
        )
    return coverage


def _mark_approved(
    coverage: dict[str, CoverageItem],
    source_item_ids: Sequence[str],
    *,
    revision_id: str,
    source_checksum: str,
) -> None:
    for source_item_id in source_item_ids:
        if source_item_id not in coverage:
            raise ReleaseStoreError("SOURCE_ITEM_NOT_IN_AUTHORITY")
        coverage[source_item_id] = CoverageItem(
            source_item_id=source_item_id,
            source_type=coverage[source_item_id].source_type,
            disposition="approved",
            revision_id=revision_id,
            source_checksum=source_checksum,
        )


def _condition_tree(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ReleaseStoreError("CONDITION_TREE_EMPTY")
    by_id = {_text(row.get("node_id")): row for row in rows}
    if "" in by_id or len(by_id) != len(rows):
        raise ReleaseStoreError("CONDITION_NODE_ID_INVALID")
    children: dict[str, list[Mapping[str, Any]]] = {node_id: [] for node_id in by_id}
    roots: list[Mapping[str, Any]] = []
    for row in rows:
        parent = _text(row.get("parent_node_id"))
        if not parent:
            roots.append(row)
        elif parent not in by_id:
            raise ReleaseStoreError("CONDITION_PARENT_MISSING")
        else:
            children[parent].append(row)
    if len(roots) != 1:
        raise ReleaseStoreError("CONDITION_ROOT_COUNT_INVALID")
    visited: set[str] = set()

    def build(row: Mapping[str, Any]) -> dict[str, Any]:
        node_id = _text(row.get("node_id"))
        if node_id in visited:
            raise ReleaseStoreError("CONDITION_TREE_CYCLE")
        visited.add(node_id)
        kind = _text(row.get("node_kind")).casefold()
        ordered_children = sorted(
            children[node_id],
            key=lambda item: (int(item.get("sibling_order") or 0), _text(item.get("node_id"))),
        )
        if kind == "leaf":
            if ordered_children:
                raise ReleaseStoreError("CONDITION_LEAF_HAS_CHILDREN")
            expected = _json_value(
                row.get("expected_value_json") or "{}",
                code="CONDITION_EXPECTED_JSON_INVALID",
                expected=dict,
            )
            operator = _text(row.get("operator"))
            if operator and "operator" not in expected:
                expected["operator"] = operator
            target_kind = _text(row.get("target_kind"))
            target_id = _text(row.get("target_id"))
            if target_kind:
                expected.setdefault("target_kind", target_kind)
            if target_id:
                expected.setdefault("target_id", target_id)
            requirement = _text(row.get("combination_requirement")).upper()
            if requirement:
                expected.setdefault("requirement", requirement)
            return {
                "node_id": node_id,
                "kind": "leaf",
                "source_text": "",
                "children": [],
                "criterion_id": node_id,
                "criterion_type": _text(row.get("criterion_type")),
                "expected": expected,
                "evidence_policy": {"anchored": True, "missing_is": "UNKNOWN"},
                "documentation_template": "",
            }
        if kind not in {"all", "any"} or not ordered_children:
            raise ReleaseStoreError("CONDITION_AGGREGATE_INVALID")
        return {
            "node_id": node_id,
            "kind": kind,
            "source_text": "",
            "children": [build(child) for child in ordered_children],
            "criterion_id": "",
            "criterion_type": "",
            "expected": {},
            "evidence_policy": {},
            "documentation_template": "",
        }

    tree = build(roots[0])
    if visited != set(by_id):
        raise ReleaseStoreError("CONDITION_TREE_DISCONNECTED")
    return tree


def _eligibility_projection(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    coverage: dict[str, CoverageItem],
    *,
    fragments: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, Mapping[str, Any]],
    concept_refs: dict[str, list[Mapping[str, Any]]],
    concept_windows: dict[str, list[tuple[date, date]]],
    concept_entries: dict[str, list[dict[str, Any]]],
    concept_reviewers: dict[str, set[str]],
) -> tuple[
    list[ReleaseRevision],
    set[str],
    list[tuple[str, str, str]],
    set[str],
]:
    revisions: list[ReleaseRevision] = []
    reviewers: set[str] = set()
    parents: list[tuple[str, str, str]] = []
    component_classes: set[str] = set()
    branches_by_revision: dict[str, list[Mapping[str, Any]]] = {}
    for branch in tables["eligibility_branch"]:
        branches_by_revision.setdefault(
            _text(branch.get("rule_revision_id")), []
        ).append(branch)
    nodes_by_branch: dict[str, list[Mapping[str, Any]]] = {}
    for node in tables["condition_node"]:
        nodes_by_branch.setdefault(_text(node.get("branch_id")), []).append(node)

    for parent in sorted(
        tables["eligibility_rule_revision"],
        key=lambda item: _text(item.get("rule_revision_id")),
    ):
        parent_id = _text(parent.get("rule_revision_id"))
        lifecycle = _text(parent.get("lifecycle")).upper()
        if lifecycle not in _PUBLISHABLE_LIFECYCLES:
            continue
        _, targets = _approval_subject(tables, "eligibility", parent_id)
        reviewer = _reviewer_for_targets(tables["review_event"], targets)
        reviewers.add(reviewer)
        parents.append(("eligibility", parent_id, lifecycle))
        parent_fragment = fragments.get(_text(parent.get("source_fragment_id")))
        parent_document = (
            None
            if parent_fragment is None
            else documents.get(_text(parent_fragment.get("source_document_id")))
        )
        if (
            parent_document is None
            or _text(parent_document.get("source_type"))
            != _text(parent.get("policy_scope"))
        ):
            raise ReleaseStoreError("ELIGIBILITY_SOURCE_SCOPE_MISMATCH")
        branches = sorted(
            branches_by_revision.get(parent_id, []),
            key=lambda item: (int(item.get("ordinal_no") or 0), _text(item.get("branch_id"))),
        )
        if not branches:
            raise ReleaseStoreError("APPROVED_ELIGIBILITY_BRANCH_EMPTY")
        if any(_text(item.get("disposition")).casefold() != "approved" for item in branches):
            raise ReleaseStoreError("APPROVED_ELIGIBILITY_CONTENT_BLOCKED")

        concept_id = _text(parent.get("drug_concept_id"))
        effective_from = _date_value(
            parent.get("effective_from"), code="ELIGIBILITY_EFFECTIVE_DATE_INVALID"
        )
        effective_to = _date_value(
            parent.get("effective_to"), code="ELIGIBILITY_EFFECTIVE_DATE_INVALID"
        )
        parent_assigned = False
        for branch in branches:
            branch_id = _text(branch.get("branch_id"))
            nodes = nodes_by_branch.get(branch_id, [])
            if not nodes or any(
                _text(item.get("disposition")).casefold() != "approved" for item in nodes
            ):
                raise ReleaseStoreError("APPROVED_ELIGIBILITY_CONTENT_BLOCKED")
            _require_combination_target_authority(tables, nodes)
            component_classes.update(
                _text(item.get("target_id"))
                for item in nodes
                if _text(item.get("criterion_type")) == "combination_requirement"
                and _text(item.get("target_kind")).upper() == "CLASS"
            )
            fragment_ids = {
                _text(parent.get("source_fragment_id")),
                _text(branch.get("source_fragment_id")),
                *(_text(item.get("source_fragment_id")) for item in nodes),
            }
            fragment_ids.discard("")
            source_refs = _dedupe_source_refs(
                [
                    _source_ref_from_fragment(
                        fragment_id, fragments=fragments, documents=documents
                    )
                    for fragment_id in sorted(fragment_ids)
                ]
            )
            if not source_refs:
                raise ReleaseStoreError("ELIGIBILITY_SOURCE_REFERENCE_MISSING")
            release_revision_id = stable_id(
                "releasebranch",
                parent_id,
                branch_id,
                parent.get("content_checksum"),
                branch.get("content_checksum"),
                sorted(_text(item.get("content_checksum")) for item in nodes),
            )
            source_items = [_source_item_id("eligibility_branch", branch_id)]
            source_items.extend(
                _source_item_id("condition_node", item.get("node_id"))
                for item in nodes
            )
            if not parent_assigned:
                source_items.append(
                    _source_item_id("eligibility_rule_revision", parent_id)
                )
                parent_assigned = True
            payload = {
                "rule_id": branch_id,
                "drug_concept_id": concept_id,
                "indication_branch_id": branch_id,
                "version": "1.0.0",
                "raw_restriction": _text(branch.get("source_text")),
                "condition_tree": _condition_tree(nodes),
                "source_rule_revision_id": parent_id,
            }
            revision = ReleaseRevision(
                revision_id=release_revision_id,
                logical_id=branch_id,
                asset_name=ELIGIBILITY_ASSET,
                entity_id=branch_id,
                payload=payload,
                reviewer_id=reviewer,
                effective_from=effective_from,
                effective_to=effective_to,
                source_refs=source_refs,
                policy_scope=_text(parent.get("policy_scope")),
                source_item_ids=tuple(sorted(source_items)),
            )
            revisions.append(revision)
            _mark_approved(
                coverage,
                revision.source_item_ids,
                revision_id=revision.revision_id,
                source_checksum=_text(source_refs[0].get("checksum")),
            )
            concept_refs.setdefault(concept_id, []).extend(source_refs)
            concept_windows.setdefault(concept_id, []).append(
                (effective_from, effective_to)
            )
            concept_reviewers.setdefault(concept_id, set()).add(reviewer)
            concept_entries.setdefault(concept_id, []).append(
                {
                    "rule_type": "限适应症",
                    "source_type": (
                        "insurance"
                        if revision.policy_scope == "INSURANCE_PAYMENT"
                        else "guideline"
                    ),
                    "basis": payload["raw_restriction"],
                    "source_refs": sorted(fragment_ids),
                }
            )
    return revisions, reviewers, parents, component_classes


def _regimen_projection(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    coverage: dict[str, CoverageItem],
    *,
    fragments: Mapping[str, Mapping[str, Any]],
    documents: Mapping[str, Mapping[str, Any]],
    concept_refs: dict[str, list[Mapping[str, Any]]],
    concept_windows: dict[str, list[tuple[date, date]]],
    concept_reviewers: dict[str, set[str]],
) -> tuple[
    list[ReleaseRevision],
    set[str],
    list[tuple[str, str, str]],
    set[str],
    set[str],
]:
    revisions: list[ReleaseRevision] = []
    reviewers: set[str] = set()
    parents: list[tuple[str, str, str]] = []
    component_concepts: set[str] = set()
    component_classes: set[str] = set()
    child_definitions = (
        ("regimen_alias", "alias_id"),
        ("regimen_context", "context_id"),
        ("regimen_component", "component_id"),
    )
    publishable_parent_logical = {
        _text(row.get("regimen_revision_id")): _text(
            row.get("logical_regimen_id")
        )
        for row in tables["regimen_revision"]
        if _text(row.get("lifecycle")).upper() in _PUBLISHABLE_LIFECYCLES
    }
    alias_owners: dict[str, set[str]] = {}
    for alias in tables["regimen_alias"]:
        parent_id = _text(alias.get("regimen_revision_id"))
        logical_id = publishable_parent_logical.get(parent_id)
        normalized_alias = _text(alias.get("normalized_alias"))
        if logical_id and normalized_alias:
            alias_owners.setdefault(normalized_alias, set()).add(logical_id)
    if any(len(owners) > 1 for owners in alias_owners.values()):
        raise ReleaseStoreError("REGIMEN_ALIAS_GLOBAL_AMBIGUITY")
    for parent in sorted(
        tables["regimen_revision"],
        key=lambda item: _text(item.get("regimen_revision_id")),
    ):
        parent_id = _text(parent.get("regimen_revision_id"))
        lifecycle = _text(parent.get("lifecycle")).upper()
        if lifecycle not in _PUBLISHABLE_LIFECYCLES:
            continue
        _, targets = _approval_subject(tables, "regimen", parent_id)
        reviewer = _reviewer_for_targets(tables["review_event"], targets)
        reviewers.add(reviewer)
        parents.append(("regimen", parent_id, lifecycle))
        children = {
            table: [
                row
                for row in tables[table]
                if _text(row.get("regimen_revision_id")) == parent_id
            ]
            for table, _ in child_definitions
        }
        aliases = children["regimen_alias"]
        contexts = children["regimen_context"]
        components = children["regimen_component"]
        if not aliases or not contexts or not components:
            raise ReleaseStoreError("APPROVED_REGIMEN_CONTENT_INCOMPLETE")
        if any(
            _text(item.get("review_status")).casefold() != "approved"
            or _bit(item.get("is_ambiguous"))
            for item in aliases
        ):
            raise ReleaseStoreError("APPROVED_REGIMEN_ALIAS_BLOCKED")
        if any(
            _text(item.get("target_kind")).upper() not in _REGIMEN_TARGET_KINDS
            or _text(item.get("requirement")).upper() not in _REGIMEN_REQUIREMENTS
            or not _text(item.get("target_id"))
            or not _text(item.get("token"))
            for item in components
        ):
            raise ReleaseStoreError("APPROVED_REGIMEN_COMPONENT_UNSUPPORTED")
        class_ids = {
            _text(item.get("target_id"))
            for item in components
            if _text(item.get("target_kind")).upper() == "CLASS"
        }
        _require_approved_class_authority(tables, class_ids)
        component_classes.update(class_ids)

        source_refs = list(_validated_source_refs(parent.get("source_refs_json")))
        for source_ref in source_refs:
            _validate_typed_source_ref(
                source_ref, fragments=fragments, documents=documents
            )
        for component in components:
            fragment_id = _text(component.get("source_fragment_id"))
            if fragment_id:
                source_refs.append(
                    _source_ref_from_fragment(
                        fragment_id, fragments=fragments, documents=documents
                    )
                )
        normalized_refs = _dedupe_source_refs(source_refs)
        effective_from = _date_value(
            parent.get("effective_from"), code="REGIMEN_EFFECTIVE_DATE_INVALID"
        )
        effective_to = _date_value(
            parent.get("effective_to"), code="REGIMEN_EFFECTIVE_DATE_INVALID"
        )
        source_items = [_source_item_id("regimen_revision", parent_id)]
        for table, key in child_definitions:
            source_items.extend(
                _source_item_id(table, row.get(key)) for row in children[table]
            )
        payload_components: list[dict[str, str]] = []
        for component in sorted(
            components,
            key=lambda item: (
                int(item.get("sibling_order") or 0),
                _text(item.get("component_id")),
            ),
        ):
            target_kind = _text(component.get("target_kind")).upper()
            target_id = _text(component.get("target_id"))
            requirement = _text(component.get("requirement")).upper()
            if not target_id:
                raise ReleaseStoreError("REGIMEN_COMPONENT_TARGET_MISSING")
            if target_kind == "CONCEPT":
                component_concepts.add(target_id)
                concept_refs.setdefault(target_id, []).extend(normalized_refs)
                concept_windows.setdefault(target_id, []).append(
                    (effective_from, effective_to)
                )
                concept_reviewers.setdefault(target_id, set()).add(reviewer)
            payload_component = {
                "target_kind": target_kind,
                "target_id": target_id,
                "token": _text(component.get("token")),
                "requirement": requirement,
            }
            # 保留旧消费者读取具体药品组分的能力；CLASS 绝不伪造概念 ID。
            if target_kind == "CONCEPT":
                payload_component["drug_concept_id"] = target_id
            payload_components.append(payload_component)
        payload = {
            "regimen_id": _text(parent.get("logical_regimen_id")) or parent_id,
            "canonical_name": _text(parent.get("canonical_name")),
            "aliases": sorted(
                {
                    _text(item.get("original_alias"))
                    for item in aliases
                    if _text(item.get("original_alias"))
                }
            ),
            "cancer_contexts": sorted(
                {
                    _text(item.get("cancer_context"))
                    for item in contexts
                    if _text(item.get("cancer_context"))
                }
            ),
            "components": payload_components,
        }
        if not payload["aliases"] or not payload["cancer_contexts"]:
            raise ReleaseStoreError("APPROVED_REGIMEN_CONTENT_INCOMPLETE")
        revision = ReleaseRevision(
            revision_id=parent_id,
            logical_id=_text(parent.get("logical_regimen_id")) or parent_id,
            asset_name=REGIMEN_ASSET,
            entity_id=_text(parent.get("logical_regimen_id")) or parent_id,
            payload=payload,
            reviewer_id=reviewer,
            effective_from=effective_from,
            effective_to=effective_to,
            source_refs=normalized_refs,
            source_item_ids=tuple(sorted(source_items)),
        )
        revisions.append(revision)
        _mark_approved(
            coverage,
            revision.source_item_ids,
            revision_id=revision.revision_id,
            source_checksum=_text(normalized_refs[0].get("checksum")),
        )
    return revisions, reviewers, parents, component_concepts, component_classes


def _approved_class_dictionary(
    tables: Mapping[str, Sequence[Mapping[str, Any]]], required_class_ids: set[str]
) -> tuple[list[dict[str, Any]], set[str]]:
    reviewers = _require_approved_class_authority(tables, required_class_ids)
    classes = _rows_by(tables, "drug_class", "drug_class_id")
    return [
        {
            "drug_class_id": class_id,
            "canonical_name": _text(classes[class_id].get("canonical_name")),
            "match_terms": sorted(
                {
                    _text(item)
                    for item in _json_value(
                        classes[class_id].get("match_terms_json"),
                        code="DRUG_CLASS_MATCH_TERMS_INVALID",
                        expected=list,
                    )
                    if _text(item)
                }
            ),
            "lifecycle": _text(classes[class_id].get("lifecycle")).upper(),
            "reviewer_id": _reviewer_for_targets(
                tables["review_event"],
                [
                    (
                        "drug_class",
                        class_id,
                        "drug_class",
                        _text(classes[class_id].get("content_checksum")),
                    )
                ],
            ),
        }
        for class_id in sorted(required_class_ids)
    ], reviewers


def _drug_projection(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    coverage: dict[str, CoverageItem],
    *,
    concept_refs: Mapping[str, Sequence[Mapping[str, Any]]],
    concept_windows: Mapping[str, Sequence[tuple[date, date]]],
    concept_entries: Mapping[str, Sequence[Mapping[str, Any]]],
    concept_reviewers: Mapping[str, set[str]],
    required_concepts: set[str],
) -> tuple[
    list[ReleaseRevision],
    set[str],
    list[tuple[str, str, str]],
    list[dict[str, Any]],
]:
    concepts = _rows_by(tables, "drug_concept", "drug_concept_id")
    products_by_concept: dict[str, list[Mapping[str, Any]]] = {}
    for product in tables["drug_product"]:
        products_by_concept.setdefault(
            _text(product.get("drug_concept_id")), []
        ).append(product)
    codes_by_product: dict[str, list[Mapping[str, Any]]] = {}
    for code in tables["drug_code_xref"]:
        codes_by_product.setdefault(_text(code.get("drug_product_id")), []).append(code)

    missing_concepts = required_concepts - set(concepts)
    if missing_concepts:
        raise ReleaseStoreError("DRUG_CONCEPT_NOT_FOUND")

    revisions: list[ReleaseRevision] = []
    reviewers: set[str] = set()
    parents: list[tuple[str, str, str]] = []
    regimen_dictionary: list[dict[str, Any]] = []
    entity_names: set[str] = set()
    for concept_id in sorted(required_concepts):
        concept = concepts[concept_id]
        if _text(concept.get("lifecycle")).upper() in {"REJECTED", "RETIRED"}:
            raise ReleaseStoreError("DRUG_CONCEPT_STATE_BLOCKED")
        supporting_reviewers = set(concept_reviewers.get(concept_id, set()))
        if not supporting_reviewers:
            raise ReleaseStoreError("DRUG_APPROVED_REFERENCE_MISSING")
        reviewers.update(supporting_reviewers)
        reviewer = (
            next(iter(supporting_reviewers))
            if len(supporting_reviewers) == 1
            else stable_id("reviewteam", sorted(supporting_reviewers))
        )
        products = sorted(
            products_by_concept.get(concept_id, []),
            key=lambda item: _text(item.get("drug_product_id")),
        )
        if not products:
            raise ReleaseStoreError("DRUG_CONCEPT_GRAPH_INCOMPLETE")
        refs = _dedupe_source_refs(concept_refs.get(concept_id, ()))
        windows = list(concept_windows.get(concept_id, ()))
        if not refs or not windows:
            raise ReleaseStoreError("DRUG_CONCEPT_SOURCE_OR_WINDOW_MISSING")
        canonical_name = _text(concept.get("canonical_name"))
        if not canonical_name or canonical_name in entity_names:
            raise ReleaseStoreError("DRUG_CANONICAL_NAME_INVALID")
        entity_names.add(canonical_name)
        aliases = sorted(
            {
                _text(item.get("product_name"))
                for item in products
                if _text(item.get("product_name"))
                and _text(item.get("product_name")) != canonical_name
            }
        )
        code_rows = [
            code
            for product in products
            for code in codes_by_product.get(_text(product.get("drug_product_id")), [])
        ]
        if not code_rows:
            raise ReleaseStoreError("DRUG_CONCEPT_GRAPH_INCOMPLETE")
        code_values = sorted(
            {_text(item.get("code_value")) for item in code_rows if _text(item.get("code_value"))}
        )
        payload = {
            "canonical_name": canonical_name,
            "canonical_match_name": canonical_name,
            "aliases": aliases,
            "codes": code_values,
            "oncology": {"drug_concept_id": concept_id},
            "entity_type": "canonical_only",
            "entries": list(concept_entries.get(concept_id, ())),
            "effective": None,
            "insurance_status": "restricted",
            "sources": {},
        }
        source_items = [_source_item_id("drug_concept", concept_id)]
        source_items.extend(
            _source_item_id("drug_product", item.get("drug_product_id"))
            for item in products
        )
        source_items.extend(
            _source_item_id("drug_code_xref", item.get("drug_code_xref_id"))
            for item in code_rows
        )
        revision_id = stable_id(
            "releasedrug",
            concept_id,
            concept.get("content_checksum"),
            sorted(_text(item.get("content_checksum")) for item in products),
            sorted(_text(item.get("content_checksum")) for item in code_rows),
        )
        revision = ReleaseRevision(
            revision_id=revision_id,
            logical_id=concept_id,
            asset_name=DRUG_ASSET,
            entity_id=canonical_name,
            payload=payload,
            reviewer_id=reviewer,
            effective_from=min(item[0] for item in windows),
            effective_to=max(item[1] for item in windows),
            source_refs=refs,
            source_item_ids=tuple(sorted(source_items)),
        )
        revisions.append(revision)
        _mark_approved(
            coverage,
            revision.source_item_ids,
            revision_id=revision.revision_id,
            source_checksum=_text(refs[0].get("checksum")),
        )
        regimen_dictionary.append(
            {
                "concept_id": concept_id,
                "generic_name": canonical_name,
                "aliases": [
                    {"value": alias, "alias_type": "trade"} for alias in aliases
                ],
                "insurance_codes": sorted(
                    {
                        _text(item.get("code_value"))
                        for item in code_rows
                        if _text(item.get("code_system")).upper() == "INSURANCE"
                        and _text(item.get("code_value"))
                    }
                ),
            }
        )
    if not revisions:
        raise ReleaseStoreError("APPROVED_DRUG_CONCEPT_EMPTY")
    return revisions, reviewers, parents, regimen_dictionary


def _pathology_projection(
    pathology: _PathologyBootstrap,
    coverage: dict[str, CoverageItem],
) -> list[ReleaseRevision]:
    revisions: list[ReleaseRevision] = []
    for entry in sorted(
        pathology.approved_entries, key=lambda item: _text(item.get("entry_id"))
    ):
        entry_id = _text(entry.get("entry_id"))
        metadata = entry.get("metadata") or {}
        source_refs = _dedupe_source_refs(metadata.get("source_refs") or [])
        effective_from = _date_value(
            metadata.get("effective_from"), code="PATHOLOGY_EFFECTIVE_DATE_INVALID"
        )
        effective_to = _date_value(
            metadata.get("effective_to"), code="PATHOLOGY_EFFECTIVE_DATE_INVALID"
        )
        payload = copy.deepcopy(dict(entry))
        payload.pop("metadata", None)
        revision_id = stable_id(
            "pathologybootstrap", entry_id, pathology.checksum, checksum(payload)
        )
        source_item_id = _source_item_id("pathology_bootstrap", entry_id)
        revision = ReleaseRevision(
            revision_id=revision_id,
            logical_id=entry_id,
            asset_name=PATHOLOGY_ASSET,
            entity_id=entry_id,
            payload=payload,
            reviewer_id=_BOOTSTRAP_REVIEWER,
            effective_from=effective_from,
            effective_to=effective_to,
            source_refs=source_refs,
            source_item_ids=(source_item_id,),
        )
        revisions.append(revision)
        _mark_approved(
            coverage,
            revision.source_item_ids,
            revision_id=revision.revision_id,
            source_checksum=_text(source_refs[0].get("checksum")),
        )
    return revisions


def _curated_projection(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[CuratedAtomCoverage]:
    mappings: dict[str, list[Mapping[str, Any]]] = {}
    for row in tables["curated_knowledge_mapping"]:
        mappings.setdefault(_text(row.get("atom_id")), []).append(row)
    atoms: list[CuratedAtomCoverage] = []
    live_condition_targets = {
        *(_text(row.get("rule_revision_id")) for row in tables["eligibility_rule_revision"]),
        *(_text(row.get("branch_id")) for row in tables["eligibility_branch"]),
        *(_text(row.get("node_id")) for row in tables["condition_node"]),
    }
    live_dictionary_targets = {
        *(_text(row.get("term_entry_id")) for row in tables["term_dictionary_entry"]),
        *(_text(row.get("drug_concept_id")) for row in tables["drug_concept"]),
    }
    allowed_target_kinds = {
        "CONDITION",
        "DICTIONARY",
        "EVALUATOR_POLICY",
        "REVIEW_GUIDANCE",
        "REGRESSION_CASE",
    }
    for row in sorted(
        tables["curated_knowledge_atom"], key=lambda item: _text(item.get("atom_id"))
    ):
        atom_id = _text(row.get("atom_id"))
        status = _text(row.get("migration_status")).upper()
        if status not in {"DISCOVERED", "MAPPED", "VERIFIED"}:
            raise ReleaseStoreError("CURATED_MIGRATION_STATUS_INVALID")
        rule_status = _text(row.get("rule_status"))
        if row.get("oncology") is None or rule_status not in {
            "ready",
            "drafting",
            "abandoned",
        }:
            raise ReleaseStoreError("CURATED_AUTHORITY_FIELDS_INVALID")
        selected = mappings.get(atom_id, [])
        if status in {"MAPPED", "VERIFIED"} and len(selected) != 1:
            raise ReleaseStoreError("CURATED_MAPPING_CARDINALITY_INVALID")
        if status == "DISCOVERED" and selected:
            raise ReleaseStoreError("CURATED_DISCOVERED_HAS_MAPPING")
        mapping = selected[0] if selected else {}
        target_kind = _text(mapping.get("target_kind")).upper()
        target_id = _text(mapping.get("target_id"))
        if mapping and target_kind not in allowed_target_kinds:
            raise ReleaseStoreError("CURATED_TARGET_KIND_INVALID")
        if status == "VERIFIED":
            if not target_id or not _text(mapping.get("verification_evidence")):
                raise ReleaseStoreError("CURATED_VERIFICATION_EVIDENCE_MISSING")
            if target_kind == "CONDITION" and target_id not in live_condition_targets:
                raise ReleaseStoreError("CURATED_VERIFIED_TARGET_NOT_FOUND")
            if target_kind == "DICTIONARY" and target_id not in live_dictionary_targets:
                raise ReleaseStoreError("CURATED_VERIFIED_TARGET_NOT_FOUND")
        atoms.append(
            CuratedAtomCoverage(
                atom_id=atom_id,
                source_rule_id=_text(row.get("source_rule_id")),
                migration_status=status,
                oncology=_bit(row.get("oncology")),
                rule_status=rule_status,
                target_id=target_id,
                verification_evidence=_text(mapping.get("verification_evidence")),
            )
        )
    if not atoms:
        raise ReleaseStoreError("CURATED_ATOM_UNIVERSE_EMPTY")
    return atoms


def _authority_snapshot_rows(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """序列化 authority；发布动作本身的 APPROVED→RELEASED 不改变 release ID。"""

    normalized: dict[str, list[dict[str, Any]]] = {}
    revision_tables = {"eligibility_rule_revision", "regimen_revision"}
    for table in sorted(tables):
        rows: list[dict[str, Any]] = []
        for raw in tables[table]:
            row = dict(raw)
            if (
                table in revision_tables
                and _text(row.get("lifecycle")).upper()
                in _PUBLISHABLE_LIFECYCLES
            ):
                row["lifecycle"] = "APPROVED"
            rows.append(row)
        normalized[table] = rows
    return normalized


def _authority_manifests(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    pathology: _PathologyBootstrap,
    coverage: Mapping[str, CoverageItem],
    curated_atoms: Sequence[CuratedAtomCoverage],
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot_content_checksum = checksum(
        {
            "tables": _authority_snapshot_rows(tables),
            "pathology_bootstrap": pathology.raw,
        }
    )
    source_payload = {
        "schema_version": "1.0.0",
        "source_item_ids": sorted(coverage),
        "counts": {"source_items": len(coverage)},
        "content_checksum": snapshot_content_checksum,
    }
    source_manifest = {
        **source_payload,
        "snapshot_checksum": checksum(source_payload),
    }
    curated_rows = [
        {
            "atom_id": atom.atom_id,
            "source_rule_id": atom.source_rule_id,
            "migration_status": atom.migration_status,
            "oncology": atom.oncology,
            "rule_status": atom.rule_status,
            "target_id": atom.target_id,
            "verification_evidence": atom.verification_evidence,
        }
        for atom in curated_atoms
    ]
    curated_payload = {
        "schema_version": "1.0.0",
        "atoms": curated_rows,
        "counts": {"atoms": len(curated_rows)},
        "content_checksum": checksum(
            {
                "atoms": [dict(row) for row in tables["curated_knowledge_atom"]],
                "mappings": [
                    dict(row) for row in tables["curated_knowledge_mapping"]
                ],
            }
        ),
    }
    curated_manifest = {
        **curated_payload,
        "manifest_checksum": checksum(curated_payload),
    }
    return source_manifest, curated_manifest


def _canonical_authoring_tables(
    raw_tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    if set(raw_tables) != set(_TABLE_SPECS):
        raise ReleaseStoreError("AUTHORING_TABLE_SET_INCOMPLETE")
    tables: dict[str, list[dict[str, Any]]] = {}
    for table, (key, _) in _TABLE_SPECS.items():
        indexed = _rows_by(raw_tables, table, key)
        tables[table] = [dict(indexed[item]) for item in sorted(indexed)]
    return tables


def inspect_operational_authority(
    adapter: ReleaseStoreAdapter,
    *,
    pathology_bootstrap: Path,
    pathology_bootstrap_checksum: str,
) -> ReleaseAuthorityInspection:
    """只读计算待人工固定的 authority checksums；不返回原文或 principal。"""

    try:
        tables = _canonical_authoring_tables(
            adapter.load_authoring_tables(lock=True)
        )
        pathology = _load_pathology_bootstrap(
            pathology_bootstrap,
            expected_checksum=pathology_bootstrap_checksum,
        )
        coverage = _initial_coverage(tables, pathology)
        curated_atoms = _curated_projection(tables)
        source_manifest, curated_manifest = _authority_manifests(
            tables, pathology, coverage, curated_atoms
        )
        result = ReleaseAuthorityInspection(
            source_authority_checksum=source_manifest["snapshot_checksum"],
            curated_authority_checksum=curated_manifest["manifest_checksum"],
            pathology_bootstrap_checksum=pathology.checksum,
            source_item_count=len(coverage),
            curated_atom_count=len(curated_atoms),
        )
        adapter.rollback()
        return result
    except ReleaseStoreError:
        adapter.rollback()
        raise
    except Exception:
        adapter.rollback()
        raise ReleaseStoreError("RELEASE_AUTHORITY_INSPECTION_FAILED") from None


def _derive_projection(
    adapter: ReleaseStoreAdapter,
    *,
    operator: str,
    created_at: str,
    pathology_bootstrap: Path,
    pathology_bootstrap_checksum: str,
    expected_source_authority_checksum: str,
    expected_curated_authority_checksum: str,
) -> _Projection:
    tables = _canonical_authoring_tables(
        adapter.load_authoring_tables(lock=True)
    )
    pathology = _load_pathology_bootstrap(
        pathology_bootstrap, expected_checksum=pathology_bootstrap_checksum
    )
    documents = _rows_by(tables, "source_document", "source_document_id")
    fragments = _rows_by(tables, "source_fragment", "source_fragment_id")
    coverage = _initial_coverage(tables, pathology)
    concept_refs: dict[str, list[Mapping[str, Any]]] = {}
    concept_windows: dict[str, list[tuple[date, date]]] = {}
    concept_entries: dict[str, list[dict[str, Any]]] = {}
    concept_reviewers: dict[str, set[str]] = {}

    (
        eligibility,
        eligibility_reviewers,
        eligibility_parents,
        eligibility_classes,
    ) = _eligibility_projection(
        tables,
        coverage,
        fragments=fragments,
        documents=documents,
        concept_refs=concept_refs,
        concept_windows=concept_windows,
        concept_entries=concept_entries,
        concept_reviewers=concept_reviewers,
    )
    (
        regimen,
        regimen_reviewers,
        regimen_parents,
        component_concepts,
        regimen_classes,
    ) = _regimen_projection(
        tables,
        coverage,
        fragments=fragments,
        documents=documents,
        concept_refs=concept_refs,
        concept_windows=concept_windows,
        concept_reviewers=concept_reviewers,
    )
    eligibility_concepts = {
        _text(item.payload.get("drug_concept_id")) for item in eligibility
    }
    required_concepts = eligibility_concepts | component_concepts
    class_dictionary, class_reviewers = _approved_class_dictionary(
        tables, eligibility_classes | regimen_classes
    )
    drugs, drug_reviewers, drug_parents, regimen_dictionary = _drug_projection(
        tables,
        coverage,
        concept_refs=concept_refs,
        concept_windows=concept_windows,
        concept_entries=concept_entries,
        concept_reviewers=concept_reviewers,
        required_concepts=required_concepts,
    )
    pathology_revisions = _pathology_projection(pathology, coverage)
    curated_atoms = _curated_projection(tables)
    revisions = [*drugs, *eligibility, *pathology_revisions, *regimen]

    typed_reviewers = (
        eligibility_reviewers
        | regimen_reviewers
        | drug_reviewers
        | class_reviewers
    )
    if not typed_reviewers:
        raise ReleaseStoreError("DOMAIN_REVIEWER_EMPTY")
    release_operator = operator.strip()
    if not release_operator or release_operator in typed_reviewers:
        raise ReleaseStoreError("RELEASE_DUTY_SEPARATION_FAILED")
    normalized_created_at = _timestamp(
        created_at, code="RELEASE_CREATED_AT_INVALID"
    )
    domain_reviewer_id = (
        next(iter(typed_reviewers))
        if len(typed_reviewers) == 1
        else stable_id("reviewteam", sorted(typed_reviewers))
    )

    source_manifest, curated_manifest = _authority_manifests(
        tables, pathology, coverage, curated_atoms
    )
    try:
        authority = build_release_authority_snapshot(
            source_snapshot_manifest=source_manifest,
            curated_manifest=curated_manifest,
            expected_source_snapshot_checksum=expected_source_authority_checksum,
            expected_curated_manifest_checksum=expected_curated_authority_checksum,
        )
    except ValueError:
        raise ReleaseStoreError("RELEASE_AUTHORITY_PIN_MISMATCH") from None

    source_registry: dict[str, dict[str, Any]] = {}
    for revision in revisions:
        for source_ref in revision.source_refs:
            key = stable_id(
                "source",
                source_ref.get("source_id"),
                source_ref.get("source_fragment_id"),
            )
            source_registry[key] = dict(source_ref)
    candidate = build_release_candidate(
        revisions=revisions,
        coverage=list(coverage.values()),
        curated_atoms=curated_atoms,
        source_snapshot_checksum=authority.source_snapshot_checksum,
        created_by=release_operator,
        release_operator=release_operator,
        created_at=normalized_created_at,
        expected_source_item_ids=authority.source_item_ids,
        authority_snapshot=authority,
        asset_extras={
            REGIMEN_ASSET: {
                "drug_concepts": regimen_dictionary,
                "drug_classes": class_dictionary,
            },
            DRUG_ASSET: {
                "policy": {
                    "guideline_authority": "临床应用指导原则，不替代法定说明书"
                },
                "sources": source_registry,
            },
        },
    )
    return _Projection(
        candidate=candidate,
        reviewer_ids=frozenset({*typed_reviewers, _BOOTSTRAP_REVIEWER}),
        domain_reviewer_id=domain_reviewer_id,
        parent_revisions=tuple(
            sorted({*eligibility_parents, *regimen_parents, *drug_parents})
        ),
    )


def _local_active_release_id(releases_dir: Path) -> str | None:
    pointer = releases_dir / "active_release.json"
    if not pointer.is_file():
        return None
    try:
        resolved = resolve_active_release_assets(releases_dir)
    except Exception:
        raise ReleaseStoreError("LOCAL_ACTIVE_RELEASE_INVALID") from None
    parents = {path.parent.name for path in resolved.values()}
    if len(parents) != 1:
        raise ReleaseStoreError("LOCAL_ACTIVE_RELEASE_INVALID")
    return next(iter(parents))


def _previous_manifest(
    pointer: Mapping[str, Any] | None, releases_dir: Path
) -> Mapping[str, Any] | None:
    active_id = None if pointer is None else _text(pointer.get("active_release_id"))
    local_id = _local_active_release_id(releases_dir)
    if active_id != local_id:
        raise ReleaseStoreError("DEPLOYMENT_POINTER_DIVERGED")
    if not active_id:
        return None
    try:
        compiled = load_release_bundle(releases_dir, active_id)
        validate_compiled_release(compiled, require_published=True)
        return compiled.manifest
    except Exception:
        raise ReleaseStoreError("PREVIOUS_RELEASE_BUNDLE_INVALID") from None


def _manifest_for_release_id(
    release_id: str | None, releases_dir: Path
) -> Mapping[str, Any] | None:
    if not release_id:
        return None
    try:
        compiled = load_release_bundle(releases_dir, release_id)
        validate_compiled_release(compiled, require_published=True)
        return compiled.manifest
    except Exception:
        raise ReleaseStoreError("PREVIOUS_RELEASE_BUNDLE_INVALID") from None


def _restore_local_active(releases_dir: Path, release_id: str | None) -> None:
    """把本地指针恢复到事务前状态；不可变 bundle 始终保留。"""

    if release_id:
        activate_release(releases_dir, release_id)
        return
    pointer = releases_dir / "active_release.json"
    if pointer.exists():
        pointer.unlink()


def _release_item_rows(compiled: Any) -> list[dict[str, Any]]:
    return [
        {
            "release_id": compiled.release_id,
            "entity_type": _text(item.get("asset_name")),
            "revision_id": _text(item.get("revision_id")),
            "content_checksum": _text(item.get("payload_checksum")),
        }
        for item in compiled.manifest.get("release_items") or []
    ]


def _item_signature(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (
                _text(row.get("entity_type")),
                _text(row.get("revision_id")),
                _text(row.get("content_checksum")),
            )
            for row in rows
        )
    )


def _validated_stored_published_bundle(
    adapter: ReleaseStoreAdapter,
    stored: Mapping[str, Any],
    releases_dir: Path,
) -> tuple[Any, Sequence[Mapping[str, Any]]]:
    release_id = _text(stored.get("release_id"))
    try:
        compiled = load_release_bundle(releases_dir, release_id)
        validate_compiled_release(compiled, require_published=True)
    except Exception:
        raise ReleaseStoreError("STORED_RELEASE_BUNDLE_INVALID") from None
    manifest = compiled.manifest
    published_by = _text(stored.get("published_by"))
    if (
        _text(stored.get("release_checksum")) != _text(manifest.get("checksum"))
        or _text(stored.get("source_snapshot_checksum"))
        != _text(manifest.get("source_snapshot_checksum"))
        or _text(stored.get("created_by")) != _text(manifest.get("created_by"))
        or published_by != _text(manifest.get("release_operator"))
        or _timestamp(
            stored.get("published_at"), code="RELEASE_PUBLISHED_AT_INVALID"
        )
        != _timestamp(
            manifest.get("published_at"), code="RELEASE_PUBLISHED_AT_INVALID"
        )
    ):
        raise ReleaseStoreError("STORED_RELEASE_BUNDLE_MISMATCH")
    items = adapter.get_release_items(release_id, lock=True)
    if _item_signature(items) != _item_signature(_release_item_rows(compiled)):
        raise ReleaseStoreError("STORED_RELEASE_ITEMS_MISMATCH")
    return compiled, items


def build_operational_release(
    adapter: ReleaseStoreAdapter,
    *,
    operator: str,
    created_at: str,
    pathology_bootstrap: Path,
    pathology_bootstrap_checksum: str,
    expected_source_authority_checksum: str,
    expected_curated_authority_checksum: str,
    releases_dir: Path,
) -> ReleaseOperationResult:
    """从锁定的 authoring 全集构建并持久化幂等 CANDIDATE。"""

    try:
        projection = _derive_projection(
            adapter,
            operator=operator,
            created_at=created_at,
            pathology_bootstrap=pathology_bootstrap,
            pathology_bootstrap_checksum=pathology_bootstrap_checksum,
            expected_source_authority_checksum=expected_source_authority_checksum,
            expected_curated_authority_checksum=expected_curated_authority_checksum,
        )
        pointer = adapter.get_pointer(lock=True)
        previous_manifest = _previous_manifest(pointer, releases_dir)
        compiled = compile_release(
            projection.candidate, previous_manifest=previous_manifest
        )
        validate_compiled_release(compiled, require_published=False)
        items = _release_item_rows(compiled)
        if not items:
            raise ReleaseStoreError("RELEASE_ITEMS_EMPTY")
        existing = adapter.get_release(compiled.release_id, lock=True)
        if existing is not None:
            stored_items = adapter.get_release_items(compiled.release_id, lock=True)
            if (
                _text(existing.get("source_snapshot_checksum"))
                != projection.candidate.source_snapshot_checksum
                or _item_signature(stored_items) != _item_signature(items)
                or _text(existing.get("created_by"))
                != projection.candidate.created_by
                or _text(existing.get("domain_reviewer_id"))
                != projection.domain_reviewer_id
            ):
                raise ReleaseStoreError("EXISTING_RELEASE_CONTENT_MISMATCH")
            status = _text(existing.get("status")).upper()
            if status not in {"CANDIDATE", "PUBLISHED", "ROLLED_BACK"}:
                raise ReleaseStoreError("EXISTING_RELEASE_STATUS_INVALID")
            if status == "CANDIDATE":
                stored_previous = _text(existing.get("previous_release_id")) or None
                active_previous = (
                    None
                    if pointer is None
                    else _text(pointer.get("active_release_id")) or None
                )
                if stored_previous != active_previous:
                    raise ReleaseStoreError("EXISTING_CANDIDATE_POINTER_STALE")
                stored_candidate = replace(
                    projection.candidate,
                    created_by=_text(existing.get("created_by")),
                    release_operator=_text(existing.get("created_by")),
                    created_at=_timestamp(
                        existing.get("created_at"),
                        code="RELEASE_CREATED_AT_INVALID",
                    ),
                )
                stored_compiled = compile_release(
                    stored_candidate, previous_manifest=previous_manifest
                )
                if _text(existing.get("release_checksum")) != _text(
                    stored_compiled.manifest.get("checksum")
                ):
                    raise ReleaseStoreError("EXISTING_RELEASE_CONTENT_MISMATCH")
            else:
                _validated_stored_published_bundle(
                    adapter, existing, releases_dir
                )
            adapter.rollback()
            return ReleaseOperationResult(
                compiled.release_id, status, len(items), reused=True
            )
        previous_release_id = (
            None if pointer is None else _text(pointer.get("active_release_id")) or None
        )
        adapter.insert_release(
            {
                "release_id": compiled.release_id,
                "status": "CANDIDATE",
                "source_snapshot_checksum": projection.candidate.source_snapshot_checksum,
                "created_by": projection.candidate.created_by,
                "domain_reviewer_id": projection.domain_reviewer_id,
                "published_by": None,
                "created_at": projection.candidate.created_at,
                "published_at": None,
                "release_checksum": compiled.manifest["checksum"],
                "previous_release_id": previous_release_id,
            }
        )
        adapter.insert_release_items(items)
        adapter.commit()
        return ReleaseOperationResult(
            compiled.release_id, "CANDIDATE", len(items), reused=False
        )
    except ReleaseStoreError:
        adapter.rollback()
        raise
    except Exception:
        adapter.rollback()
        raise ReleaseStoreError("RELEASE_BUILD_FAILED") from None


def _candidate_for_stored_release(
    adapter: ReleaseStoreAdapter,
    stored: Mapping[str, Any],
    *,
    pathology_bootstrap: Path,
    pathology_bootstrap_checksum: str,
    expected_source_authority_checksum: str,
    expected_curated_authority_checksum: str,
    releases_dir: Path,
) -> tuple[_Projection, Any, Mapping[str, Any] | None]:
    projection = _derive_projection(
        adapter,
        operator=_text(stored.get("created_by")),
        created_at=_timestamp(
            stored.get("created_at"), code="RELEASE_CREATED_AT_INVALID"
        ),
        pathology_bootstrap=pathology_bootstrap,
        pathology_bootstrap_checksum=pathology_bootstrap_checksum,
        expected_source_authority_checksum=expected_source_authority_checksum,
        expected_curated_authority_checksum=expected_curated_authority_checksum,
    )
    if projection.candidate.release_id != _text(stored.get("release_id")):
        raise ReleaseStoreError("RELEASE_RECONSTRUCTION_ID_MISMATCH")
    if projection.domain_reviewer_id != _text(stored.get("domain_reviewer_id")):
        raise ReleaseStoreError("RELEASE_RECONSTRUCTION_REVIEWER_MISMATCH")
    if (
        projection.candidate.source_snapshot_checksum
        != _text(stored.get("source_snapshot_checksum"))
    ):
        raise ReleaseStoreError("RELEASE_RECONSTRUCTION_SNAPSHOT_MISMATCH")
    pointer = adapter.get_pointer(lock=True)
    expected_previous = _text(stored.get("previous_release_id")) or None
    actual_previous = (
        None if pointer is None else _text(pointer.get("active_release_id")) or None
    )
    status = _text(stored.get("status")).upper()
    expected_active = (
        _text(stored.get("release_id")) if status == "PUBLISHED" else expected_previous
    )
    local_active = _local_active_release_id(releases_dir)
    allowed_local = {expected_active}
    if status == "CANDIDATE":
        # 进程可能在本地原子切换后、DB commit 前退出；完整 prepared bundle
        # 可由同一 candidate/pin 重试安全收敛，其他 divergence 仍 fail closed。
        allowed_local.add(_text(stored.get("release_id")))
    if expected_active != actual_previous or local_active not in allowed_local:
        raise ReleaseStoreError("RELEASE_PREVIOUS_POINTER_MISMATCH")
    previous_manifest = _manifest_for_release_id(expected_previous, releases_dir)
    candidate_compiled = compile_release(
        projection.candidate, previous_manifest=previous_manifest
    )
    validate_compiled_release(candidate_compiled, require_published=False)
    stored_items = adapter.get_release_items(
        projection.candidate.release_id, lock=True
    )
    if _item_signature(stored_items) != _item_signature(
        _release_item_rows(candidate_compiled)
    ):
        raise ReleaseStoreError("RELEASE_RECONSTRUCTION_ITEMS_MISMATCH")
    if _text(stored.get("status")).upper() == "CANDIDATE" and _text(
        stored.get("release_checksum")
    ) != _text(candidate_compiled.manifest.get("checksum")):
        raise ReleaseStoreError("RELEASE_RECONSTRUCTION_CHECKSUM_MISMATCH")
    return projection, candidate_compiled, previous_manifest


def publish_operational_release(
    adapter: ReleaseStoreAdapter,
    *,
    release_id: str,
    operator: str,
    published_at: str,
    pathology_bootstrap: Path,
    pathology_bootstrap_checksum: str,
    expected_source_authority_checksum: str,
    expected_curated_authority_checksum: str,
    releases_dir: Path,
) -> ReleaseOperationResult:
    """重建 candidate 后发布；本地与 DB pointer 失败时执行事务补偿。"""

    identity = release_id.strip()
    publisher = operator.strip()
    published_time = _timestamp(
        published_at, code="RELEASE_PUBLISHED_AT_INVALID"
    )
    if not identity or not publisher:
        raise ReleaseStoreError("RELEASE_PUBLISH_ARGUMENT_INVALID")
    try:
        stored = adapter.get_release(identity, lock=True)
        if stored is None:
            raise ReleaseStoreError("RELEASE_NOT_FOUND")
        status = _text(stored.get("status")).upper()
        if status not in {"CANDIDATE", "PUBLISHED"}:
            raise ReleaseStoreError("RELEASE_NOT_PUBLISHABLE")
        if publisher != _text(stored.get("created_by")):
            raise ReleaseStoreError("RELEASE_OPERATOR_MISMATCH")
        if publisher == _text(stored.get("domain_reviewer_id")):
            raise ReleaseStoreError("RELEASE_DUTY_SEPARATION_FAILED")
        if status == "PUBLISHED":
            if _text(stored.get("published_by")) != publisher:
                raise ReleaseStoreError("PUBLISHED_RELEASE_ARGUMENT_MISMATCH")
            _, items = _validated_stored_published_bundle(
                adapter, stored, releases_dir
            )
            pointer = adapter.get_pointer(lock=True)
            if (
                pointer is None
                or _text(pointer.get("active_release_id")) != identity
                or _local_active_release_id(releases_dir) != identity
            ):
                raise ReleaseStoreError("PUBLISHED_RELEASE_NOT_ACTIVE")
            try:
                activate_release(releases_dir, identity)
            except Exception:
                raise ReleaseStoreError("PUBLISHED_RELEASE_LOCAL_INVALID") from None
            adapter.rollback()
            return ReleaseOperationResult(identity, "PUBLISHED", len(items), True)

        projection, candidate_compiled, previous_manifest = _candidate_for_stored_release(
            adapter,
            stored,
            pathology_bootstrap=pathology_bootstrap,
            pathology_bootstrap_checksum=pathology_bootstrap_checksum,
            expected_source_authority_checksum=expected_source_authority_checksum,
            expected_curated_authority_checksum=expected_curated_authority_checksum,
            releases_dir=releases_dir,
        )
        if publisher in projection.reviewer_ids:
            raise ReleaseStoreError("RELEASE_DUTY_SEPARATION_FAILED")

        effective_published_at = published_time
        bundle_dir = releases_dir / identity
        if bundle_dir.exists():
            try:
                prepared = load_release_bundle(releases_dir, identity)
                validate_compiled_release(prepared, require_published=True)
            except Exception:
                raise ReleaseStoreError("PREPARED_RELEASE_BUNDLE_INVALID") from None
            if (
                _text(prepared.manifest.get("created_by"))
                != projection.candidate.created_by
                or _text(prepared.manifest.get("release_operator")) != publisher
                or _text(prepared.manifest.get("source_snapshot_checksum"))
                != projection.candidate.source_snapshot_checksum
                or _item_signature(_release_item_rows(prepared))
                != _item_signature(_release_item_rows(candidate_compiled))
            ):
                raise ReleaseStoreError("PREPARED_RELEASE_BUNDLE_MISMATCH")
            effective_published_at = _timestamp(
                prepared.manifest.get("published_at"),
                code="RELEASE_PUBLISHED_AT_INVALID",
            )
        published_candidate = publish_candidate(
            projection.candidate,
            published_by=publisher,
            published_at=effective_published_at,
        )
        pointer = adapter.get_pointer(lock=True)
        published_compiled = compile_release(
            published_candidate, previous_manifest=previous_manifest
        )
        validate_compiled_release(published_compiled, require_published=True)
        try:
            write_release_bundle(published_compiled, releases_dir)
        except Exception:
            raise ReleaseStoreError("RELEASE_BUNDLE_WRITE_FAILED") from None

        items = adapter.get_release_items(identity, lock=True)
        previous_id = (
            None if pointer is None else _text(pointer.get("active_release_id")) or None
        )
        for entity_type, revision_id, lifecycle in projection.parent_revisions:
            if lifecycle == "RELEASED":
                continue
            if lifecycle != "APPROVED" or not adapter.set_revision_lifecycle(
                entity_type,
                revision_id,
                expected=("APPROVED",),
                lifecycle="RELEASED",
            ):
                raise ReleaseStoreError("REVISION_RELEASE_STATE_RACE")
        adapter.update_release(
            identity,
            {
                "status": "PUBLISHED",
                "published_by": publisher,
                "published_at": effective_published_at,
                "release_checksum": published_compiled.manifest["checksum"],
            },
        )
        adapter.set_pointer(identity, previous_id, publisher)
        try:
            activate_release(releases_dir, identity)
        except Exception:
            adapter.rollback()
            try:
                _restore_local_active(releases_dir, previous_id)
            except Exception:
                raise ReleaseStoreError(
                    "RELEASE_PUBLISH_COMPENSATION_FAILED"
                ) from None
            raise ReleaseStoreError("RELEASE_LOCAL_ACTIVATION_FAILED") from None
        try:
            adapter.commit()
        except Exception:
            adapter.rollback()
            try:
                _restore_local_active(releases_dir, previous_id)
            except Exception:
                raise ReleaseStoreError(
                    "RELEASE_PUBLISH_COMPENSATION_FAILED"
                ) from None
            raise ReleaseStoreError("RELEASE_PUBLISH_COMMIT_FAILED") from None
        return ReleaseOperationResult(identity, "PUBLISHED", len(items), False)
    except ReleaseStoreError:
        adapter.rollback()
        raise
    except Exception:
        adapter.rollback()
        raise ReleaseStoreError("RELEASE_PUBLISH_FAILED") from None


def rollback_operational_release(
    adapter: ReleaseStoreAdapter,
    *,
    target_release_id: str,
    operator: str,
    reason: str,
    occurred_at: str,
    releases_dir: Path,
) -> ReleaseOperationResult:
    """先提交 DB 指针，再切本地；本地失败时恢复 DB release/pointer。"""

    target_id = target_release_id.strip()
    actor = operator.strip()
    rollback_reason = reason.strip()
    occurred = _timestamp(occurred_at, code="ROLLBACK_OCCURRED_AT_INVALID")
    if not target_id or not actor or not rollback_reason:
        raise ReleaseStoreError("RELEASE_ROLLBACK_ARGUMENT_INVALID")
    committed = False
    previous_pointer: Mapping[str, Any] | None = None
    current_before: Mapping[str, Any] | None = None
    target_before: Mapping[str, Any] | None = None
    try:
        pointer = adapter.get_pointer(lock=True)
        if pointer is None:
            raise ReleaseStoreError("ACTIVE_RELEASE_POINTER_MISSING")
        previous_pointer = dict(pointer)
        current_id = _text(pointer.get("active_release_id"))
        local_id = _local_active_release_id(releases_dir)
        if current_id != local_id:
            if (
                target_id != current_id
                or not local_id
                or _text(pointer.get("previous_release_id")) != local_id
            ):
                raise ReleaseStoreError("DEPLOYMENT_POINTER_DIVERGED")
            recovered_target = adapter.get_release(current_id, lock=True)
            recovered_previous = adapter.get_release(local_id, lock=True)
            if (
                recovered_target is None
                or recovered_previous is None
                or _text(recovered_target.get("status")).upper() != "PUBLISHED"
                or _text(recovered_previous.get("status")).upper()
                != "ROLLED_BACK"
            ):
                raise ReleaseStoreError("DEPLOYMENT_POINTER_DIVERGED")
            _, recovered_items = _validated_stored_published_bundle(
                adapter, recovered_target, releases_dir
            )
            _validated_stored_published_bundle(
                adapter, recovered_previous, releases_dir
            )
            try:
                rollback_release(
                    releases_dir,
                    target_release_id=target_id,
                    actor=actor,
                    reason=rollback_reason,
                    occurred_at=occurred,
                )
            except Exception:
                raise ReleaseStoreError("RELEASE_LOCAL_RECOVERY_FAILED") from None
            adapter.rollback()
            return ReleaseOperationResult(
                target_id, "PUBLISHED", len(recovered_items), False
            )
        current = adapter.get_release(current_id, lock=True)
        if current is None:
            raise ReleaseStoreError("ROLLBACK_RELEASE_NOT_FOUND")
        if _text(current.get("status")).upper() != "PUBLISHED":
            raise ReleaseStoreError("ACTIVE_RELEASE_STATUS_INVALID")
        _, current_items = _validated_stored_published_bundle(
            adapter, current, releases_dir
        )
        if target_id == current_id:
            adapter.rollback()
            return ReleaseOperationResult(
                target_id, "PUBLISHED", len(current_items), True
            )
        target = adapter.get_release(target_id, lock=True)
        if target is None:
            raise ReleaseStoreError("ROLLBACK_RELEASE_NOT_FOUND")
        current_before = dict(current)
        target_before = dict(target)
        if _text(target.get("status")).upper() not in {"PUBLISHED", "ROLLED_BACK"}:
            raise ReleaseStoreError("ROLLBACK_TARGET_NOT_PUBLISHED")
        _, target_items = _validated_stored_published_bundle(
            adapter, target, releases_dir
        )
        adapter.update_release(current_id, {"status": "ROLLED_BACK"})
        adapter.update_release(target_id, {"status": "PUBLISHED"})
        adapter.set_pointer(target_id, current_id, actor)
        adapter.commit()
        committed = True
        try:
            rollback_release(
                releases_dir,
                target_release_id=target_id,
                actor=actor,
                reason=rollback_reason,
                occurred_at=occurred,
            )
        except Exception:
            try:
                adapter.update_release(
                    current_id, {"status": current_before["status"]}
                )
                adapter.update_release(
                    target_id, {"status": target_before["status"]}
                )
                adapter.restore_pointer(previous_pointer)
                adapter.commit()
            except Exception:
                adapter.rollback()
                raise ReleaseStoreError(
                    "RELEASE_ROLLBACK_COMPENSATION_FAILED"
                ) from None
            raise ReleaseStoreError("RELEASE_LOCAL_ROLLBACK_FAILED") from None
        return ReleaseOperationResult(
            target_id, "PUBLISHED", len(target_items), False
        )
    except ReleaseStoreError:
        if not committed:
            adapter.rollback()
        raise
    except Exception:
        if not committed:
            adapter.rollback()
        raise ReleaseStoreError("RELEASE_ROLLBACK_FAILED") from None
