"""工作簿 staging、服务端二次校验与事务性 materialization。"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .ids import checksum, import_batch_id
from .workbooks import parse_workbook, validate_workbook


@dataclass
class StagingRow:
    sheet_name: str
    excel_row_no: int
    stable_row_id: str
    canonical_payload: dict[str, Any]
    row_checksum: str


@dataclass
class ImportBatch:
    import_batch_id: str
    workbook_sha256: str
    template_schema_version: str
    safe_basename: str
    uploaded_by: str
    expected_counts: dict[str, int]
    rows: list[StagingRow]
    status: str = "UPLOADED"
    safe_errors: list[str] = field(default_factory=list)
    reconciliation: dict[str, dict[str, int]] = field(default_factory=dict)


def workbook_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def safe_basename(path: Path) -> str:
    return "".join(character if character.isalnum() or character in "._-()" else "_" for character in path.name)


def _row_id(row: dict[str, Any]) -> str:
    for key in (
        "node_id", "branch_id", "drug_product_id", "source_fragment_id", "atom_id",
        "alias_id", "context_id", "component_id", "schedule_component_id", "entity_id",
        "regimen_revision_id", "rule_revision_id",
    ):
        if row.get(key):
            return str(row[key])
    return checksum({key: value for key, value in row.items() if key != "_excel_row"})


def build_staging_rows(parsed: dict[str, Any]) -> list[StagingRow]:
    rows = []
    for sheet_name, sheet_rows in sorted(parsed["sheets"].items()):
        for row in sheet_rows:
            payload = {key: value for key, value in row.items() if key != "_excel_row"}
            rows.append(
                StagingRow(
                    sheet_name=sheet_name,
                    excel_row_no=int(row["_excel_row"]),
                    stable_row_id=_row_id(row),
                    canonical_payload=payload,
                    row_checksum=str(row.get("row_checksum") or checksum(payload)),
                )
            )
    return rows


def dry_run_plan(path: Path, *, kind: str, database: str) -> dict[str, Any]:
    issues = validate_workbook(path, kind=kind)
    parsed = parse_workbook(path, kind=kind) if not issues else None
    counts = {} if parsed is None else {sheet: len(rows) for sheet, rows in parsed["sheets"].items()}
    return {
        "database": database,
        "schemas": ["kb_stg", "kb"],
        "safe_basename": safe_basename(path),
        "workbook_sha256": workbook_sha256(path),
        "template_schema_version": "" if parsed is None else parsed["metadata"].get("template_schema_version"),
        "expected_counts": counts,
        "local_validation_errors": len(issues),
        "contains_phi": False,
        "mutations_planned": 0,
    }


class InMemoryKnowledgeStore:
    """SQL Server 行为测试适配器；用 deep-copy 模拟事务回滚。"""

    def __init__(self) -> None:
        self.batches: dict[str, ImportBatch] = {}
        self._batch_key: dict[tuple[str, str], str] = {}
        self.authoring: dict[str, dict[str, dict[str, Any]]] = {}
        self.approved_revision_ids: set[str] = set()
        self.release_ids: set[str] = set()
        self.deployed_release_id: str | None = None

    def upload(self, path: Path, *, kind: str, uploaded_by: str) -> ImportBatch:
        issues = validate_workbook(path, kind=kind)
        if issues:
            raise ValueError(f"本地验证失败: {len(issues)} errors")
        parsed = parse_workbook(path, kind=kind)
        digest = workbook_sha256(path)
        version = str(parsed["metadata"]["template_schema_version"])
        key = (digest, version)
        if key in self._batch_key:
            return self.batches[self._batch_key[key]]
        rows = build_staging_rows(parsed)
        batch = ImportBatch(
            import_batch_id=import_batch_id(digest, version),
            workbook_sha256=digest,
            template_schema_version=version,
            safe_basename=safe_basename(path),
            uploaded_by=uploaded_by,
            expected_counts={sheet: len(values) for sheet, values in parsed["sheets"].items()},
            rows=rows,
        )
        self.batches[batch.import_batch_id] = batch
        self._batch_key[key] = batch.import_batch_id
        return batch

    def server_validate(
        self,
        batch_id: str,
        validators: list[Callable[[StagingRow, "InMemoryKnowledgeStore"], str | None]] | None = None,
    ) -> ImportBatch:
        batch = self.batches[batch_id]
        errors = []
        for row in batch.rows:
            for validator in validators or []:
                code = validator(row, self)
                if code:
                    errors.append(f"{row.sheet_name}:{row.excel_row_no}:{code}")
        batch.safe_errors = sorted(errors)
        batch.status = "VALIDATION_FAILED" if errors else "VALIDATED"
        return batch

    def materialize(self, batch_id: str, *, fail_on_stable_id: str | None = None) -> ImportBatch:
        batch = self.batches[batch_id]
        if batch.status != "VALIDATED":
            raise ValueError("只有 VALIDATED 批次可 materialize")
        snapshot = copy.deepcopy(self.authoring)
        reconciliation: dict[str, dict[str, int]] = {}
        try:
            for row in batch.rows:
                if row.stable_row_id == fail_on_stable_id:
                    raise RuntimeError("injected materialization failure")
                entity_store = self.authoring.setdefault(row.sheet_name, {})
                counts = reconciliation.setdefault(
                    row.sheet_name,
                    {"staged": 0, "inserted": 0, "reused": 0, "updated_draft": 0, "rejected": 0},
                )
                counts["staged"] += 1
                existing = entity_store.get(row.stable_row_id)
                if existing is None:
                    entity_store[row.stable_row_id] = copy.deepcopy(row.canonical_payload)
                    counts["inserted"] += 1
                elif checksum(existing) == checksum(row.canonical_payload):
                    counts["reused"] += 1
                elif str(existing.get("lifecycle") or "DRAFT") in {"DRAFT", "CHANGES_REQUESTED"}:
                    entity_store[row.stable_row_id] = copy.deepcopy(row.canonical_payload)
                    counts["updated_draft"] += 1
                else:
                    counts["rejected"] += 1
                    raise ValueError("immutable revision update rejected")
            for counts in reconciliation.values():
                accounted = counts["inserted"] + counts["reused"] + counts["updated_draft"] + counts["rejected"]
                if counts["staged"] != accounted or counts["rejected"]:
                    raise ValueError("staging→authoring reconciliation mismatch")
        except Exception:
            self.authoring = snapshot
            batch.status = "FAILED"
            batch.safe_errors = ["MATERIALIZATION_ROLLED_BACK"]
            raise
        batch.reconciliation = reconciliation
        batch.status = "MATERIALIZED"
        return batch

    def approve(self, revision_id: str) -> None:
        self.approved_revision_ids.add(revision_id)

    def register_release(self, release_id: str) -> None:
        self.release_ids.add(release_id)

    def deploy(self, release_id: str) -> None:
        if release_id not in self.release_ids:
            raise ValueError("release 不存在")
        self.deployed_release_id = release_id
