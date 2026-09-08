# -*- coding: utf-8 -*-
"""慢病条件资产的 checksum、加载和自动使用门禁。"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .contracts import (
    ChronicDiseaseCriteriaAsset,
    DiseaseRevision,
    NodeCompilationStatus,
    PolicyRole,
    PolicySet,
    ReviewStatus,
    RevisionExecutionStatus,
    RevisionLifecycle,
    SourceManifest,
)


def canonical_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def fragment_payload_checksum(fragment: dict[str, Any]) -> str:
    payload = json.loads(json.dumps(fragment, ensure_ascii=False))
    payload.pop("fragment_checksum", None)
    return sha256_digest(canonical_json_bytes(payload))


def source_manifest_payload_checksum(manifest: dict[str, Any]) -> str:
    payload = json.loads(json.dumps(manifest, ensure_ascii=False))
    payload.pop("manifest_checksum", None)
    return sha256_digest(canonical_json_bytes(payload))


def interpretation_payload_checksum(interpretation: dict[str, Any]) -> str:
    payload = dict(interpretation)
    payload.pop("interpretation_checksum", None)
    return sha256_digest(canonical_json_bytes(payload))


def asset_payload_checksum(asset: dict[str, Any]) -> str:
    payload = json.loads(json.dumps(asset, ensure_ascii=False))
    payload.pop("asset_checksum", None)
    return sha256_digest(canonical_json_bytes(payload))


def load_source_manifest(path: Path, *, root: Path | None = None) -> SourceManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    manifest = SourceManifest.model_validate(raw)
    expected = source_manifest_payload_checksum(raw)
    if manifest.manifest_checksum != expected:
        raise ValueError(
            "chronic source manifest checksum 不一致: "
            f"declared={manifest.manifest_checksum}, expected={expected}"
        )
    base = root or path.resolve().parents[1]
    for document in manifest.documents:
        document_path = base / document.file_path
        if not document_path.is_file():
            raise ValueError(f"慢病来源 PDF 不存在: {document.file_path}")
        actual = sha256_digest(document_path.read_bytes())
        if actual != document.pdf_checksum:
            raise ValueError(f"慢病来源 PDF checksum 不一致: {document.source_document_id}")
    for fragment in manifest.fragments:
        raw_fragment = next(
            item
            for item in raw["fragments"]
            if item["source_fragment_id"] == fragment.source_fragment_id
        )
        if fragment.fragment_checksum != fragment_payload_checksum(raw_fragment):
            raise ValueError(f"source fragment checksum 不一致: {fragment.source_fragment_id}")
    for item in raw.get("expert_interpretations", []):
        if item["interpretation_checksum"] != interpretation_payload_checksum(item):
            raise ValueError("expert interpretation checksum 不一致")
    return manifest


def load_criteria_asset(
    path: Path,
    *,
    source_manifest: SourceManifest | None = None,
) -> ChronicDiseaseCriteriaAsset:
    raw = json.loads(path.read_text(encoding="utf-8"))
    asset = ChronicDiseaseCriteriaAsset.model_validate(raw)
    expected = asset_payload_checksum(raw)
    if asset.asset_checksum != expected:
        raise ValueError(
            "chronic criteria asset checksum 不一致: "
            f"declared={asset.asset_checksum}, expected={expected}"
        )
    if source_manifest is not None:
        if asset.source_manifest_checksum != source_manifest.manifest_checksum:
            raise ValueError("criteria asset 与 source manifest checksum 不一致")
        validate_source_isolation(asset, source_manifest)
    return asset


def validate_source_isolation(
    asset: ChronicDiseaseCriteriaAsset,
    manifest: SourceManifest,
) -> None:
    documents = {item.source_document_id: item for item in manifest.documents}
    fragments = {item.source_fragment_id: item for item in manifest.fragments}
    interpretations = {item.interpretation_id: item for item in manifest.expert_interpretations}
    for item in manifest.expert_interpretations:
        if item.interpretation_checksum != interpretation_payload_checksum(item.model_dump(mode="json")):
            raise ValueError("expert interpretation checksum 不一致")
    for policy in asset.policy_sets:
        for source_id in policy.source_document_ids:
            document = documents.get(source_id)
            if document is None or document.policy_version != policy.policy_version:
                raise ValueError(f"policy {policy.policy_id} 引用跨版本 source document")
        for revision in policy.disease_revisions:
            nodes = {item.node_id: item for item in revision.nodes}
            for ref in revision.expert_interpretation_ids:
                interpretation = interpretations.get(ref)
                if interpretation is None:
                    raise ValueError("revision 引用未知书面解释")
                if (interpretation.rule_id, interpretation.policy_version) != (
                    revision.rule_id, revision.policy_version
                ):
                    raise ValueError("revision 引用跨病种或跨版本书面解释")
                if set(interpretation.affected_node_ids) - set(nodes):
                    raise ValueError("书面解释引用未知节点")
                for node_id in interpretation.affected_node_ids:
                    if ref not in nodes[node_id].expert_interpretation_ids:
                        raise ValueError("书面解释必须关联受影响节点")
            for node in revision.nodes:
                for ref in node.expert_interpretation_ids:
                    if node.node_id not in interpretations[ref].affected_node_ids:
                        raise ValueError("节点超出书面解释范围")
            refs = set(revision.source_fragment_ids)
            refs.update(
                ref for node in revision.nodes for ref in node.source_fragment_ids
            )
            for ref in refs:
                fragment = fragments.get(ref)
                if fragment is None:
                    raise ValueError(f"criteria 引用未知 source fragment: {ref}")
                if fragment.policy_version != policy.policy_version:
                    raise ValueError(f"criteria {revision.rule_id} 引用跨版本 fragment")
                if fragment.source_document_id not in policy.source_document_ids:
                    raise ValueError("criteria fragment 不属于 policy source documents")


def select_current_revision(
    asset: ChronicDiseaseCriteriaAsset,
    *,
    rule_id: str,
    service_date: date,
) -> DiseaseRevision:
    policy = next(
        item
        for item in asset.policy_sets
        if item.policy_role == PolicyRole.CURRENT_RECOGNITION
    )
    matches = [item for item in policy.disease_revisions if item.rule_id == rule_id]
    if len(matches) != 1:
        raise ValueError(f"当前 2025 policy 中 {rule_id} revision 数量不为 1")
    revision = matches[0]
    if revision.effective_from and service_date < revision.effective_from:
        raise ValueError(f"{rule_id} 不适用于服务日期 {service_date}")
    if revision.effective_to and service_date > revision.effective_to:
        raise ValueError(f"{rule_id} 不适用于服务日期 {service_date}")
    return revision


def automatic_evaluation_eligible(
    revision: DiseaseRevision,
    *,
    policy: PolicySet | None = None,
    source_manifest: SourceManifest | None = None,
    service_date: date | None = None,
) -> bool:
    """专家审批、生命周期、完整节点和 blocker 同时 fail closed。"""
    base_ready = (
        revision.lifecycle == RevisionLifecycle.ACTIVE
        and revision.review_status == ReviewStatus.APPROVED
        and revision.execution_status == RevisionExecutionStatus.EVALUATABLE
        and all(
            node.compilation_status == NodeCompilationStatus.COMPILED
            for node in revision.nodes
        )
    )
    if not base_ready or policy is None or source_manifest is None or service_date is None:
        return False
    if (
        not policy.execution_enabled
        or policy.lifecycle != RevisionLifecycle.ACTIVE
        or policy.review_status != ReviewStatus.APPROVED
        or policy.policy_role != PolicyRole.CURRENT_RECOGNITION
        or policy.policy_version != revision.policy_version
        or not any(item == revision for item in policy.disease_revisions)
    ):
        return False
    if policy.effective_from and service_date < policy.effective_from:
        return False
    if policy.effective_to and service_date > policy.effective_to:
        return False
    if revision.effective_from and service_date < revision.effective_from:
        return False
    if revision.effective_to and service_date > revision.effective_to:
        return False
    fragments = {item.source_fragment_id: item for item in source_manifest.fragments}
    refs = set(revision.source_fragment_ids)
    refs.update(ref for node in revision.nodes for ref in node.source_fragment_ids)
    return bool(refs) and all(
        ref in fragments
        and fragments[ref].review_status == ReviewStatus.APPROVED
        and fragments[ref].excerpt_kind == "verbatim"
        and fragments[ref].policy_version == revision.policy_version
        and fragments[ref].source_document_id in policy.source_document_ids
        for ref in refs
    )
