#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 142 数据中台可复现地生成 RD04 直接 shadow 报告与无 PHI manifest.

候选全集先按国家码精确命中，加“无码费用完整通用名”兜底粗筛；随后复用
``lookup_patient_drugs`` 做退费净额、RD04 ownership 和结构化资格求值。
所有含患者标识的中间文件只写入 0700 临时目录，文件为 0600，默认退出即删除。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from javert.config import JavertConfig, get_config  # noqa: E402
from javert.data import hub_source as hs  # noqa: E402
from javert.data.csv_loader import CsvLoader  # noqa: E402
from javert.oncology.knowledge import canonical_json_bytes  # noqa: E402
from javert.tools.drug_audit_lookup import lookup_patient_drugs  # noqa: E402
from scripts.oncology_shadow_report import build_direct_report  # noqa: E402
from scripts.prescan_med_rst import insurance_oncology_drugs  # noqa: E402


DEFAULT_REPORT = ROOT / "docs" / "oncology" / "shadow_comparison.json"
DEFAULT_MANIFEST = ROOT / "docs" / "oncology" / "shadow_run_manifest.json"
CANDIDATE_QUERY_VERSION = "rd04-hub-candidate-v1"
EXPECTED_COUNTS_ORIGIN = "pre_evaluation_lookup_matches"
SOURCE_CONSISTENCY = "read_committed_query_window_no_snapshot_isolation"
SOURCE_TABLES = [
    "TB_HIS_ZY_FEE_DETAIL_FS",
    "TB_HIS_ZY_FEE_DETAIL_EXT",
    "TB_CIS_MEDICAL_DOCUMENT",
    "TB_CIS_LEAVEHOSPITAL_SUMMARY",
    "TB_IH_DIAGNOSIS_DETAIL",
    "TB_BA_SYJBK",
    "TB_BA_SYZDK",
]
KNOWLEDGE_ASSET_NAMES = (
    "drug_audit_kb.json",
    "oncology_eligibility_rules.json",
    "pathology_biomarker_kb.json",
    "oncology_regimen_kb.json",
)


