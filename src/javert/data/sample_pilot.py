# -*- coding: utf-8 -*-
"""患者采样脚本 — 从全量数据里挑 50 个甲状腺癌患者写到 pilot_patients.txt."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("javert.data.sample_pilot")

THYROID_KEYWORDS = ["甲状腺", "thyroid"]
SURGERY_KW = ["手术", "切除", "切开", "活检", "甲状腺癌根治"]
CHEMO_KW = ["化疗", "替尼", "靶向", "PD-1", "免疫治疗"]
LAB_KW = ["TSH", "甲功", "FT4", "FT3", "病理", "降钙素"]
DEFAULT_TARGET = 50


def _patient_id_from_bah(bah: str) -> str:
    """费用表 bah 形如 'H31010600042-J13365 ' → 'J13365'."""
    if not isinstance(bah, str):
        return ""
    parts = bah.strip().split("-")
    if len(parts) >= 2:
        return parts[-1].strip()
    return bah.strip()


def sample_pilot_patients(
    notes_df: pd.DataFrame,
    fees_df: pd.DataFrame,
    target: int = DEFAULT_TARGET,
) -> list[str]:
    """按规则挑选 patient_id 列表.

    规则:
        1. 文书命中 THYROID_KEYWORDS (≥1 条)
        2. 同时有费用记录 (≥10 条)
        3. 优先包含手术/化疗/检验三类信号的患者
    """
    if "住院号" not in notes_df.columns:
        raise ValueError(f"notes_df 缺少 住院号 列: {notes_df.columns.tolist()}")
    if "bah" not in fees_df.columns:
        raise ValueError(f"fees_df 缺少 bah 列: {fees_df.columns.tolist()}")

    # 1. 文书命中甲状腺
    content_col = next((c for c in ["内容", "content"] if c in notes_df.columns), None)
    if content_col is None:
        raise ValueError("notes_df 缺少 内容/content 列")

    pattern = "|".join(THYROID_KEYWORDS)
    thyroid_rows = notes_df[notes_df[content_col].astype(str).str.contains(pattern, na=False)]
    thyroid_pids = set(thyroid_rows["住院号"].astype(str).str.strip().unique())
    logger.info("文书命中甲状腺关键词患者数: %d", len(thyroid_pids))

    # 2. 费用 ≥10 条
    fees_df = fees_df.copy()
    fees_df["_pid"] = fees_df["bah"].astype(str).map(_patient_id_from_bah)
    pid_fee_count = fees_df.groupby("_pid").size()
    eligible = {pid for pid, n in pid_fee_count.items() if n >= 10}
    candidates = thyroid_pids & eligible
    logger.info("交集 (甲状腺 + 费用≥10): %d", len(candidates))

    # 3. 给候选患者按信号覆盖打分 (手术 + 化疗 + 检验, 各 1 分)
    name_col = next(
        (c for c in ["medins_list_name", "prodname", "项目名称"] if c in fees_df.columns),
        None,
    )
    note_pid_col = "住院号"
    scored: list[tuple[str, int, int]] = []  # (pid, signal_count, fee_count)

    for pid in candidates:
        pid_fees = fees_df[fees_df["_pid"] == pid]
        names = pid_fees[name_col].astype(str).str.cat(sep=" ") if name_col else ""
        pid_notes = thyroid_rows[thyroid_rows[note_pid_col].astype(str).str.strip() == pid]
        notes_text = pid_notes[content_col].astype(str).str.cat(sep=" ") if not pid_notes.empty else ""
        joined = names + " " + notes_text
        signals = 0
        if any(kw in joined for kw in SURGERY_KW):
            signals += 1
        if any(kw in joined for kw in CHEMO_KW):
            signals += 1
        if any(kw in joined for kw in LAB_KW):
            signals += 1
        scored.append((pid, signals, int(pid_fee_count[pid])))

    # 排序: 信号数降序 → 费用条数降序 → patient_id 升序 (确定性)
    scored.sort(key=lambda x: (-x[1], -x[2], x[0]))
    selected = [pid for pid, _, _ in scored[:target]]
    logger.info("最终入选 %d 个患者 (目标 %d)", len(selected), target)
    return selected


def write_pilot_roster(patient_ids: list[str], roster_path: Path, comment: str = "") -> None:
    """写 pilot_patients.txt."""
    roster_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if comment:
        for ln in comment.splitlines():
            lines.append(f"# {ln}")
        lines.append("")
    lines.extend(patient_ids)
    roster_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("pilot 名单写入 %s (%d 患者)", roster_path, len(patient_ids))
