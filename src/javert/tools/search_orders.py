# -*- coding: utf-8 -*-
"""search_orders — 结构化医嘱优先，OCR 医嘱类文书保守兜底。"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from javert.data.loader import DataLoader

REQUIRES_PATIENT_ID = True

DESCRIPTION = (
    "检索患者医嘱。优先返回结构化医嘱的类型、项目、数量和执行时间；"
    "结构化医嘱缺失时，仅从医嘱/嘱单类病历全文返回关键词上下文，并明确标记为非结构化候选。"
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号"},
        "keyword": {"type": "string", "description": "可选，医嘱项目关键词"},
        "order_type": {"type": "string", "description": "可选，临时/ST/长期等医嘱类型"},
    },
    "required": ["patient_id"],
}

_MAX_ROWS = 20
_MAX_SNIPPET = 420


def _column(df: pd.DataFrame, *candidates: str) -> str | None:
    return next((column for column in candidates if column in df.columns), None)


def _excerpt(content: str, keyword: str | None) -> str:
    text = " ".join(content.split())
    if not keyword:
        return text[:_MAX_SNIPPET] + ("..." if len(text) > _MAX_SNIPPET else "")
    position = text.find(keyword)
    if position < 0:
        return text[:_MAX_SNIPPET] + ("..." if len(text) > _MAX_SNIPPET else "")
    start = max(0, position - 100)
    end = min(len(text), position + len(keyword) + 260)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")


def create_executor(loader: DataLoader) -> Callable[..., str]:
    """绑定患者文书 loader。"""

    def execute(
        patient_id: str,
        keyword: str | None = None,
        order_type: str | None = None,
        **_kwargs,
    ) -> str:
        notes = loader.get_notes(patient_id)
        if notes.empty:
            return "该患者无可检索医嘱或病历文书"

        content_col = _column(notes, "内容", "content", "text")
        stage_col = _column(notes, "阶段", "stage", "doc_name")
        sub_stage_col = _column(notes, "子阶段", "sub_stage", "section")
        source_col = _column(notes, "来源文件", "source_file")
        if not content_col:
            return "病历数据缺少内容列，无法检索医嘱"

        structured = notes.iloc[0:0]
        if source_col:
            structured = notes[notes[source_col].astype(str) == "data_hub_advice"]
        if not structured.empty:
            selected = structured
            evidence_type = "结构化医嘱"
        else:
            labels = pd.Series("", index=notes.index, dtype="string")
            if stage_col:
                labels = labels + notes[stage_col].fillna("").astype(str)
            if sub_stage_col:
                labels = labels + " " + notes[sub_stage_col].fillna("").astype(str)
            selected = notes[labels.str.contains("医嘱|嘱单|临时|长期", regex=True, na=False)]
            evidence_type = "非结构化医嘱候选（病历全文，不能证明实际执行）"

        if keyword:
            selected = selected[selected[content_col].astype(str).str.contains(keyword, regex=False, na=False)]
        if order_type:
            type_text = selected[content_col].fillna("").astype(str)
            if sub_stage_col:
                type_text = type_text + " " + selected[sub_stage_col].fillna("").astype(str)
            selected = selected[type_text.str.contains(order_type, case=False, regex=False, na=False)]
        if selected.empty:
            qualifier = f"关键词“{keyword}”" if keyword else "当前条件"
            return f"{evidence_type}中未找到{qualifier}"

        lines = [f"{evidence_type}（匹配 {len(selected)} 行，最多显示 {_MAX_ROWS} 行）:"]
        for index, (_, row) in enumerate(selected.head(_MAX_ROWS).iterrows(), 1):
            stage = str(row.get(sub_stage_col) or row.get(stage_col) or "医嘱")
            lines.append(f"[{index}] {stage} ⟨医嘱行={index}⟩")
            lines.append(f"    {_excerpt(str(row[content_col]), keyword)}")
        if evidence_type.startswith("非结构化"):
            lines.append("提示：文本命中次数不等于独立医嘱次数，须结合医嘱时间、执行记录和费用数量复核。")
        return "\n".join(lines)

    return execute
