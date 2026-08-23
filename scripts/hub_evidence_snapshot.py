#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 allowlisted SELECT-only Hub profile 生成 immutable Evidence Snapshot。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pyodbc

from javert.config import get_config
from javert.data.hub_source import build_conn_str
from javert.evidence.hub_snapshot import (
    DEFAULT_PROFILE_ID, SOURCE_PROFILES, SnapshotSummary, _assert_new_external_dir,
    build_snapshot_bundle, inspect_connection, read_all_raw_rows,
)
from javert.evidence.serialization import canonical_json_bytes
from scripts.prescan_med_rst import insurance_oncology_drugs
from scripts.run_oncology_shadow_batch import (
    CANDIDATE_QUERY_VERSION, discover_coarse_candidate_ids, fetch_candidate_frames,
    secure_workspace, source_server_time,
)

def _write_public_json(path: Path, value) -> None:
    if path.exists():
        raise FileExistsError("HUB_SUMMARY_OUTPUT_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _code_version() -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    digest = hashlib.sha256()
    for relative in (
        "src/javert/evidence/hub_snapshot.py",
        "src/javert/data/hub_source.py",
        "scripts/hub_evidence_snapshot.py",
        "scripts/run_oncology_shadow_batch.py",
        "scripts/prescan_med_rst.py",
    ):
        path = ROOT / relative
        digest.update(relative.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return f"{commit}:sha256:{digest.hexdigest()}"


def _connect(database: str):
    cfg = get_config()
    return pyodbc.connect(
        build_conn_str(cfg, database=database, login_timeout=15)
        + ";ApplicationIntent=ReadOnly",
        timeout=30, autocommit=True,
    )


def _snapshot(connection, *, profile_id: str, output_dir: Path, limit: int):
    profile = SOURCE_PROFILES[profile_id]
    preflight = inspect_connection(connection, profile)
    if not preflight.ok:
        codes = ",".join(sorted({item.code for item in preflight.issues}))
        raise RuntimeError(f"HUB_PREFLIGHT_FAILED:{codes}")
    configs = get_config().resolve("configs")
    drugs = insurance_oncology_drugs(configs / "drug_audit_kb.json")
    query_started_at = source_server_time(connection)
    discovered = discover_coarse_candidate_ids(connection, drugs)
    if len(discovered) < limit:
        raise RuntimeError("HUB_SMOKE_CANDIDATES_INSUFFICIENT")
    cohort = sorted(discovered)[:limit]
    raw_rows = read_all_raw_rows(connection, cohort)
    fees, notes, diagnoses = fetch_candidate_frames(connection, cohort)
    query_finished_at = source_server_time(connection)
    return build_snapshot_bundle(
        output_dir=output_dir, preflight=preflight, raw_rows=raw_rows,
        canonical_frames={
            "shi_fee.csv": fees, "case_notes.csv": notes, "shi_zd.csv": diagnoses,
        },
        cohort=cohort, code_version=_code_version(),
        cohort_query_version=CANDIDATE_QUERY_VERSION,
        query_started_at=query_started_at, query_finished_at=query_finished_at,
    )


def run(*, profile_id: str, limit: int, output_dir: Path | None, smoke: bool) -> SnapshotSummary:
    if profile_id not in SOURCE_PROFILES:
        raise ValueError("HUB_SOURCE_PROFILE_UNKNOWN")
    if limit < 1 or limit > 100:
        raise ValueError("HUB_SNAPSHOT_LIMIT_INVALID")
    profile = SOURCE_PROFILES[profile_id]
    if smoke:
        if limit != 5:
            raise ValueError("HUB_SMOKE_LIMIT_MUST_BE_FIVE")
        connection = _connect(profile.database)
        try:
            with secure_workspace() as workspace:
                _manifest, summary = _snapshot(
                    connection, profile_id=profile_id,
                    output_dir=workspace / "snapshot", limit=limit,
                )
                return summary
        finally:
            connection.close()
    if output_dir is None:
        raise ValueError("HUB_SNAPSHOT_OUTPUT_REQUIRED")
    _assert_new_external_dir(output_dir)
    connection = _connect(profile.database)
    try:
        _manifest, summary = _snapshot(
            connection, profile_id=profile_id, output_dir=output_dir, limit=limit,
        )
    finally:
        connection.close()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="生成只读 Hub Evidence Snapshot")
    parser.add_argument("--profile", choices=sorted(SOURCE_PROFILES), default=DEFAULT_PROFILE_ID)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--summary", type=Path, help="可选无 PHI summary 输出（必须是新文件）")
    parser.add_argument("--smoke", action="store_true", help="五候选临时快照，退出前清理 PHI")
    args = parser.parse_args()
    try:
        summary = run(
            profile_id=args.profile, limit=args.limit,
            output_dir=args.output_dir, smoke=args.smoke,
        )
    except Exception as exc:
        blocked = {
            "schema_version": "0.1.0", "status": "blocked",
            "source_profile": args.profile,
            "error_codes": [str(exc).split(":", 1)[0]],
        }
        print(json.dumps(blocked, ensure_ascii=False, sort_keys=True))
        raise SystemExit(2)
    if args.summary:
        _write_public_json(args.summary, summary.model_dump(mode="json"))
    print(canonical_json_bytes(summary).decode("utf-8").rstrip())


if __name__ == "__main__":
    main()
