# -*- coding: utf-8 -*-
"""note_diagnosis — 文书诊断提取 (子阶段匹配 + 多诊断拆分 + 计数排序).

Source: zadig_agent/src/tools/note_diagnosis.py + skills/note_diagnosis.py
        (snapshot @ 2026-05-08)
改动: notes_df 来源改为 DataLoader.all_notes(); 移除 ICD enrich 部分 (Javert 不带 coder).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Callable

import pandas as pd

from javert.data.loader import DataLoader

logger = logging.getLogger("javert.tools.note_diagnosis")

REQUIRES_PATIENT_ID = True

DESCRIPTION = "从患者文书提取诊断文本 (入院/出院/术前/术后诊断), 按出现频次排序."

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号"},
    },
    "required": ["patient_id"],
}

DIAGNOSIS_STAGES = [
    "入院诊断", "出院诊断", "术前诊断", "术后诊断",
    "临床诊断", "目前诊断", "诊断", "门诊诊断",
    "诊断及诊断依据", "主要诊断", "转出诊断",
    "术中诊断", "死亡诊断", "病理诊断",
]

SPLIT_PATTERN = re.compile(
    r"(?:\d+[\.、）\)]\s*)|(?:[；;]\s*)|(?:\n\s*)"
)


def _split_diagnoses(text: str) -> list[str]:
    parts = SPLIT_PATTERN.split(text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        p = re.sub(r"^\d+[\.、）\)]\s*", "", p)
        if len(p) >= 2:
            out.append(p)
    if not out and text.strip():
        out = [text.strip()]
    return out


def extract_diagnoses(notes_df: pd.DataFrame, patient_id: str) -> dict[str, Any]:
    """从全表 notes_df 提取该患者的诊断字典."""
    if "住院号" not in notes_df.columns:
        return {"diagnoses": [], "raw_texts": [], "total_stages_matched": 0}
    patient_notes = notes_df[notes_df["住院号"].astype(str).str.strip() == patient_id]
    if patient_notes.empty:
        return {"diagnoses": [], "raw_texts": [], "total_stages_matched": 0}

    if "子阶段" not in patient_notes.columns or "内容" not in patient_notes.columns:
        return {"diagnoses": [], "raw_texts": [], "total_stages_matched": 0}

    diag_rows = patient_notes[patient_notes["子阶段"].isin(DIAGNOSIS_STAGES)]
    if diag_rows.empty:
        return {"diagnoses": [], "raw_texts": [], "total_stages_matched": 0}

    counter: Counter = Counter()
    stage_map: dict[str, list[str]] = {}
    raw_texts: list[str] = []

    for _, row in diag_rows.iterrows():
        content = str(row["内容"]).strip()
        stage = str(row["子阶段"]).strip()
        if not content or content == "nan":
            continue
        content = re.sub(r"^[：:]\s*", "", content)
        raw_texts.append(content)
        for part in _split_diagnoses(content):
            counter[part] += 1
            stage_map.setdefault(part, [])
            if stage not in stage_map[part]:
                stage_map[part].append(stage)

    diagnoses = [
        {"text": text, "count": count, "stages": stage_map.get(text, [])}
        for text, count in counter.most_common()
    ]
    return {
        "diagnoses": diagnoses,
        "raw_texts": raw_texts,
        "total_stages_matched": len(diag_rows),
    }


def format_for_agent(result: dict[str, Any]) -> str:
    if not result["diagnoses"]:
        return "未从文书中提取到诊断信息."
    lines = [
        f"从文书提取到 {len(result['diagnoses'])} 条诊断 (共匹配 {result['total_stages_matched']} 个诊断子阶段):",
        "",
    ]
    for i, diag in enumerate(result["diagnoses"], 1):
        lines.append(
            f"{i}. 「{diag['text']}」 (出现{diag['count']}次, 来源: {'/'.join(diag['stages'])})"
        )
    return "\n".join(lines)


def create_executor(loader: DataLoader) -> Callable[..., str]:
    """绑定 DataLoader, 返回 note_diagnosis(patient_id) 函数."""

    def execute(patient_id: str, **_kwargs) -> str:
        notes_df = loader.all_notes()
        result = extract_diagnoses(notes_df, patient_id)
        return format_for_agent(result)

    return execute
