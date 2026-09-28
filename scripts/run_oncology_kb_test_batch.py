#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从只读 hub 选取小批量 RD04 候选，用本地批准资产审计并写入工作台结果库。

患者标识只存在于进程内存、0700 临时目录和受控结果库；stdout/stderr 仅输出聚合计数。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from javert.config import get_config, reset_config_cache  # noqa: E402
from javert.data import hub_source as hs  # noqa: E402
from javert.data.csv_loader import CsvLoader  # noqa: E402
from javert.store.sqlserver_store import reset_sqlserver_store  # noqa: E402
from javert.tools.drug_audit_lookup import lookup_patient_drugs  # noqa: E402
from scripts.prescan_med_rst import insurance_oncology_drugs  # noqa: E402
from scripts.run_oncology_shadow_batch import (  # noqa: E402
    _chunks,
    discover_coarse_candidate_ids,
    fetch_candidate_frames,
    secure_workspace,
    write_private_csv,
)


def _selected_frames(
    connection,
    patient_ids: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return fetch_candidate_frames(connection, patient_ids)


def _select_confirmed_patients(
    connection,
    *,
    limit: int,
    workspace: Path,
    kb_path: Path,
) -> tuple[list[str], int]:
    drugs = insurance_oncology_drugs(kb_path)
    coarse_ids = discover_coarse_candidate_ids(connection, drugs)
    selected: list[str] = []
    for chunk_no, chunk in enumerate(_chunks(coarse_ids, size=50), start=1):
        fees, notes, diagnoses = _selected_frames(connection, chunk)
        chunk_dir = workspace / f"candidate-{chunk_no:04d}"
        chunk_dir.mkdir(mode=0o700)
        fees_path = chunk_dir / "shi_fee.csv"
        notes_path = chunk_dir / "case_notes.csv"
        zd_path = chunk_dir / "shi_zd.csv"
        write_private_csv(fees_path, fees)
        write_private_csv(notes_path, notes)
        write_private_csv(zd_path, diagnoses)
        loader = CsvLoader(notes_path, fees_path)
        for patient_id in chunk:
            result = lookup_patient_drugs(
                patient_id,
                loader,
                kb_path,
                zd_path,
                rule_type="限适应症",
                source_type="insurance",
            )
            if result.get("matches"):
                selected.append(patient_id)
                if len(selected) == limit:
                    return selected, len(coarse_ids)
    return selected, len(coarse_ids)


def _write_runtime_snapshot(connection, patient_ids: list[str], workspace: Path) -> None:
    yq2org = hs.fetch_hospital_map(connection)
    fees, notes, diagnoses = _selected_frames(connection, patient_ids)
    frames = {
        "shi_fee.csv": fees,
        "case_notes.csv": notes,
        "shi_zd.csv": diagnoses,
        "shi_ss.csv": hs.fetch_ss(connection, patient_ids, yq2org),
        "lab_results.csv": hs.fetch_labs(connection, patient_ids),
        "examinations.csv": hs.fetch_exams(connection, patient_ids),
    }
    for filename, frame in frames.items():
        write_private_csv(workspace / filename, frame)


def _configure_runtime(
    *,
    workspace: Path,
    source_database: str,
    result_database: str,
    batch_tag: str,
) -> None:
    overrides = {
        "JAVERT_DATA_DIR": str(workspace),
        "JAVERT_NOTES_FILE": "case_notes.csv",
        "JAVERT_FEES_FILE": "shi_fee.csv",
        "JAVERT_ZD_FILE": "shi_zd.csv",
        "JAVERT_SS_FILE": "shi_ss.csv",
        "JAVERT_LABS_FILE": "lab_results.csv",
        "JAVERT_EXAMINATIONS_FILE": "examinations.csv",
        "JAVERT_HUB_DATABASE": source_database,
        "JAVERT_SQL_DATABASE": result_database,
        "JAVERT_SQL_ENABLED": "true",
        "JAVERT_BATCH_TAG": batch_tag,
        "JAVERT_ONCOLOGY_ELIGIBILITY_V2": "on",
    }
    os.environ.update(overrides)
    reset_config_cache()
    reset_sqlserver_store()


def _existing_batch_counts(connection, batch_tag: str) -> tuple[int, int]:
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COUNT(*), COUNT(DISTINCT patient_id) "
        "FROM javert_audit_runs WHERE rule_id=N'RD04' AND batch_tag=?",
        (batch_tag,),
    )
    row = cursor.fetchone()
    return int(row[0]), int(row[1])


def run(
    *,
    limit: int,
    source_database: str,
    result_database: str,
    batch_tag: str,
) -> dict[str, int | str]:
    if limit < 1 or limit > 100:
        raise ValueError("limit 必须在 1..100")
    base_cfg = get_config()
    kb_path = base_cfg.resolve("configs/drug_audit_kb.json")

    result_connection = hs.connect(base_cfg, database=result_database)
    try:
        existing_rows, existing_patients = _existing_batch_counts(
            result_connection,
            batch_tag,
        )
    finally:
        result_connection.close()
    if existing_rows or existing_patients:
        raise RuntimeError("目标 batch_tag 已存在，拒绝混入或覆盖既有结果")

    with secure_workspace() as workspace:
        source_connection = hs.connect(base_cfg, database=source_database)
        try:
            selected, coarse_count = _select_confirmed_patients(
                source_connection,
                limit=limit,
                workspace=workspace,
                kb_path=kb_path,
            )
            if len(selected) != limit:
                raise RuntimeError("只读源中符合 RD04 净正收费口径的患者不足")
            _write_runtime_snapshot(source_connection, selected, workspace)
        finally:
            source_connection.close()

        _configure_runtime(
            workspace=workspace,
            source_database=source_database,
            result_database=result_database,
            batch_tag=batch_tag,
        )
        from javert.commands.audit_patient import run_audit_patient

        log_path = workspace / "audit.log"
        completed = 0
        failed = 0
        with log_path.open("w", encoding="utf-8") as log_handle:
            os.chmod(log_path, 0o600)
            with contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(log_handle):
                for patient_id in selected:
                    rc = run_audit_patient(
                        patient_id,
                        rules_arg="RD04",
                        concurrency=1,
                        use_router=False,
                    )
                    if rc == 0:
                        completed += 1
                    else:
                        failed += 1

        runtime_cfg = get_config()
        result_connection = hs.connect(runtime_cfg, database=result_database)
        try:
            result_rows, result_patients = _existing_batch_counts(
                result_connection,
                batch_tag,
            )
        finally:
            result_connection.close()
        if failed or completed != limit or result_patients != limit:
            raise RuntimeError(
                "批跑未完整落库："
                f"completed={completed}, failed={failed}, result_patients={result_patients}"
            )
        return {
            "source_database": source_database,
            "result_database": result_database,
            "batch_tag": batch_tag,
            "coarse_candidate_patients": coarse_count,
            "completed_patients": completed,
            "result_patients": result_patients,
            "result_rows": result_rows,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--source-database", default="sh_yb_platform")
    parser.add_argument("--result-database", default="zadig")
    parser.add_argument("--batch-tag", default="kb_test1")
    args = parser.parse_args()
    summary = run(
        limit=args.limit,
        source_database=args.source_database,
        result_database=args.result_database,
        batch_tag=args.batch_tag,
    )
    print(summary)


if __name__ == "__main__":
    main()
