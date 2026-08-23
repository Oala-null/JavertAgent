# -*- coding: utf-8 -*-
"""合成 Evidence/Evaluation 工件的最小隐私门禁。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .models import ValidationIssue

_FORBIDDEN_KEYS = {
    "patient_id", "patient_name", "patient_no", "syxh", "mrn", "medical_record_no",
    "住院号", "患者号", "姓名", "password", "credential", "connection_string",
    "raw_note", "note_text", "medical_record_text", "salt", "ownership_id", "run_id",
}


def privacy_issues(value: Any, path: str = "$") -> tuple[ValidationIssue, ...]:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    issues: list[ValidationIssue] = []

    def visit(item: Any, current: str) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key)
                child_path = f"{current}.{key}"
                if key.strip().lower() in _FORBIDDEN_KEYS:
                    issues.append(ValidationIssue(code="PRIVACY_FORBIDDEN_FIELD", path=child_path))
                visit(child, child_path)
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, f"{current}[{index}]")

    visit(value, path)
    return tuple(sorted(issues, key=lambda issue: (issue.code, issue.path)))


def synthetic_fixture_issues(value: Any) -> tuple[ValidationIssue, ...]:
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    issues = list(privacy_issues(raw))
    if not isinstance(raw, Mapping) or raw.get("deidentified") is not True:
        issues.append(ValidationIssue(code="FIXTURE_DEIDENTIFIED_REQUIRED", path="$.deidentified"))
    return tuple(sorted(issues, key=lambda issue: (issue.code, issue.path)))