@contextmanager
def secure_workspace(parent: Path | None = None) -> Iterator[Path]:
    """创建 0700 工作区并在任何正常/异常退出时递归清理."""
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    raw = tempfile.mkdtemp(
        prefix="javert-oncology-shadow-",
        dir=str(parent) if parent is not None else None,
    )
    path = Path(raw)
    path.chmod(0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _private_fd(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    os.chmod(path, 0o600)
    return fd


def write_private_json(path: Path, payload: Any) -> None:
    with os.fdopen(_private_fd(path), "wb") as handle:
        handle.write(canonical_json_bytes(payload))


def write_private_csv(path: Path, frame: pd.DataFrame) -> None:
    with os.fdopen(
        _private_fd(path),
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        frame.to_csv(handle, index=False)


def _query_dicts(
    connection: Any,
    sql: str,
    params: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    cursor.execute(sql, tuple(params))
    columns = [item[0] for item in cursor.description]
    return [
        {
            column: ("" if value is None else value)
            for column, value in zip(columns, row)
        }
        for row in cursor.fetchall()
    ]


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("[", "\\[")
    )


def discover_coarse_candidate_ids(
    connection: Any,
    drugs: dict[str, set[str]],
) -> list[str]:
    """按 RD04 两条候选路径取患者号，不在报告或 manifest 中落原始 ID."""
    codes = sorted(set().union(*drugs.values())) if drugs else []
    names = sorted(drugs)
    if not codes and not names:
        return []

    conditions: list[str] = []
    params: list[str] = []
    if codes:
        conditions.append(
            "LTRIM(RTRIM(COALESCE(f.MXXMBMYB, N''))) "
            f"IN ({','.join('?' for _ in codes)})"
        )
        params.extend(codes)
    if names:
        name_conditions = " OR ".join(
            "f.MXXMMC LIKE ? ESCAPE '\\'" for _ in names
        )
        conditions.append(
            "(LOWER(LTRIM(RTRIM(COALESCE(f.MXXMBMYB, N'')))) "
            f"IN (N'', N'nan', N'none', N'null') AND ({name_conditions}))"
        )
        params.extend(f"%{_escape_like(name)}%" for name in names)

    rows = _query_dicts(
        connection,
        "SELECT DISTINCT LTRIM(RTRIM(f.JZLSH)) AS patient_id "
        "FROM TB_HIS_ZY_FEE_DETAIL_FS f "
        "WHERE NULLIF(LTRIM(RTRIM(COALESCE(f.JZLSH, N''))), N'') "
        "IS NOT NULL AND ("
        + " OR ".join(conditions)
        + ") ORDER BY patient_id",
        params,
    )
    return sorted(
        {
            str(row["patient_id"]).strip()
            for row in rows
            if str(row["patient_id"]).strip()
        }
    )


def _chunks(values: Sequence[str], size: int = 100) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _concat(parts: list[pd.DataFrame]) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def fetch_candidate_frames(
    connection: Any,
    patient_ids: Sequence[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """复用 hub_source 映射，按小批次取费用、文书和诊断."""
    yq2org = hs.fetch_hospital_map(connection)
    fees: list[pd.DataFrame] = []
    notes: list[pd.DataFrame] = []
    diagnoses: list[pd.DataFrame] = []
    for chunk in _chunks(patient_ids):
        fees.append(hs.fetch_fees(connection, chunk, yq2org))
        notes.append(hs.fetch_notes(connection, chunk))
        diagnoses.append(hs.fetch_zd(connection, chunk, yq2org))
    return _concat(fees), _concat(notes), _concat(diagnoses)


def source_server_time(connection: Any) -> str:
    rows = _query_dicts(
        connection,
        "SELECT CONVERT(NVARCHAR(33), SYSUTCDATETIME(), 127) "
        "AS server_time",
    )
    value = str(rows[0]["server_time"]) if rows else ""
    return value if value.endswith("Z") else f"{value}Z"


def load_historical_context(
    connection: Any,
    patient_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """读取每患者最新历史 RD04 和最新专家复核；仅在私密内存/临时文件存在."""
    history: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(patient_ids, size=500):
        placeholders = ",".join("?" for _ in chunk)
        latest_runs = _query_dicts(
            connection,
            "WITH ranked AS ("
            " SELECT run_id, patient_id, verdict,"
            " ROW_NUMBER() OVER (PARTITION BY patient_id"
            " ORDER BY created_at DESC, id DESC) AS rn"
            " FROM javert_audit_runs"
            " WHERE rule_id=N'RD04' AND patient_id IN ("
            + placeholders
            + ")) SELECT run_id, patient_id, verdict FROM ranked WHERE rn=1",
            chunk,
        )
        for row in latest_runs:
            patient_id = str(row["patient_id"])
            history.setdefault(patient_id, {}).update(
                {
                    "run_id": str(row["run_id"]),
                    "verdict": str(row["verdict"]),
                }
            )

        latest_reviews = _query_dicts(
            connection,
            "WITH ranked AS ("
            " SELECT ar.patient_id, rv.run_id, ar.verdict"
            " AS review_run_old_verdict, rv.review_verdict,"
            " ROW_NUMBER() OVER (PARTITION BY ar.patient_id"
            " ORDER BY rv.created_at DESC, rv.id DESC) AS rn"
            " FROM javert_vio_review rv"
            " INNER JOIN javert_audit_runs ar ON ar.run_id=rv.run_id"
            " WHERE ar.rule_id=N'RD04' AND rv.is_latest=1"
            " AND ar.patient_id IN ("
            + placeholders
            + ")) SELECT patient_id, run_id, review_run_old_verdict,"
            " review_verdict FROM ranked WHERE rn=1",
            chunk,
        )
        for row in latest_reviews:
            patient_id = str(row["patient_id"])
            history.setdefault(patient_id, {})["latest_expert_review"] = {
                "run_id": str(row["run_id"]),
                "review_run_old_verdict": str(
                    row["review_run_old_verdict"]
                ),
                "review_verdict": str(row["review_verdict"]),
            }
    return history


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_metadata(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    metadata = payload.get("metadata") or {}
    return {
        "sha256": _sha256(path),
        "schema_version": (
            metadata.get("schema_version")
            or payload.get("schema_version")
            or ""
        ),
        "content_version": (
            metadata.get("content_version")
            or payload.get("content_version")
            or payload.get("version")
            or ""
        ),
    }


def git_state(
    root: Path = ROOT,
    *,
    excluded_paths: Sequence[Path] = (),
) -> tuple[str, bool, dict[str, Any]]:
    root = root.resolve()
    excluded: list[str] = []
    for path in excluded_paths:
        absolute = path.resolve() if path.is_absolute() else (root / path).resolve()
        try:
            excluded.append(absolute.relative_to(root).as_posix())
        except ValueError:
            continue
    excluded = sorted(set(excluded))
    pathspecs = [".", *(f":(exclude){path}" for path in excluded)]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=normal",
            "--",
            *pathspecs,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    tracked_diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--", *pathspecs],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    untracked_raw = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *pathspecs,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    untracked_paths = [
        root / os.fsdecode(value)
        for value in untracked_raw.split(b"\0")
        if value
    ]
    untracked_digest = hashlib.sha256()
    for path in sorted(untracked_paths, key=lambda item: str(item)):
        relative = os.fsencode(str(path.relative_to(root)))
        untracked_digest.update(relative)
        untracked_digest.update(b"\0")
        untracked_digest.update(
            hashlib.sha256(path.read_bytes()).digest()
            if path.is_file()
            else b"<non-file>"
        )
        untracked_digest.update(b"\0")
    tracked_sha = hashlib.sha256(tracked_diff).hexdigest()
    untracked_sha = untracked_digest.hexdigest()
    combined = hashlib.sha256(
        (
            f"{commit}\0{tracked_sha}\0{untracked_sha}\0"
            f"{len(untracked_paths)}"
        ).encode("ascii")
    ).hexdigest()
    clean = not bool(status.strip())
    return commit, clean, {
        "algorithm": "git-head-dirty-content-sha256-v1",
        "fingerprint": combined,
        "tracked_diff_sha256": tracked_sha,
        "untracked_aggregate_sha256": untracked_sha,
        "untracked_file_count": len(untracked_paths),
        "excluded_generated_outputs": excluded,
    }


def build_run_manifest(
    *,
    source_summary: dict[str, Any],
    report: dict[str, Any],
    source_database: str,
    history_database: str,
    asset_paths: Sequence[Path],
    code_commit: str,
    working_tree_clean: bool,
    working_tree_fingerprint: dict[str, Any],
    run_ref: str | None = None,
) -> dict[str, Any]:
    """构造不接受患者级输入的 manifest，避免误把 ID/原文带入 Git."""
    return {
        "schema_version": "1.0.0",
        "run_ref": run_ref or f"shadow-{uuid.uuid4().hex[:12]}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code": {
            "commit": code_commit,
            "working_tree_clean": working_tree_clean,
            "working_tree_scope": (
                "生成的 report/manifest 已排除，避免输出自引用"
            ),
            "working_tree_fingerprint": working_tree_fingerprint,
        },
        "knowledge_assets": {
            path.name: _asset_metadata(path)
            for path in sorted(asset_paths, key=lambda item: item.name)
        },
        "source": {
            "database": source_database,
            "history_database": history_database,
            "source_tables": SOURCE_TABLES,
            "source_query_started_at": source_summary.get(
                "source_query_started_at", ""
            ),
            "source_query_finished_at": source_summary.get(
                "source_query_finished_at", ""
            ),
            "source_consistency": SOURCE_CONSISTENCY,
            "snapshot_isolation": False,
            "candidate_query_version": CANDIDATE_QUERY_VERSION,
            "candidate_query_modes": [
                "national_code_exact",
                "uncoded_full_generic_name",
            ],
            "expected_counts_origin": EXPECTED_COUNTS_ORIGIN,
        },
        "counts": {
            "coarse_candidate_patient_count": source_summary.get(
                "coarse_candidate_patient_count", 0
            ),
            "confirmed_net_positive_patient_count": source_summary.get(
                "confirmed_net_positive_patient_count", 0
            ),
            "candidate_evaluation_count": source_summary.get(
                "candidate_evaluation_count", 0
            ),
            "validation_error_count": report.get(
                "validation_error_count", 0
            ),
            "duplicate_count": report.get("duplicate_count", 0),
            "old_new_comparable_patient_count": report.get(
                "old_new_comparable_patient_count", 0
            ),
        },
        "report_status": report.get("status", "blocked"),
        "comparison_design": "historical_unpaired",
        "phi_policy": (
            "manifest 不接收或输出患者级标识、salt、凭据、病历原文、费用明细；"
            "原始中间数据只存在于自动清理的私密临时目录。"
        ),
    }


def _assert_no_raw_identifiers(
    payload: Any,
    identifiers: Sequence[str],
    *,
    salt: str,
) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    forbidden = [value for value in identifiers if value]
    forbidden.append(salt)
    if any(value in encoded for value in forbidden):
        raise RuntimeError("去标识校验失败：公开产物仍含原始标识或 salt")


def _write_public_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def run_batch(
    *,
    report_path: Path,
    manifest_path: Path,
    salt: str,
    temp_root: Path | None = None,
    env_file: Path | None = None,
    hub_database: str | None = None,
    history_database: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if env_file is not None and not env_file.is_file():
        raise FileNotFoundError(f"env 文件不存在: {env_file}")
    if (
        temp_root is not None
        and temp_root.resolve().is_relative_to(ROOT.resolve())
    ):
        raise ValueError("临时目录必须位于 Git 工作区之外")
    cfg = (
        JavertConfig(_env_file=str(env_file))
        if env_file is not None
        else get_config()
    )
    source_database = hub_database or cfg.hub_database
    result_database = history_database or cfg.sql_database
    configs = cfg.resolve("configs")
    asset_paths = [configs / name for name in KNOWLEDGE_ASSET_NAMES]
    drug_kb_path, eligibility_path, pathology_path, regimen_path = asset_paths
    drugs = insurance_oncology_drugs(drug_kb_path)

    hub = hs.connect(cfg, database=source_database)
    try:
        query_started_at = source_server_time(hub)
        coarse_ids = discover_coarse_candidate_ids(hub, drugs)
        fees, notes, diagnoses = fetch_candidate_frames(hub, coarse_ids)
        query_finished_at = source_server_time(hub)
    finally:
        hub.close()

    with secure_workspace(temp_root) as workspace:
        fees_path = workspace / "shi_fee.csv"
        notes_path = workspace / "case_notes.csv"
        zd_path = workspace / "shi_zd.csv"
        write_private_csv(fees_path, fees)
        write_private_csv(notes_path, notes)
        write_private_csv(zd_path, diagnoses)

        loader = CsvLoader(notes_path, fees_path)
        shadow_runs: list[dict[str, Any]] = []
        expected_candidate_count = 0
        confirmed_ids: list[str] = []
        for patient_id in coarse_ids:
            result = lookup_patient_drugs(
                patient_id,
                loader,
                drug_kb_path,
                zd_path,
                rule_type="限适应症",
                source_type="insurance",
                oncology_v2_mode="shadow",
                audit_rule_id="RD04",
                eligibility_path=eligibility_path,
                pathology_path=pathology_path,
                regimen_path=regimen_path,
            )
            matches = result.get("matches") or []
            if not matches:
                continue
            confirmed_ids.append(patient_id)
            expected_candidate_count += len(matches)
            shadow_runs.append(
                {
                    "patient_id": patient_id,
                    "structured": result.get("oncology_structured") or {},
                }
            )

        history_connection = hs.connect(cfg, database=result_database)
        try:
            history = load_historical_context(
                history_connection,
                confirmed_ids,
            )
        finally:
            history_connection.close()

        source_summary = {
            "schema_version": "1.0.0",
            "source_query_started_at": query_started_at,
            "source_query_finished_at": query_finished_at,
            "source_consistency": SOURCE_CONSISTENCY,
            "candidate_query_version": CANDIDATE_QUERY_VERSION,
            "candidate_query_modes": [
                "national_code_exact",
                "uncoded_full_generic_name",
            ],
            "expected_counts_origin": EXPECTED_COUNTS_ORIGIN,
            "coarse_candidate_patient_count": len(coarse_ids),
            "confirmed_net_positive_patient_count": len(confirmed_ids),
            "candidate_evaluation_count": expected_candidate_count,
        }
        write_private_json(workspace / "shadow_input.json", shadow_runs)
        write_private_json(workspace / "history.json", history)
        write_private_json(
            workspace / "source_summary.json",
            source_summary,
        )

        report = build_direct_report(
            shadow_runs,
            history_by_patient=history,
            salt=salt,
            source_summary=source_summary,
        )
        code_commit, working_tree_clean, working_tree_fingerprint = (
            git_state(
                excluded_paths=(report_path, manifest_path),
            )
        )
        manifest = build_run_manifest(
            source_summary=source_summary,
            report=report,
            source_database=source_database,
            history_database=result_database,
            asset_paths=asset_paths,
            code_commit=code_commit,
            working_tree_clean=working_tree_clean,
            working_tree_fingerprint=working_tree_fingerprint,
        )
        report["run_manifest_ref"] = manifest["run_ref"]
        private_identifiers = list(coarse_ids)
        for patient_history in history.values():
            private_identifiers.append(
                str(patient_history.get("run_id") or "")
            )
            private_identifiers.append(
                str(
                    (
                        patient_history.get("latest_expert_review")
                        or {}
                    ).get("run_id")
                    or ""
                )
            )
        for shadow_run in shadow_runs:
            structured = shadow_run.get("structured") or {}
            for candidate in structured.get("candidate_evaluations") or []:
                private_identifiers.append(
                    str(candidate.get("ownership_key") or "")
                )
        _assert_no_raw_identifiers(
            [report, manifest],
            private_identifiers,
            salt=salt,
        )
        _write_public_json(report_path, report)
        _write_public_json(manifest_path, manifest)
        return report, manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 142 数据中台生成严格 RD04 shadow 报告",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--salt-env",
        default="JAVERT_SHADOW_DEID_SALT",
        help="承载去标识 salt 的环境变量名（salt 不接受命令行明文）",
    )
    parser.add_argument(
        "--temp-root",
        type=Path,
        help="可选临时目录父路径；子目录仍强制 0700 且自动清理",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="显式凭据 env 文件；不会写入报告或 manifest",
    )
    parser.add_argument(
        "--hub-database",
        help="hub 源数据库；默认取 JAVERT_HUB_DATABASE",
    )
    parser.add_argument(
        "--history-database",
        help="历史 RD04/专家复核数据库；默认取 JAVERT_SQL_DATABASE",
    )
    args = parser.parse_args()
    salt = os.environ.get(args.salt_env, "")
    if not salt:
        parser.error(f"必须设置环境变量 {args.salt_env}")
    report, manifest = run_batch(
        report_path=args.report,
        manifest_path=args.manifest,
        salt=salt,
        temp_root=args.temp_root,
        env_file=args.env_file,
        hub_database=args.hub_database,
        history_database=args.history_database,
    )
    print(
        f"{args.report} status={report['status']} "
        f"patients={report['patient_count']} "
        f"candidate_evaluations={report['candidate_evaluation_count']} "
        f"old_new_comparable_patients="
        f"{report['old_new_comparable_patient_count']}"
    )
    print(f"{args.manifest} run_ref={manifest['run_ref']}")
    if report["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
