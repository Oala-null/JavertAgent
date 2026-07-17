# -*- coding: utf-8 -*-
"""肿瘤知识资产共用的版本、来源与 checksum 契约."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ReviewStatus(StrEnum):
    APPROVED = "approved"
    NEEDS_REVIEW = "needs_review"
    RETIRED = "retired"


class SourceReference(BaseModel):
    """不可变知识来源引用."""

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    version: str = ""
    publication_date: date | None = None
    effective_date: date | None = None
    retrieval_date: date
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _require_revision(self) -> "SourceReference":
        if not (self.version or self.publication_date or self.effective_date):
            raise ValueError("来源必须提供 version、publication_date 或 effective_date")
        return self


class KnowledgeMetadata(BaseModel):
    """三个知识资产共享的发布元数据."""

    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    content_version: str = Field(min_length=1)
    effective_from: date
    effective_to: date | None = None
    source_refs: list[SourceReference] = Field(min_length=1)
    checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    review_status: ReviewStatus

    @model_validator(mode="after")
    def _validate_effective_range(self) -> "KnowledgeMetadata":
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to 不能早于 effective_from")
        return self


class KnowledgeEntryMetadata(BaseModel):
    """单个可激活知识条目的审核和生效边界."""

    content_version: str = Field(min_length=1)
    effective_from: date
    effective_to: date | None = None
    source_refs: list[SourceReference] = Field(min_length=1)
    review_status: ReviewStatus

    @model_validator(mode="after")
    def _validate_effective_range(self) -> "KnowledgeEntryMetadata":
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to 不能早于 effective_from")
        return self


class KnowledgeAssetEnvelope(BaseModel):
    """共享 JSON Schema 的最小 envelope；各工作线再收紧 entries 类型."""

    asset_type: Literal["eligibility", "pathology", "regimen"]
    metadata: KnowledgeMetadata
    entries: list[dict[str, Any]]


class EligibilityAssetEnvelope(KnowledgeAssetEnvelope):
    asset_type: Literal["eligibility"] = "eligibility"


class PathologyAssetEnvelope(KnowledgeAssetEnvelope):
    asset_type: Literal["pathology"] = "pathology"


class RegimenAssetEnvelope(KnowledgeAssetEnvelope):
    asset_type: Literal["regimen"] = "regimen"


def canonical_json_bytes(value: Any) -> bytes:
    """稳定 JSON 字节，用于构建产物与重复运行比较."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def asset_payload_checksum(value: dict[str, Any]) -> str:
    """计算资产内容 checksum；metadata.checksum 自身不参与摘要."""
    payload = json.loads(json.dumps(value, ensure_ascii=False))
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("checksum", None)
    return sha256_digest(canonical_json_bytes(payload))
