"""知识 authoring 稳定 ID 与 canonical checksum。"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """返回跨重复构建稳定的 UTF-8 JSON。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def checksum(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _normalize_part(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        if isinstance(value, set):
            value = sorted(value)
        return canonical_json_bytes(value).decode("utf-8")
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def stable_id(prefix: str, *parts: Any) -> str:
    """以语义字段生成稳定 ID；不使用时间、行序或数据库自增键。"""
    normalized = [_normalize_part(part) for part in parts]
    digest = hashlib.sha256("\x1f".join(normalized).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def source_document_id(source_type: str, title: str, version: str) -> str:
    return stable_id("srcdoc", source_type, title, version)


def source_fragment_id(document_id: str, anchor: str, text: str) -> str:
    return stable_id("srcfrag", document_id, anchor, checksum(text))


def logical_rule_id(source_type: str, drug_concept_id: str, source_fragment_id_: str) -> str:
    return stable_id("rule", source_type, drug_concept_id, source_fragment_id_)


def revision_id(logical_id: str, canonical_payload: Any) -> str:
    return stable_id("rev", logical_id, checksum(canonical_payload))


def branch_id(rule_revision_id: str, source_span: str, ordinal: int) -> str:
    return stable_id("branch", rule_revision_id, source_span, ordinal)


def node_id(branch_revision_id: str, semantic_path: str, payload: Any) -> str:
    return stable_id("node", branch_revision_id, semantic_path, checksum(payload))


def release_id(revision_ids: list[str], source_snapshot_checksum: str) -> str:
    return stable_id("release", sorted(revision_ids), source_snapshot_checksum)


def import_batch_id(workbook_sha256: str, template_schema_version: str) -> str:
    return stable_id("batch", workbook_sha256, template_schema_version)
