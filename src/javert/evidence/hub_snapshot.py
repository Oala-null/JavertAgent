# -*- coding: utf-8 -*-
"""SQL Server Hub → immutable Evidence Snapshot（只读、无运行时接线）。"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import pandas as pd
from pydantic import Field

from .models import FrozenStrictModel, ValidationIssue
from .serialization import canonical_json_bytes, checksum_value, row_fingerprint, sha256_digest

QUERY_VERSION = "hub-evidence-snapshot-v1"
SOURCE_CONSISTENCY = "read_committed_query_window_no_snapshot_isolation"


class HubSourceProfile(FrozenStrictModel):
    profile_id: Literal["sh_yb_platform-readonly", "tp-data-hub-readonly"]
    database: Literal["sh_yb_platform", "TP_data_hub"]
    version: Literal["1"] = "1"


SOURCE_PROFILES = {
    "sh_yb_platform-readonly": HubSourceProfile(
        profile_id="sh_yb_platform-readonly", database="sh_yb_platform"
    ),
    "tp-data-hub-readonly": HubSourceProfile(
        profile_id="tp-data-hub-readonly", database="TP_data_hub"
    ),
}
DEFAULT_PROFILE_ID = "sh_yb_platform-readonly"


class HubTableSpec(FrozenStrictModel):
    table: str
    primary_key: tuple[str, ...] = Field(min_length=1)
    selected_columns: tuple[str, ...] = Field(min_length=1)
    patient_column: str | None = None


TABLE_SPECS: tuple[HubTableSpec, ...] = (
    HubTableSpec(
        table="TB_DIC_HOSPITAL", primary_key=("YLJGYQDM",),
        selected_columns=("YLJGYQDM", "YYJC"),
    ),
    HubTableSpec(
        table="TB_HIS_ZY_FEE_DETAIL_FS",
        primary_key=("YLJGYQDM", "SFMXID", "STFBZ"), patient_column="JZLSH",
        selected_columns=(
            "YLJGYQDM", "SFMXID", "STFBZ", "JZLSH", "YZID", "MXFYLB",
            "FYFSSJ", "MXXMBM", "MXXMBMYB", "MXXMMC", "MXXMDW", "MXXMDJ",
            "MXXMSL", "MXXMJE",
        ),
    ),
    HubTableSpec(
        table="TB_HIS_ZY_FEE_DETAIL_EXT",
        primary_key=("YLJGYQDM", "SFMXID"), patient_column="JZLSH",
        selected_columns=(
            "YLJGYQDM", "SFMXID", "JZLSH", "PRODNAME", "SPEC", "FEE_TYPE",
            "MEDINS_CHRGITM_TYPE", "SELFPAY_PROP", "CHRGITM_LV", "LIST_TYPE",
            "BILG_DEPT_CODG", "BILG_DEPT_NAME", "BILG_DR_CODG", "BILG_DR_NAME",
            "ACORD_DEPT_CODG", "ACORD_DEPT_NAME", "ORDERS_DR_CODE", "ORDERS_DR_NAME",
        ),
    ),
    HubTableSpec(
        table="TB_CIS_MEDICAL_DOCUMENT",
        primary_key=("YLJGYQDM", "WSLSH"), patient_column="JZLSH",
        selected_columns=(
            "YLJGYQDM", "JZLSH", "WSLSH", "JLSJ", "WSMC", "WSLB", "DLBT", "ZW",
        ),
    ),
    HubTableSpec(
        table="TB_CIS_LEAVEHOSPITAL_SUMMARY",
        primary_key=("YLJGYQDM", "JZLSH"), patient_column="JZLSH",
        selected_columns=(
            "YLJGYQDM", "JZLSH", "CYSJ", "YYZTBBT1", "YYZTB1", "YYZTBBT2", "YYZTB2",
            "RYZD", "CYZD", "RYZZTZ", "JCHZ", "ZLGC", "HBZ", "CYQKMS", "CYYZ", "ZLJGSM",
        ),
    ),
    HubTableSpec(
        table="TB_CIS_DRADVICE_DETAIL",
        primary_key=("YLJGYQDM", "YZID"), patient_column="JZLSH",
        selected_columns=(
            "YLJGYQDM", "YZID", "JZLSH", "YZZH", "YZSM", "MXXMMC", "YZXDSJ",
            "YZZXSJ", "YZZZSJ", "YZLB", "XMMXSL", "XMMXDW",
        ),
    ),
    HubTableSpec(
        table="TB_IH_DIAGNOSIS_DETAIL",
        primary_key=("YLJGYQDM", "ZYZDLSH"), patient_column="JZLSH",
        selected_columns=("YLJGYQDM", "ZYZDLSH", "JZLSH", "ZDBM", "ZDSM", "CYZDBZ"),
    ),
    HubTableSpec(
        table="TB_BA_SYJBK", primary_key=("YLJGYQDM", "SYXH"), patient_column="SYXH",
        selected_columns=("YLJGYQDM", "SYXH", "ZYZD"),
    ),
    HubTableSpec(
        table="TB_BA_SYZDK", primary_key=("YLJGYQDM", "SYXH", "ZDXH"), patient_column="SYXH",
        selected_columns=("YLJGYQDM", "SYXH", "ZDXH", "ZDDM", "ZDMC"),
    ),
)
TABLE_SPEC_BY_NAME = {spec.table: spec for spec in TABLE_SPECS}


class CatalogTable(FrozenStrictModel):
    table: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    permissions: dict[str, bool]


class SourceCapabilities(FrozenStrictModel):
    snapshot_isolation: str
    read_committed_snapshot: bool
    cdc_enabled: bool
    change_tracking_enabled: bool


class HubPreflight(FrozenStrictModel):
    ok: bool
    profile: HubSourceProfile
    capabilities: SourceCapabilities
    schema_checksum: str
    issues: tuple[ValidationIssue, ...] = ()


class SnapshotArtifact(FrozenStrictModel):
    artifact_id: str
    kind: Literal["raw", "canonical", "lineage"]
    relative_path: str
    row_count: int = Field(ge=0)
    columns: tuple[str, ...]
    primary_key: tuple[str, ...] = ()
    schema_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class LineageRecord(FrozenStrictModel):
    source_artifact_id: str
    row_ordinal: int = Field(ge=0)
    row_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    database: str
    table: str
    primary_key_columns: tuple[str, ...]
    primary_key_values: tuple[str, ...]
    selected_columns: tuple[str, ...]
    query_version: str
    row_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SnapshotManifest(FrozenStrictModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    status: Literal["COMPLETE", "INVALID"]
    snapshot_id: str
    snapshot_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_profile: str
    database: str
    query_version: str
    code_version: str
    cohort_query_version: str
    cohort_query_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    query_started_at: str
    query_finished_at: str
    source_consistency: Literal["read_committed_query_window_no_snapshot_isolation"] = SOURCE_CONSISTENCY
    atomic_snapshot: Literal[False] = False
    capabilities: SourceCapabilities
    preflight_schema_checksum: str
    cohort_size: int = Field(ge=1)
    artifacts: tuple[SnapshotArtifact, ...]
    issues: tuple[ValidationIssue, ...] = ()


class SnapshotSummary(FrozenStrictModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    status: Literal["complete", "blocked"]
    source_profile: str
    database: str
    permission_preflight: Literal["passed", "failed"]
    source_consistency: str
    atomic_snapshot: Literal[False] = False
    query_started_at: str
    query_finished_at: str
    cohort_size: int = Field(ge=0)
    snapshot_id: str = ""
    artifact_counts: dict[str, int] = Field(default_factory=dict)
    artifact_digests: dict[str, str] = Field(default_factory=dict)
    error_codes: tuple[str, ...] = ()
    interpretation: str = (
        "工程 snapshot mechanics；不构成代表性 cohort、临床 A/B 或生产授权。"
    )


def evaluate_preflight(
    *, profile: HubSourceProfile, database_name: str,
    catalog: Mapping[str, CatalogTable], capabilities: SourceCapabilities,
) -> HubPreflight:
    issues: list[ValidationIssue] = []
    if database_name != profile.database:
        issues.append(ValidationIssue(code="HUB_PROFILE_DATABASE_MISMATCH", path="database"))
    for spec in TABLE_SPECS:
        table = catalog.get(spec.table)
        if table is None:
            issues.append(ValidationIssue(code="HUB_REQUIRED_TABLE_MISSING", record_id=spec.table))
            continue
        missing = sorted(set(spec.selected_columns) - set(table.columns))
        if missing:
            issues.append(ValidationIssue(code="HUB_REQUIRED_COLUMN_MISSING", record_id=spec.table))
        if table.primary_key != spec.primary_key:
            issues.append(ValidationIssue(code="HUB_PRIMARY_KEY_MISMATCH", record_id=spec.table))
        perms = table.permissions
        if not perms.get("select", False):
            issues.append(ValidationIssue(code="HUB_SELECT_PERMISSION_REQUIRED", record_id=spec.table))
        if any(perms.get(action, False) for action in ("insert", "update", "delete")):
            issues.append(ValidationIssue(code="HUB_DML_PERMISSION_FORBIDDEN", record_id=spec.table))
    schema_payload = [
        {
            "table": spec.table,
            "primary_key": spec.primary_key,
            "selected_columns": spec.selected_columns,
            "actual_columns": catalog[spec.table].columns if spec.table in catalog else (),
        }
        for spec in TABLE_SPECS
    ]
    ordered = tuple(sorted(issues, key=lambda issue: (issue.code, issue.record_id, issue.path)))
    return HubPreflight(
        ok=not ordered, profile=profile, capabilities=capabilities,
        schema_checksum=checksum_value(schema_payload), issues=ordered,
    )


def inspect_connection(connection: Any, profile: HubSourceProfile) -> HubPreflight:
    cursor = connection.cursor()
    cursor.execute("SELECT DB_NAME()")
    database_name = str(cursor.fetchone()[0])
    cursor.execute(
        "SELECT snapshot_isolation_state_desc,is_read_committed_snapshot_on,is_cdc_enabled "
        "FROM sys.databases WHERE name=DB_NAME()"
    )
    db = cursor.fetchone()
    try:
        cursor.execute("SELECT COUNT(*) FROM sys.change_tracking_databases WHERE database_id=DB_ID()")
        change_tracking = bool(cursor.fetchone()[0])
    except Exception:
        change_tracking = False
    capabilities = SourceCapabilities(
        snapshot_isolation=str(db[0]), read_committed_snapshot=bool(db[1]),
        cdc_enabled=bool(db[2]), change_tracking_enabled=change_tracking,
    )
    names = [spec.table for spec in TABLE_SPECS]
    placeholders = ",".join("?" for _ in names)
    cursor.execute(
        f"SELECT t.name,c.name FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id "
        f"WHERE t.name IN ({placeholders}) ORDER BY t.name,c.column_id",
        tuple(names),
    )
    columns: dict[str, list[str]] = {}
    for table, column in cursor.fetchall():
        columns.setdefault(str(table), []).append(str(column))
    cursor.execute(
        f"SELECT t.name,ic.key_ordinal,c.name FROM sys.tables t "
        "JOIN sys.indexes i ON i.object_id=t.object_id AND i.is_primary_key=1 "
        "JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id "
        "JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id "
        f"WHERE t.name IN ({placeholders}) ORDER BY t.name,ic.key_ordinal",
        tuple(names),
    )
    primary_keys: dict[str, list[str]] = {}
    for table, _ordinal, column in cursor.fetchall():
        primary_keys.setdefault(str(table), []).append(str(column))
    catalog: dict[str, CatalogTable] = {}
    for spec in TABLE_SPECS:
        if spec.table not in columns:
            continue
        object_name = f"dbo.{spec.table}"
        cursor.execute(
            "SELECT HAS_PERMS_BY_NAME(?,'OBJECT','SELECT'),"
            "HAS_PERMS_BY_NAME(?,'OBJECT','INSERT'),"
            "HAS_PERMS_BY_NAME(?,'OBJECT','UPDATE'),"
            "HAS_PERMS_BY_NAME(?,'OBJECT','DELETE')",
            object_name, object_name, object_name, object_name,
        )
        row = cursor.fetchone()
        catalog[spec.table] = CatalogTable(
            table=spec.table, columns=tuple(columns[spec.table]),
            primary_key=tuple(primary_keys.get(spec.table, ())),
            permissions={
                "select": bool(row[0]), "insert": bool(row[1]),
                "update": bool(row[2]), "delete": bool(row[3]),
            },
        )
    return evaluate_preflight(
        profile=profile, database_name=database_name,
        catalog=catalog, capabilities=capabilities,
    )


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return value.hex()
    return value


def _safe_key(row: Mapping[str, Any], columns: Sequence[str]) -> tuple[str, ...]:
    values = tuple("" if row.get(column) is None else str(row[column]).strip() for column in columns)
    if any(not value for value in values):
        raise ValueError("HUB_PRIMARY_KEY_EMPTY")
    return values


def validate_ordered_rows(rows: Sequence[Mapping[str, Any]], spec: HubTableSpec) -> None:
    keys = [_safe_key(row, spec.primary_key) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("HUB_PRIMARY_KEY_DUPLICATE")
    if keys != sorted(keys):
        raise ValueError("HUB_PRIMARY_KEY_ORDER_INVALID")


def read_table_rows(
    connection: Any, spec: HubTableSpec, patient_ids: Sequence[str],
) -> list[dict[str, Any]]:
    columns = ",".join(f"[{column}]" for column in spec.selected_columns)
    order = ",".join(f"[{column}]" for column in spec.primary_key)
    params: tuple[Any, ...] = ()
    where = ""
    if spec.patient_column:
        if not patient_ids:
            return []
        placeholders = ",".join("?" for _ in patient_ids)
        where = f" WHERE [{spec.patient_column}] IN ({placeholders})"
        params = tuple(patient_ids)
    cursor = connection.cursor()
    cursor.execute(
        f"SELECT {columns} FROM [dbo].[{spec.table}]{where} ORDER BY {order}",
        params,
    )
    names = [item[0] for item in cursor.description]
    rows = [
        {name: _normalize(value) for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]
    validate_ordered_rows(rows, spec)
    return rows


def _private_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
    os.chmod(path, 0o600)


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def write_raw_and_lineage(
    *, output_dir: Path, profile: HubSourceProfile,
    raw_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[SnapshotArtifact], SnapshotArtifact, list[str]]:
    artifacts: list[SnapshotArtifact] = []
    lineage_records: list[LineageRecord] = []
    private_values: list[str] = []
    for spec in TABLE_SPECS:
        rows = list(raw_rows.get(spec.table, ()))
        for row in rows:
            if not set(spec.selected_columns).issubset(row):
                raise ValueError("HUB_REQUIRED_COLUMN_MISSING")
        validate_ordered_rows(rows, spec)
        path = output_dir / "raw" / f"{spec.table}.jsonl"
        payload = b"".join(canonical_json_bytes(dict(row)) for row in rows)
        _private_write(path, payload)
        artifact_id = f"source:{profile.profile_id}:{spec.table}:{QUERY_VERSION}"
        for ordinal, row in enumerate(rows):
            key_values = _safe_key(row, spec.primary_key)
            private_values.extend(key_values)
            lineage_records.append(LineageRecord(
                source_artifact_id=artifact_id, row_ordinal=ordinal,
                row_fingerprint=row_fingerprint(row), database=profile.database,
                table=spec.table, primary_key_columns=spec.primary_key,
                primary_key_values=key_values, selected_columns=spec.selected_columns,
                query_version=QUERY_VERSION, row_checksum=checksum_value(dict(row)),
            ))
        artifacts.append(SnapshotArtifact(
            artifact_id=artifact_id, kind="raw",
            relative_path=path.relative_to(output_dir).as_posix(), row_count=len(rows),
            columns=spec.selected_columns, primary_key=spec.primary_key,
            schema_checksum=checksum_value({
                "columns": spec.selected_columns, "primary_key": spec.primary_key,
            }),
            content_checksum=_file_checksum(path),
        ))
    lineage_path = output_dir / "lineage" / "source_rows.jsonl"
    lineage_payload = b"".join(canonical_json_bytes(item) for item in lineage_records)
    _private_write(lineage_path, lineage_payload)
    lineage_artifact = SnapshotArtifact(
        artifact_id=f"lineage:{profile.profile_id}:{QUERY_VERSION}", kind="lineage",
        relative_path=lineage_path.relative_to(output_dir).as_posix(),
        row_count=len(lineage_records), columns=tuple(LineageRecord.model_fields),
        schema_checksum=checksum_value(tuple(LineageRecord.model_fields)),
        content_checksum=_file_checksum(lineage_path),
    )
    return artifacts, lineage_artifact, private_values


_CANONICAL_SORT_COLUMNS = {
    "shi_fee.csv": ("bah", "id", "fee_ocur_time", "medins_list_name", "cnt"),
    "case_notes.csv": ("住院号", "事件时间", "阶段", "子阶段", "内容", "来源文件"),
    "shi_zd.csv": ("ba_id", "ipt_medcas_hmpg_sn", "diag_code", "diag_name"),
}


def _stable_frame(frame: pd.DataFrame, preferred: Sequence[str]) -> pd.DataFrame:
    result = frame.copy()
    columns = [column for column in preferred if column in result.columns]
    if not result.empty:
        sort_columns = columns or list(result.columns)
        keys = result[sort_columns].fillna("").astype(str).agg("\x1f".join, axis=1)
        result = result.assign(_snapshot_sort_key=keys).sort_values(
            "_snapshot_sort_key", kind="mergesort"
        ).drop(columns="_snapshot_sort_key").reset_index(drop=True)
    return result


def write_canonical_frames(
    *, output_dir: Path, frames: Mapping[str, pd.DataFrame],
) -> list[SnapshotArtifact]:
    artifacts: list[SnapshotArtifact] = []
    for name in ("shi_fee.csv", "case_notes.csv", "shi_zd.csv"):
        frame = _stable_frame(frames[name], _CANONICAL_SORT_COLUMNS[name])
        path = output_dir / "canonical" / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False, lineterminator="\n")
        os.chmod(path, 0o600)
        artifacts.append(SnapshotArtifact(
            artifact_id=f"canonical:{name}:{QUERY_VERSION}", kind="canonical",
            relative_path=path.relative_to(output_dir).as_posix(),
            row_count=len(frame), columns=tuple(str(column) for column in frame.columns),
            schema_checksum=checksum_value(tuple(str(column) for column in frame.columns)),
            content_checksum=_file_checksum(path),
        ))
    return artifacts


def _bare_patient(value: Any, cohort: set[str]) -> str:
    text = str(value or "").strip()
    if text in cohort:
        return text
    matches = [patient for patient in cohort if text.endswith(f"-{patient}")]
    return matches[0] if len(matches) == 1 else text


def validate_canonical_coverage(
    *, cohort: Sequence[str], frames: Mapping[str, pd.DataFrame],
    raw_rows: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> tuple[ValidationIssue, ...]:
    cohort_set = set(cohort)
    issues: list[ValidationIssue] = []
    domains = {
        "shi_fee.csv": {_bare_patient(value, cohort_set) for value in frames["shi_fee.csv"].get("bah", [])},
        "case_notes.csv": {_bare_patient(value, cohort_set) for value in frames["case_notes.csv"].get("住院号", [])},
        "shi_zd.csv": {_bare_patient(value, cohort_set) for value in frames["shi_zd.csv"].get("ba_id", [])},
    }
    for name, patients in domains.items():
        patients.discard("")
        if not patients.issubset(cohort_set):
            issues.append(ValidationIssue(code="SNAPSHOT_CANONICAL_COHORT_ESCAPE", record_id=name))
    if domains["shi_fee.csv"] != cohort_set:
        issues.append(ValidationIssue(code="SNAPSHOT_FEE_COHORT_INCOMPLETE", record_id="shi_fee.csv"))
    if raw_rows is not None:
        if len(frames["shi_fee.csv"]) != len(raw_rows.get("TB_HIS_ZY_FEE_DETAIL_FS", ())):
            issues.append(ValidationIssue(code="SNAPSHOT_FEE_ROW_COUNT_MISMATCH", record_id="shi_fee.csv"))
        diagnosis_source_count = sum(
            len(raw_rows.get(table, ()))
            for table in ("TB_IH_DIAGNOSIS_DETAIL", "TB_BA_SYJBK", "TB_BA_SYZDK")
        )
        if len(frames["shi_zd.csv"]) > diagnosis_source_count:
            issues.append(ValidationIssue(code="SNAPSHOT_DIAGNOSIS_ROW_COUNT_INVALID", record_id="shi_zd.csv"))
        note_source_count = sum(
            len(raw_rows.get(table, ()))
            for table in (
                "TB_CIS_MEDICAL_DOCUMENT", "TB_CIS_LEAVEHOSPITAL_SUMMARY",
                "TB_CIS_DRADVICE_DETAIL",
            )
        )
        if len(frames["case_notes.csv"]) and note_source_count == 0:
            issues.append(ValidationIssue(code="SNAPSHOT_NOTE_SOURCE_MISSING", record_id="case_notes.csv"))
    return tuple(sorted(issues, key=lambda item: (item.code, item.record_id)))


def snapshot_content_payload(
    *, profile: HubSourceProfile, code_version: str,
    cohort_query_version: str, cohort_query_checksum: str,
    artifacts: Sequence[SnapshotArtifact],
) -> dict[str, Any]:
    return {
        "source_profile": profile.profile_id,
        "database": profile.database,
        "query_version": QUERY_VERSION,
        "code_version": code_version,
        "cohort_query_version": cohort_query_version,
        "cohort_query_checksum": cohort_query_checksum,
        "artifacts": [
            {
                "artifact_id": item.artifact_id,
                "row_count": item.row_count,
                "schema_checksum": item.schema_checksum,
                "content_checksum": item.content_checksum,
            }
            for item in sorted(artifacts, key=lambda value: value.artifact_id)
        ],
    }


def _inside_git(path: Path) -> bool:
    return any((parent / ".git").exists() for parent in (path.parent, *path.parents))


def _assert_new_external_dir(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError("SNAPSHOT_OUTPUT_NOT_EMPTY")
    if _inside_git(path):
        raise ValueError("SNAPSHOT_OUTPUT_INSIDE_GIT")


def _assert_no_private_values(value: Any, private_values: Sequence[str]) -> None:
    text = canonical_json_bytes(value).decode("utf-8")
    for private in private_values:
        candidate = str(private).strip()
        if len(candidate) >= 6 and candidate in text:
            raise ValueError("SNAPSHOT_PUBLIC_PHI_LEAK")


def build_snapshot_bundle(
    *, output_dir: Path, preflight: HubPreflight,
    raw_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    canonical_frames: Mapping[str, pd.DataFrame], cohort: Sequence[str],
    code_version: str, cohort_query_version: str,
    query_started_at: str, query_finished_at: str,
) -> tuple[SnapshotManifest, SnapshotSummary]:
    if not preflight.ok:
        raise ValueError("SNAPSHOT_PREFLIGHT_FAILED")
    if not cohort:
        raise ValueError("SNAPSHOT_COHORT_EMPTY")
    _assert_new_external_dir(output_dir)
    created = not output_dir.exists()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_dir, 0o700)
    try:
        raw_artifacts, lineage, private_values = write_raw_and_lineage(
            output_dir=output_dir, profile=preflight.profile, raw_rows=raw_rows,
        )
        canonical_artifacts = write_canonical_frames(
            output_dir=output_dir, frames=canonical_frames,
        )
        coverage_issues = validate_canonical_coverage(
            cohort=cohort, frames=canonical_frames, raw_rows=raw_rows,
        )
        if coverage_issues:
            raise ValueError(coverage_issues[0].code)
        artifacts = tuple((*raw_artifacts, *canonical_artifacts, lineage))
        cohort_query_checksum = checksum_value({
            "query_version": cohort_query_version,
            "cohort_size": len(cohort),
            "cohort_identity": checksum_value(sorted(cohort)),
        })
        content_payload = snapshot_content_payload(
            profile=preflight.profile, code_version=code_version,
            cohort_query_version=cohort_query_version,
            cohort_query_checksum=cohort_query_checksum, artifacts=artifacts,
        )
        snapshot_checksum = checksum_value(content_payload)
        snapshot_id = "snapshot-" + snapshot_checksum[7:23]
        manifest = SnapshotManifest(
            status="COMPLETE", snapshot_id=snapshot_id,
            snapshot_checksum=snapshot_checksum,
            source_profile=preflight.profile.profile_id,
            database=preflight.profile.database, query_version=QUERY_VERSION,
            code_version=code_version, cohort_query_version=cohort_query_version,
            cohort_query_checksum=cohort_query_checksum,
            query_started_at=query_started_at, query_finished_at=query_finished_at,
            capabilities=preflight.capabilities,
            preflight_schema_checksum=preflight.schema_checksum,
            cohort_size=len(cohort), artifacts=artifacts,
        )
        _assert_no_private_values(manifest, (*cohort, *private_values))
        _private_write(output_dir / "manifest.json", canonical_json_bytes(manifest))
        summary = SnapshotSummary(
            status="complete", source_profile=preflight.profile.profile_id,
            database=preflight.profile.database, permission_preflight="passed",
            source_consistency=SOURCE_CONSISTENCY,
            query_started_at=query_started_at, query_finished_at=query_finished_at,
            cohort_size=len(cohort), snapshot_id=snapshot_id,
            artifact_counts={item.artifact_id: item.row_count for item in artifacts},
            artifact_digests={item.artifact_id: item.content_checksum for item in artifacts},
        )
        _assert_no_private_values(summary, (*cohort, *private_values))
        return manifest, summary
    except Exception:
        if created or not (output_dir / "manifest.json").exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise


def read_snapshot_manifest(path: Path) -> SnapshotManifest:
    return SnapshotManifest.model_validate_json(path.read_text(encoding="utf-8"))


def validate_snapshot_pair(left: SnapshotManifest, right: SnapshotManifest) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for label, item in (("left", left), ("right", right)):
        if item.status != "COMPLETE":
            issues.append(ValidationIssue(code="SNAPSHOT_NOT_COMPLETE", path=label))
    if left.source_profile != right.source_profile or left.database != right.database:
        issues.append(ValidationIssue(code="SNAPSHOT_SOURCE_PROFILE_MISMATCH"))
    if left.snapshot_id != right.snapshot_id or left.snapshot_checksum != right.snapshot_checksum:
        issues.append(ValidationIssue(code="SNAPSHOT_CONTENT_MISMATCH"))
    return tuple(sorted(issues, key=lambda item: (item.code, item.path)))


def read_all_raw_rows(connection: Any, patient_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    return {
        spec.table: read_table_rows(connection, spec, patient_ids)
        for spec in TABLE_SPECS
    }
