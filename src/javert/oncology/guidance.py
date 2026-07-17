# -*- coding: utf-8 -*-
"""条件级、前瞻性的病历完善建议.

建议在资格求值之后生成，只读取 UNKNOWN 条件与已证实上下文，不写回证据或状态。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .contracts import (
    CriterionState,
    DocumentationSuggestion,
    EligibilityEvaluation,
)


TRANSPLANT_CRITERION_ID = "pola-transplant-ineligible"
TRANSPLANT_SUGGESTION_TEXT = (
    "患者74岁且已多线治疗；如拟使用该药，建议病程中补充"
    "“不适合造血干细胞移植”及简要原因，避免因文书缺项影响医保报销。"
)


class DocumentationTemplate(BaseModel):
    criterion_id: str
    title: str
    rationale: str
    content_template: str
    priority: Literal["low", "medium", "high"] = "high"
    safety_note: str = (
        "年龄和既往治疗仅作为建议语境；本建议不代表临床医师已作出该评估。"
    )
    required_context: list[str] = Field(default_factory=list)


DEFAULT_TEMPLATES = {
    TRANSPLANT_CRITERION_ID: DocumentationTemplate(
        criterion_id=TRANSPLANT_CRITERION_ID,
        title="补充造血干细胞移植适合性评估",
        rationale=(
            "现有病种、既往多线治疗和疾病进展证据支持继续评估该药，"
            "但病历未见医保限定要求的移植不适合评估。"
        ),
        content_template=(
            "患者{age}岁且已多线治疗；如拟使用该药，建议病程中补充"
            "“不适合造血干细胞移植”及简要原因，避免因文书缺项影响医保报销。"
        ),
        required_context=["age", "multi_line_treatment"],
    )
}


def load_documentation_templates(
    path: Path,
) -> dict[str, DocumentationTemplate]:
    """加载医院可改措辞配置；criterion_id 仍是稳定关联键."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    templates = [
        DocumentationTemplate.model_validate(item)
        for item in raw.get("templates", [])
    ]
    return {item.criterion_id: item for item in templates}


def generate_documentation_suggestions(
    evaluation: EligibilityEvaluation,
    *,
    patient_context: dict[str, Any],
    templates: dict[str, DocumentationTemplate] | None = None,
    enabled: bool = True,
) -> list[DocumentationSuggestion]:
    """只为 UNKNOWN 条件生成建议；不修改 evaluation 或制造 evidence anchor."""
    if not enabled:
        return []
    available = templates or DEFAULT_TEMPLATES
    suggestions: list[DocumentationSuggestion] = []
    for assessment in evaluation.criterion_assessments:
        template = available.get(assessment.criterion_id)
        if template is None or assessment.state != CriterionState.UNKNOWN:
            continue
        if any(not patient_context.get(key) for key in template.required_context):
            continue
        content = template.content_template.format(**patient_context)
        suggestions.append(
            DocumentationSuggestion(
                criterion_id=assessment.criterion_id,
                title=template.title,
                rationale=template.rationale,
                suggested_content=content,
                supporting_context=[
                    f"年龄{patient_context['age']}岁",
                    "既往多线治疗",
                ],
                priority=template.priority,
                safety_note=template.safety_note,
            )
        )
    return suggestions
