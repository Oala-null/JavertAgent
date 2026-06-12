# -*- coding: utf-8 -*-
"""快照拷贝 — 从 zadig_agent/data/ 物理拷贝两个 csv 到 Javert/data/."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("javert.data.snapshot")


def _count_rows(csv_path: Path) -> int:
    """快速行数统计 (含 header). 返回数据行数 = 总行数 - 1."""
    with open(csv_path, "rb") as f:
        # 简单计数 \n; 末尾若无换行结果差 1, 但仅做 sanity check 可接受
        total = sum(1 for _ in f)
    return max(total - 1, 0)


def take_snapshot(
    upstream_data_dir: Path,
    target_data_dir: Path,
    notes_filename: str = "case_notes.csv",
    fees_filename: str = "shi_fee.csv",
    refresh: bool = False,
) -> dict:
    """物理拷贝 case_notes.csv + shi_fee.csv, 写 _snapshot.json.

    Returns:
        snapshot dict (source / copied_at / file_sizes / row_counts).

    Raises:
        FileExistsError: 目标已存在且 refresh=False.
        FileNotFoundError: 上游 csv 不存在.
    """
    target_data_dir.mkdir(parents=True, exist_ok=True)

    src_notes = upstream_data_dir / "case_notes" / notes_filename
    src_fees = upstream_data_dir / "patients" / fees_filename
    if not src_notes.exists():
        raise FileNotFoundError(f"上游文书不存在: {src_notes}")
    if not src_fees.exists():
        raise FileNotFoundError(f"上游费用不存在: {src_fees}")

    dst_notes = target_data_dir / notes_filename
    dst_fees = target_data_dir / fees_filename

    for dst in (dst_notes, dst_fees):
        if dst.exists() and not refresh:
            raise FileExistsError(
                f"snapshot exists: {dst.name} 已存在; 用 --refresh-data 覆盖"
            )

    logger.info("拷贝 %s -> %s", src_notes, dst_notes)
    shutil.copy2(src_notes, dst_notes)
    logger.info("拷贝 %s -> %s", src_fees, dst_fees)
    shutil.copy2(src_fees, dst_fees)

    notes_rows = _count_rows(dst_notes)
    fees_rows = _count_rows(dst_fees)
    src_notes_rows = _count_rows(src_notes)
    src_fees_rows = _count_rows(src_fees)
    if notes_rows != src_notes_rows:
        raise RuntimeError(f"文书行数不一致: src={src_notes_rows} dst={notes_rows}")
    if fees_rows != src_fees_rows:
        raise RuntimeError(f"费用行数不一致: src={src_fees_rows} dst={fees_rows}")

    snapshot = {
        "source": {
            "notes": str(src_notes),
            "fees": str(src_fees),
        },
        "copied_at": datetime.now(timezone.utc).isoformat(),
        "file_sizes": {
            notes_filename: dst_notes.stat().st_size,
            fees_filename: dst_fees.stat().st_size,
        },
        "row_counts": {
            notes_filename: notes_rows,
            fees_filename: fees_rows,
        },
    }
    snapshot_path = target_data_dir / "_snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("snapshot 写入 %s (notes=%d, fees=%d)", snapshot_path, notes_rows, fees_rows)
    return snapshot


def load_pilot_roster(roster_path: Path) -> list[str]:
    """读取 pilot_patients.txt: 每行一个 patient_id, # 注释, 空行忽略."""
    if not roster_path.exists():
        raise FileNotFoundError(f"pilot 名单不存在: {roster_path}")
    ids: list[str] = []
    seen: set[str] = set()
    for line in roster_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        ids.append(line)
    return ids
