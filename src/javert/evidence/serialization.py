# -*- coding: utf-8 -*-
"""Evidence/Evaluation 工件的 canonical serialization。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


def _record_identity(value: Mapping[str, Any]) -> str | None:
    for key in (
        "ontology_ref_id", "source_artifact_id", "evidence_id", "entity_id",
        "fact_id", "activity_id", "inference_id", "assertion_id", "conflict_id",
        "review_action_id", "plan_id", "case_id", "observation_id", "review_id",
        "gate_id", "metric_id",
    ):
        if key in value:
            return str(value[key])
    return None


def canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): canonicalize(child) for key, child in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        items = [canonicalize(child) for child in value]
        if items and all(isinstance(child, Mapping) and _record_identity(child) for child in items):
            return sorted(items, key=lambda child: _record_identity(child) or "")
        return items
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            canonicalize(value), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def sha256_digest(value: bytes | Any) -> str:
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def checksum_value(value: Any) -> str:
    return sha256_digest(canonical_json_bytes(value))


def row_fingerprint(row: Mapping[str, Any]) -> str:
    """基于声明的 canonical row serialization 生成稳定指纹。"""
    return checksum_value(dict(row))
