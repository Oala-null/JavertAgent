# -*- coding: utf-8 -*-
"""Promise 治理资产与运行时 trace 的严格 schema。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PromiseStatus = Literal["draft", "active", "superseded"]
DriftStatus = Literal["observed", "confirmed", "promoted", "rejected"]
PromisePhase = Literal["decision_pre_llm", "public_projection", "transport"]
PromiseFinality = Literal["LOCKED"]
PromiseGuarantee = Literal["VIOLATION", "CLEAN", "INCONCLUSIVE"]
PromiseCaseType = Literal["positive", "near_negative"]
ExpectedOutcome = Literal["MATCH", "NOT_APPLICABLE"]

_FORBIDDEN_FACT_KEYS = {
    "patient_id",
    "patient_name",
    "patient_no",
    "syxh",
    "mrn",
    "medical_record_no",
    "住院号",
    "患者号",
    "姓名",
    "run_id",
    "ownership_id",
    "owner_id",
    "raw_note",
    "note_text",
    "medical_record_text",
    "sql",
    "connection_string",
    "password",
    "credential",
}
_RUN_ID_VALUE = re.compile(r"^aud_[A-Za-z0-9_-]{12}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_safe_value(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if key in _FORBIDDEN_FACT_KEYS:
                raise ValueError(f"facts 含禁止字段类型: {'.'.join((*path, key))}")
            _check_safe_value(child, (*path, key))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _check_safe_value(child, (*path, str(index)))
        return
    if isinstance(value, str) and _RUN_ID_VALUE.fullmatch(value.strip()):
        raise ValueError(f"facts 含未盐化 run 标识: {'.'.join(path)}")


def validate_safe_facts(value: dict[str, Any]) -> dict[str, Any]:
    _check_safe_value(value)
    return value


class ConfirmationRef(StrictModel):
    source_type: Literal[
        "expert_consensus",
        "rule_contract",
        "test_reproduction",
        "source_workbook",
    ]
    reference: str = Field(min_length=3, max_length=300)


class DriftCase(StrictModel):
    asset_type: Literal["drift_case"] = "drift_case"
    case_id: str = Field(pattern=r"^DRIFT-[A-Z0-9][A-Z0-9-]*$")
    status: DriftStatus
    title: str = Field(min_length=3, max_length=160)
    observed_behavior: str = Field(min_length=3, max_length=500)
    expected_behavior: str = Field(min_length=3, max_length=500)
    facts: dict[str, Any] = Field(default_factory=dict)
    confirmation: ConfirmationRef

    _safe_facts = field_validator("facts")(validate_safe_facts)


class PromiseScope(StrictModel):
    rule_ids: list[str] = Field(min_length=1)
    semantic_profile: str = Field(min_length=3, max_length=120)

    @field_validator("rule_ids")
    @classmethod
    def _unique_rule_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("scope.rule_ids 不得重复")
        for rule_id in value:
            if not re.fullmatch(r"R\d{3}|RD\d{2,3}", rule_id):
                raise ValueError(f"非法 rule scope: {rule_id}")
        return value


class PromiseTrace(StrictModel):
    promise_id: str = Field(pattern=r"^PR-[A-Z][A-Z0-9-]*$")
    version: int = Field(ge=1)
    kind: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    finality: PromiseFinality
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    facts: dict[str, Any] = Field(default_factory=dict)
    historical_conflict: bool = False

    _safe_facts = field_validator("facts")(validate_safe_facts)


class PromiseMatch(StrictModel):
    outcome: Literal["MATCH"] = "MATCH"
    guarantee: PromiseGuarantee
    trace: PromiseTrace


class PromiseExpected(StrictModel):
    outcome: ExpectedOutcome
    verdict: PromiseGuarantee | None = None
    finality: PromiseFinality | None = None
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    facts: dict[str, Any] | None = None

    _safe_facts = field_validator("facts")(lambda value: validate_safe_facts(value) if value is not None else value)

    @model_validator(mode="after")
    def _match_contract(self) -> "PromiseExpected":
        required = (self.verdict, self.finality, self.reason_code, self.facts)
        if self.outcome == "MATCH" and any(value is None for value in required):
            raise ValueError("MATCH 期望必须声明 verdict/finality/reason_code")
        if self.outcome == "NOT_APPLICABLE" and any(value is not None for value in required):
            raise ValueError("NOT_APPLICABLE 期望不得声明终局保证")
        return self


class PromiseCase(StrictModel):
    asset_type: Literal["promise_case"] = "promise_case"
    case_id: str = Field(pattern=r"^CASE-[A-Z0-9][A-Z0-9-]*$")
    promise_id: str = Field(pattern=r"^PR-[A-Z][A-Z0-9-]*$")
    promise_version: int = Field(ge=1)
    case_type: PromiseCaseType
    rule_id: str = Field(pattern=r"^(R\d{3}|RD\d{2,3})$")
    source_case_id: str | None = Field(
        default=None, pattern=r"^DRIFT-[A-Z0-9][A-Z0-9-]*$"
    )
    facts: dict[str, Any]
    expected: PromiseExpected
    historical_boundary: bool = False

    _safe_facts = field_validator("facts")(validate_safe_facts)

    @model_validator(mode="after")
    def _case_polarity(self) -> "PromiseCase":
        if self.case_type == "positive" and self.expected.outcome != "MATCH":
            raise ValueError("positive 案例必须期望 MATCH")
        if self.case_type == "near_negative" and self.expected.outcome != "NOT_APPLICABLE":
            raise ValueError("near_negative 案例必须期望 NOT_APPLICABLE")
        return self


def definition_content_digest(value: Mapping[str, Any] | BaseModel) -> str:
    if isinstance(value, BaseModel):
        raw = value.model_dump(mode="json")
    else:
        raw = dict(value)
    raw.pop("content_sha256", None)
    # 生命周期状态允许 active → superseded；其余语义内容不可就地改。
    raw.pop("status", None)
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PromiseDefinition(StrictModel):
    asset_type: Literal["promise_definition"] = "promise_definition"
    promise_id: str = Field(pattern=r"^PR-[A-Z][A-Z0-9-]*$")
    version: int = Field(ge=1)
    status: PromiseStatus
    phase: PromisePhase
    kind: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    scope: PromiseScope
    params: dict[str, Any]
    source_cases: list[str] = Field(min_length=1)
    cases: list[str] = Field(min_length=2)
    guarantee: PromiseGuarantee
    finality: PromiseFinality
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    confirmation: ConfirmationRef
    version_note: str = Field(min_length=3, max_length=500)
    supersedes: int | None = Field(default=None, ge=1)
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("source_cases", "cases")
    @classmethod
    def _unique_refs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("资产引用不得重复")
        return value

    @model_validator(mode="after")
    def _version_and_digest(self) -> "PromiseDefinition":
        if self.version == 1 and self.supersedes is not None:
            raise ValueError("v1 不得声明 supersedes")
        if self.supersedes is not None and self.supersedes >= self.version:
            raise ValueError("supersedes 必须指向更早版本")
        expected = definition_content_digest(self)
        if self.content_sha256 != expected:
            raise ValueError("Promise active 内容摘要不匹配，必须新建版本而非就地修改")
        return self
