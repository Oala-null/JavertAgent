"""肿瘤知识 release 的本地门禁、确定性编译与 active 指针回滚。

本模块只处理已物化的结构化知识，不连接 authoring 数据库。构建时间等会影响
字节结果的值都由调用方显式传入，确保同一 release 在干净环境重复编译时字节一致。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, get_args

from javert.oncology.eligibility import CriterionType, EligibilityRulesAsset
from javert.oncology.knowledge import (
    KnowledgeEntryMetadata,
    KnowledgeMetadata,
    SourceReference,
    asset_payload_checksum,
    canonical_json_bytes,
)
from javert.oncology.pathology import PathologyKnowledgeAsset
from javert.oncology.regimen import RegimenKnowledgeAsset

from .ids import checksum, release_id as make_release_id, stable_id


DRUG_ASSET = "oncology_drug_kb.json"
ELIGIBILITY_ASSET = "oncology_eligibility_rules.json"
PATHOLOGY_ASSET = "pathology_biomarker_kb.json"
REGIMEN_ASSET = "oncology_regimen_kb.json"
ASSET_FILENAMES = (
    DRUG_ASSET,
    ELIGIBILITY_ASSET,
    PATHOLOGY_ASSET,
    REGIMEN_ASSET,
)
RELEASE_SCHEMA_VERSION = "1.0.0"
ASSET_SCHEMA_VERSION = "2.0.0"
AUTHORITY_SCHEMA_VERSION = "1.0.0"
SUPPORTED_CRITERION_TYPES = frozenset(get_args(CriterionType))

ReleaseStatus = Literal["CANDIDATE", "PUBLISHED"]


@dataclass(frozen=True)
class ReleaseRevision:
    """一个可以被 release 引用的不可变业务 revision。"""

    revision_id: str
    logical_id: str
    asset_name: str
    entity_id: str
    payload: Mapping[str, Any]
    reviewer_id: str
    effective_from: date
    effective_to: date | None
    source_refs: tuple[Mapping[str, Any], ...]
    lifecycle: str = "APPROVED"
    immutable: bool = True
    policy_scope: str = ""
    disposition: str = "approved"
    source_item_ids: tuple[str, ...] = ()
    blocker_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverageItem:
    """来源全集中的唯一分区记录。"""

    source_item_id: str
    source_type: str
    disposition: str
    revision_id: str = ""
    reason_code: str = ""
    source_checksum: str = ""


@dataclass(frozen=True)
class CuratedAtomCoverage:
    """精选规则知识保全台账在 release 时所需的最小投影。"""

    atom_id: str
    source_rule_id: str
    migration_status: str
    oncology: bool
    rule_status: str
    target_id: str = ""
    verification_evidence: str = ""


@dataclass(frozen=True, init=False)
class ReleaseAuthoritySnapshot:
    """经固定 checksum 校验的发布全集；只能由验证工厂创建。"""

    source_snapshot_checksum: str
    curated_manifest_checksum: str
    source_item_ids: tuple[str, ...]
    curated_atoms: tuple[CuratedAtomCoverage, ...]
    authority_checksum: str


@dataclass(frozen=True)
class ReleaseBlocker:
    code: str
    entity_id: str
    detail: str


class ReleaseValidationError(ValueError):
    """发布门禁失败；消息只包含稳定 ID 和安全错误码。"""

    def __init__(self, blockers: Sequence[ReleaseBlocker]):
        self.blockers = tuple(blockers)
        summary = "; ".join(
            f"{item.code}:{item.entity_id}:{item.detail}" for item in self.blockers
        )
        super().__init__(f"release validation failed: {summary}")


@dataclass(frozen=True)
class ReleaseCandidate:
    release_id: str
    source_snapshot_checksum: str
    created_by: str
    release_operator: str
    created_at: str
    revisions: tuple[ReleaseRevision, ...]
    coverage: tuple[CoverageItem, ...]
    curated_atoms: tuple[CuratedAtomCoverage, ...]
    asset_extras: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    status: ReleaseStatus = "CANDIDATE"
    published_at: str = ""


@dataclass(frozen=True)
class CompiledRelease:
    release_id: str
    assets: Mapping[str, bytes]
    manifest: Mapping[str, Any]
    coverage_manifest: Mapping[str, Any]

    @property
    def manifest_bytes(self) -> bytes:
        return canonical_json_bytes(self.manifest)

    @property
    def coverage_bytes(self) -> bytes:
        return canonical_json_bytes(self.coverage_manifest)


def _curated_atom_payload(atom: CuratedAtomCoverage) -> dict[str, Any]:
    return {
        "atom_id": atom.atom_id,
        "source_rule_id": atom.source_rule_id,
        "migration_status": atom.migration_status,
        "oncology": atom.oncology,
        "rule_status": atom.rule_status,
        "target_id": atom.target_id,
        "verification_evidence": atom.verification_evidence,
    }


def _checked_manifest_checksum(
    document: Mapping[str, Any],
    *,
    checksum_field: str,
    expected_checksum: str,
    name: str,
) -> str:
    declared = str(document.get(checksum_field) or "")
    payload = copy.deepcopy(dict(document))
    payload.pop(checksum_field, None)
    actual = checksum(payload)
    if declared != actual:
        raise ValueError(f"{name} 自校验 checksum 不一致")
    if declared != expected_checksum:
        raise ValueError(f"{name} 与固定 checksum 不一致")
    return declared


def build_release_authority_snapshot(
    *,
    source_snapshot_manifest: Mapping[str, Any],
    curated_manifest: Mapping[str, Any],
    expected_source_snapshot_checksum: str,
    expected_curated_manifest_checksum: str,
) -> ReleaseAuthoritySnapshot:
    """从两个已固定 checksum 的完整 manifest 构造不可缩减的发布全集。

    ``expected_*`` 必须来自发布命令所固定的外部审批输入，而不能取自待校验
    manifest 本身。候选构建器只信任本函数返回的投影，不信任调用方另传的列表。
    """

    if source_snapshot_manifest.get("schema_version") != AUTHORITY_SCHEMA_VERSION:
        raise ValueError("source snapshot schema_version 不受支持")
    if curated_manifest.get("schema_version") != AUTHORITY_SCHEMA_VERSION:
        raise ValueError("curated manifest schema_version 不受支持")
    source_checksum = _checked_manifest_checksum(
        source_snapshot_manifest,
        checksum_field="snapshot_checksum",
        expected_checksum=expected_source_snapshot_checksum,
        name="source snapshot",
    )
    curated_checksum = _checked_manifest_checksum(
        curated_manifest,
        checksum_field="manifest_checksum",
        expected_checksum=expected_curated_manifest_checksum,
        name="curated manifest",
    )

    raw_source_item_ids = source_snapshot_manifest.get("source_item_ids")
    if not isinstance(raw_source_item_ids, list) or not raw_source_item_ids:
        raise ValueError("source snapshot source_item_ids 必须为非空列表")
    source_item_ids = tuple(sorted(str(item).strip() for item in raw_source_item_ids))
    if any(not item for item in source_item_ids) or len(set(source_item_ids)) != len(
        source_item_ids
    ):
        raise ValueError("source snapshot source_item_ids 为空或重复")
    source_counts = source_snapshot_manifest.get("counts") or {}
    if source_counts.get("source_items") != len(source_item_ids):
        raise ValueError("source snapshot source_items 计数不一致")

    raw_atoms = curated_manifest.get("atoms")
    if not isinstance(raw_atoms, list) or not raw_atoms:
        raise ValueError("curated manifest atoms 必须为非空列表")
    atoms: list[CuratedAtomCoverage] = []
    allowed_atom_fields = {
        "atom_id",
        "source_rule_id",
        "migration_status",
        "oncology",
        "rule_status",
        "target_id",
        "verification_evidence",
    }
    for raw_atom in raw_atoms:
        if not isinstance(raw_atom, Mapping):
            raise ValueError("curated manifest atom 必须为对象")
        if set(raw_atom) - allowed_atom_fields:
            raise ValueError("curated manifest atom 含非发布投影字段")
        if not isinstance(raw_atom.get("oncology"), bool):
            raise ValueError("curated manifest atom oncology 必须为布尔值")
        try:
            atom = CuratedAtomCoverage(**dict(raw_atom))
        except TypeError as exc:
            raise ValueError("curated manifest atom 字段不完整") from exc
        if not atom.atom_id.strip() or not atom.source_rule_id.strip():
            raise ValueError("curated manifest atom ID 不能为空")
        atoms.append(atom)
    atom_ids = [item.atom_id for item in atoms]
    if len(set(atom_ids)) != len(atom_ids):
        raise ValueError("curated manifest atom_id 重复")
    curated_counts = curated_manifest.get("counts") or {}
    if curated_counts.get("atoms") != len(atoms):
        raise ValueError("curated manifest atoms 计数不一致")

    ordered_atoms = tuple(sorted(atoms, key=lambda item: (item.source_rule_id, item.atom_id)))
    authority_payload = {
        "source_snapshot_checksum": source_checksum,
        "curated_manifest_checksum": curated_checksum,
        "source_item_ids": source_item_ids,
        "curated_atoms": [_curated_atom_payload(item) for item in ordered_atoms],
    }
    authority = object.__new__(ReleaseAuthoritySnapshot)
    object.__setattr__(authority, "source_snapshot_checksum", source_checksum)
    object.__setattr__(authority, "curated_manifest_checksum", curated_checksum)
    object.__setattr__(authority, "source_item_ids", source_item_ids)
    object.__setattr__(authority, "curated_atoms", ordered_atoms)
    object.__setattr__(authority, "authority_checksum", checksum(authority_payload))
    return authority


_ALLOWED_COVERAGE = {
    "approved",
    "in_review",
    "rejected",
    "unsupported",
    "excluded",
    "migration_pending",
}
_BLOCKED_PAYLOAD_VALUES = {
    "unsupported",
    "needs_review",
    "in_review",
    "ambiguous",
    "unable_to_determine",
    "rejected",
}


def _contains_blocked_payload(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered_key = str(key).lower()
            if lowered_key in {"ambiguous", "source_missing", "concept_missing"} and item is True:
                return True
            if lowered_key in {
                "criterion_type",
                "disposition",
                "review_status",
                "resolution_status",
            } and str(item).lower() in _BLOCKED_PAYLOAD_VALUES:
                return True
            if _contains_blocked_payload(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_blocked_payload(item) for item in value)
    return False


def _source_reference_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return SourceReference.model_validate(value).model_dump(mode="json")


def _source_reference_identity(value: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(value.get("source_id") or ""),
        str(value.get("source_fragment_id") or ""),
    )


def _condition_nodes(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    nodes = [value]
    children = value.get("children")
    if isinstance(children, list):
        for child in children:
            nodes.extend(_condition_nodes(child))
    return nodes


def _regimen_component_semantics(
    component: Any,
) -> tuple[str, str, str, str, str]:
    """返回 kind/target/requirement/token/error；兼容旧 drug_concept_id 资产。"""

    if not isinstance(component, Mapping):
        return "", "", "", "", "REGIMEN_COMPONENT_INVALID"
    legacy_id = str(component.get("drug_concept_id") or "").strip()
    raw_kind = str(component.get("target_kind") or "").strip().upper()
    target_kind = raw_kind or ("CONCEPT" if legacy_id else "")
    target_id = str(component.get("target_id") or legacy_id).strip()
    requirement = str(component.get("requirement") or "REQUIRED").strip().upper()
    token = str(component.get("token") or "").strip()
    if target_kind not in {"CONCEPT", "CLASS"}:
        return target_kind, target_id, requirement, token, "REGIMEN_TARGET_KIND_INVALID"
    if requirement not in {"REQUIRED", "OPTIONAL", "WITH_OR_WITHOUT"}:
        return target_kind, target_id, requirement, token, "REGIMEN_REQUIREMENT_INVALID"
    if not target_id or not token:
        return target_kind, target_id, requirement, token, "REGIMEN_COMPONENT_TARGET_MISSING"
    if target_kind == "CONCEPT" and legacy_id and legacy_id != target_id:
        return target_kind, target_id, requirement, token, "REGIMEN_CONCEPT_TARGET_CONFLICT"
    if target_kind == "CLASS" and legacy_id:
        return target_kind, target_id, requirement, token, "REGIMEN_CLASS_AS_CONCEPT"
    return target_kind, target_id, requirement, token, ""


def _cross_asset_blockers(
    revisions: Sequence[ReleaseRevision],
    asset_extras: Mapping[str, Mapping[str, Any]],
) -> list[ReleaseBlocker]:
    blockers: list[ReleaseBlocker] = []
    source_registry: dict[tuple[str, str], bytes] = {}
    for revision in revisions:
        for raw_ref in revision.source_refs:
            try:
                source_ref = _source_reference_payload(raw_ref)
            except Exception:
                blockers.append(
                    ReleaseBlocker(
                        "SOURCE_REFERENCE_INVALID", revision.revision_id, "source_refs schema"
                    )
                )
                continue
            identity = _source_reference_identity(source_ref)
            encoded = canonical_json_bytes(source_ref)
            previous = source_registry.setdefault(identity, encoded)
            if previous != encoded:
                blockers.append(
                    ReleaseBlocker(
                        "SOURCE_REFERENCE_CONFLICT",
                        revision.revision_id,
                        ":".join(identity),
                    )
                )

    drug_concept_ids: set[str] = set()
    for revision in revisions:
        if revision.asset_name != DRUG_ASSET:
            continue
        oncology = revision.payload.get("oncology")
        concept_id = (
            str(oncology.get("drug_concept_id") or "").strip()
            if isinstance(oncology, Mapping)
            else ""
        )
        if not concept_id:
            blockers.append(
                ReleaseBlocker(
                    "ONCOLOGY_DRUG_CONCEPT_ID_MISSING",
                    revision.revision_id,
                    revision.entity_id,
                )
            )
        else:
            drug_concept_ids.add(concept_id)

    pathology_marker_ids = {
        str(revision.payload.get("marker_id") or "").strip()
        for revision in revisions
        if revision.asset_name == PATHOLOGY_ASSET
    }
    pathology_marker_ids.discard("")

    regimen_ids = {
        str(revision.payload.get("regimen_id") or revision.logical_id).strip()
        for revision in revisions
        if revision.asset_name == REGIMEN_ASSET
    }
    regimen_ids.discard("")

    raw_classes = (asset_extras.get(REGIMEN_ASSET) or {}).get("drug_classes")
    approved_class_ids: set[str] = set()
    if raw_classes is not None and not isinstance(raw_classes, list):
        blockers.append(
            ReleaseBlocker("REGIMEN_DRUG_CLASS_INVALID", REGIMEN_ASSET, "drug_classes")
        )
    for raw_class in raw_classes if isinstance(raw_classes, list) else []:
        if not isinstance(raw_class, Mapping):
            blockers.append(
                ReleaseBlocker("REGIMEN_DRUG_CLASS_INVALID", REGIMEN_ASSET, "class")
            )
            continue
        class_id = str(raw_class.get("drug_class_id") or "").strip()
        lifecycle = str(raw_class.get("lifecycle") or "").strip().upper()
        match_terms = raw_class.get("match_terms")
        if (
            not class_id
            or not str(raw_class.get("canonical_name") or "").strip()
            or not str(raw_class.get("reviewer_id") or "").strip()
            or not isinstance(match_terms, list)
            or not any(str(term).strip() for term in match_terms)
        ):
            blockers.append(
                ReleaseBlocker(
                    "REGIMEN_DRUG_CLASS_INVALID", REGIMEN_ASSET, class_id or "class"
                )
            )
            continue
        if class_id in approved_class_ids:
            blockers.append(
                ReleaseBlocker("REGIMEN_DRUG_CLASS_DUPLICATE", REGIMEN_ASSET, class_id)
            )
        if lifecycle not in {"APPROVED", "RELEASED"}:
            blockers.append(
                ReleaseBlocker("DRUG_CLASS_NOT_APPROVED", REGIMEN_ASSET, class_id)
            )
            continue
        approved_class_ids.add(class_id)

    for revision in revisions:
        if revision.asset_name != ELIGIBILITY_ASSET:
            continue
        concept_id = str(revision.payload.get("drug_concept_id") or "").strip()
        if not concept_id:
            blockers.append(
                ReleaseBlocker("DRUG_CONCEPT_REFERENCE_MISSING", revision.revision_id, "eligibility")
            )
        elif concept_id not in drug_concept_ids:
            blockers.append(
                ReleaseBlocker("DRUG_CONCEPT_NOT_FOUND", revision.revision_id, concept_id)
            )
        tree = revision.payload.get("condition_tree")
        if not isinstance(tree, Mapping):
            blockers.append(
                ReleaseBlocker("CONDITION_TREE_INVALID", revision.revision_id, "condition_tree")
            )
            continue
        for node in _condition_nodes(tree):
            if str(node.get("kind") or "").lower() != "leaf":
                continue
            criterion_type = str(node.get("criterion_type") or "").strip()
            if criterion_type == "unsupported":
                blockers.append(
                    ReleaseBlocker(
                        "UNSUPPORTED_CRITERION_TYPE", revision.revision_id, criterion_type
                    )
                )
            elif criterion_type not in SUPPORTED_CRITERION_TYPES:
                blockers.append(
                    ReleaseBlocker(
                        "UNKNOWN_CRITERION_TYPE",
                        revision.revision_id,
                        criterion_type or "missing",
                    )
                )
            if criterion_type == "biomarker":
                expected = node.get("expected")
                marker_id = (
                    str(expected.get("marker_id") or "").strip()
                    if isinstance(expected, Mapping)
                    else ""
                )
                if not marker_id:
                    blockers.append(
                        ReleaseBlocker(
                            "PATHOLOGY_MARKER_REFERENCE_MISSING",
                            revision.revision_id,
                            str(node.get("node_id") or "leaf"),
                        )
                    )
                elif marker_id not in pathology_marker_ids:
                    blockers.append(
                        ReleaseBlocker(
                            "PATHOLOGY_MARKER_NOT_FOUND", revision.revision_id, marker_id
                        )
                    )
            if criterion_type == "combination_requirement":
                expected = node.get("expected")
                target_kind = (
                    str(expected.get("target_kind") or "").strip().upper()
                    if isinstance(expected, Mapping)
                    else ""
                )
                target_id = (
                    str(expected.get("target_id") or "").strip()
                    if isinstance(expected, Mapping)
                    else ""
                )
                requirement = (
                    str(expected.get("requirement") or "").strip().upper()
                    if isinstance(expected, Mapping)
                    else ""
                )
                if target_kind not in {"CONCEPT", "CLASS", "REGIMEN"} or not target_id:
                    blockers.append(
                        ReleaseBlocker(
                            "COMBINATION_TARGET_INVALID", revision.revision_id,
                            str(node.get("node_id") or "leaf"),
                        )
                    )
                elif target_kind == "CONCEPT" and target_id not in drug_concept_ids:
                    blockers.append(
                        ReleaseBlocker("DRUG_CONCEPT_NOT_FOUND", revision.revision_id, target_id)
                    )
                elif target_kind == "CLASS" and target_id not in approved_class_ids:
                    blockers.append(
                        ReleaseBlocker("DRUG_CLASS_NOT_APPROVED", revision.revision_id, target_id)
                    )
                elif target_kind == "REGIMEN" and target_id not in regimen_ids:
                    blockers.append(
                        ReleaseBlocker("REGIMEN_TARGET_NOT_APPROVED", revision.revision_id, target_id)
                    )
                if requirement not in {"REQUIRED", "OPTIONAL", "WITH_OR_WITHOUT"}:
                    blockers.append(
                        ReleaseBlocker(
                            "COMBINATION_REQUIREMENT_INVALID",
                            revision.revision_id,
                            str(node.get("node_id") or "leaf"),
                        )
                    )

    raw_concepts = (asset_extras.get(REGIMEN_ASSET) or {}).get("drug_concepts")
    regimen_concept_ids: set[str] = set()
    if isinstance(raw_concepts, list):
        for raw_concept in raw_concepts:
            concept_id = (
                str(raw_concept.get("concept_id") or "").strip()
                if isinstance(raw_concept, Mapping)
                else ""
            )
            if not concept_id:
                blockers.append(
                    ReleaseBlocker(
                        "REGIMEN_DRUG_CONCEPT_INVALID", REGIMEN_ASSET, "concept_id missing"
                    )
                )
                continue
            if concept_id in regimen_concept_ids:
                blockers.append(
                    ReleaseBlocker(
                        "REGIMEN_DRUG_CONCEPT_DUPLICATE", REGIMEN_ASSET, concept_id
                    )
                )
            regimen_concept_ids.add(concept_id)
            if concept_id not in drug_concept_ids:
                blockers.append(
                    ReleaseBlocker("DRUG_CONCEPT_NOT_FOUND", REGIMEN_ASSET, concept_id)
                )

    for revision in revisions:
        if revision.asset_name != REGIMEN_ASSET:
            continue
        components = revision.payload.get("components")
        if not isinstance(components, list) or not components:
            blockers.append(
                ReleaseBlocker("REGIMEN_COMPONENTS_INVALID", revision.revision_id, "components")
            )
            continue
        for component in components:
            target_kind, target_id, _, _, error_code = _regimen_component_semantics(
                component
            )
            if error_code:
                blockers.append(
                    ReleaseBlocker(
                        error_code, revision.revision_id, target_id or "regimen"
                    )
                )
                continue
            if target_kind == "CLASS":
                if target_id not in approved_class_ids:
                    blockers.append(
                        ReleaseBlocker(
                            "DRUG_CLASS_NOT_APPROVED", revision.revision_id, target_id
                        )
                    )
                continue
            if target_id not in drug_concept_ids:
                blockers.append(
                    ReleaseBlocker("DRUG_CONCEPT_NOT_FOUND", revision.revision_id, target_id)
                )
            elif target_id not in regimen_concept_ids:
                blockers.append(
                    ReleaseBlocker(
                        "REGIMEN_CONCEPT_DICTIONARY_MISSING", revision.revision_id, target_id
                    )
                )
    return blockers


def _revision_blockers(revisions: Sequence[ReleaseRevision]) -> list[ReleaseBlocker]:
    blockers: list[ReleaseBlocker] = []
    known_ids = {item.revision_id for item in revisions}
    if len(known_ids) != len(revisions):
        blockers.append(ReleaseBlocker("DUPLICATE_REVISION_ID", "release", "revision ID 重复"))

    for revision in revisions:
        entity = revision.revision_id or revision.entity_id or "unknown"
        if revision.asset_name not in ASSET_FILENAMES:
            blockers.append(ReleaseBlocker("UNKNOWN_ASSET", entity, revision.asset_name))
        if revision.lifecycle.upper() != "APPROVED":
            blockers.append(ReleaseBlocker("REVISION_NOT_APPROVED", entity, revision.lifecycle))
        if not revision.immutable:
            blockers.append(ReleaseBlocker("REVISION_MUTABLE", entity, "immutable=false"))
        if not revision.reviewer_id.strip():
            blockers.append(ReleaseBlocker("REVIEWER_MISSING", entity, "reviewer_id 为空"))
        if revision.disposition.lower() != "approved":
            blockers.append(ReleaseBlocker("REVISION_BLOCKED", entity, revision.disposition))
        if revision.effective_to is None:
            blockers.append(ReleaseBlocker("FUTURE_WINDOW_MISSING", entity, "effective_to 为空"))
        elif revision.effective_from > revision.effective_to:
            blockers.append(ReleaseBlocker("EFFECTIVE_WINDOW_INVALID", entity, "起止日期倒置"))
        if not revision.source_refs:
            blockers.append(ReleaseBlocker("SOURCE_MISSING", entity, "source_refs 为空"))
        if not revision.source_item_ids:
            blockers.append(
                ReleaseBlocker("REVISION_SOURCE_ITEMS_MISSING", entity, "source_item_ids 为空")
            )
        if _contains_blocked_payload(revision.payload):
            blockers.append(ReleaseBlocker("UNREVIEWED_OR_UNSUPPORTED_PAYLOAD", entity, "payload 未清零"))
        blockers.extend(
            ReleaseBlocker(code, entity, "显式发布阻断项")
            for code in sorted(set(revision.blocker_codes))
        )

    # 同一逻辑规则的不同可发布 revision 使用 inclusive 区间，端点相接也算重叠。
    by_logical: dict[str, dict[str, ReleaseRevision]] = {}
    for revision in revisions:
        by_logical.setdefault(revision.logical_id, {})[revision.revision_id] = revision
    for logical_id, unique_revisions in sorted(by_logical.items()):
        ordered = sorted(
            unique_revisions.values(),
            key=lambda item: (item.effective_from, item.effective_to or date.max, item.revision_id),
        )
        for left, right in zip(ordered, ordered[1:], strict=False):
            if left.effective_to is None or right.effective_from <= left.effective_to:
                blockers.append(
                    ReleaseBlocker(
                        "EFFECTIVE_WINDOW_OVERLAP",
                        logical_id,
                        f"{left.revision_id},{right.revision_id}",
                    )
                )
    return blockers


def _coverage_blockers(
    revisions: Sequence[ReleaseRevision],
    coverage: Sequence[CoverageItem],
    expected_source_item_ids: Sequence[str],
) -> list[ReleaseBlocker]:
    blockers: list[ReleaseBlocker] = []
    revision_ids = {item.revision_id for item in revisions}
    revisions_by_id = {item.revision_id: item for item in revisions}
    seen: dict[str, int] = {}
    for item in coverage:
        seen[item.source_item_id] = seen.get(item.source_item_id, 0) + 1
        if item.disposition not in _ALLOWED_COVERAGE:
            blockers.append(
                ReleaseBlocker("UNKNOWN_COVERAGE_PARTITION", item.source_item_id, item.disposition)
            )
        if item.disposition == "approved" and item.revision_id not in revision_ids:
            blockers.append(
                ReleaseBlocker("APPROVED_SOURCE_NOT_IN_RELEASE", item.source_item_id, item.revision_id)
            )
        if item.disposition == "approved" and item.revision_id in revisions_by_id:
            source_checksums = {
                str(source_ref.get("checksum") or "")
                for source_ref in revisions_by_id[item.revision_id].source_refs
            }
            if not item.source_checksum or item.source_checksum not in source_checksums:
                blockers.append(
                    ReleaseBlocker(
                        "SOURCE_CHECKSUM_NOT_REFERENCED",
                        item.source_item_id,
                        item.revision_id,
                    )
                )
    for source_item_id, count in sorted(seen.items()):
        if count != 1:
            blockers.append(
                ReleaseBlocker("SOURCE_PARTITION_NOT_UNIQUE", source_item_id, f"count={count}")
            )

    expected = set(expected_source_item_ids)
    actual = set(seen)
    for missing in sorted(expected - actual):
        blockers.append(ReleaseBlocker("SOURCE_COVERAGE_GAP", missing, "未进入任何分区"))
    for unexpected in sorted(actual - expected):
        blockers.append(ReleaseBlocker("UNEXPECTED_SOURCE_ITEM", unexpected, "不在来源全集"))

    by_source = {item.source_item_id: item for item in coverage}
    for revision in revisions:
        for source_item_id in revision.source_item_ids:
            item = by_source.get(source_item_id)
            if item is None:
                blockers.append(
                    ReleaseBlocker("REVISION_SOURCE_UNPARTITIONED", revision.revision_id, source_item_id)
                )
            elif item.disposition != "approved" or item.revision_id != revision.revision_id:
                blockers.append(
                    ReleaseBlocker("REVISION_SOURCE_NOT_APPROVED", revision.revision_id, source_item_id)
                )
    return blockers


def _curated_blockers(
    atoms: Sequence[CuratedAtomCoverage], revisions: Sequence[ReleaseRevision]
) -> list[ReleaseBlocker]:
    blockers: list[ReleaseBlocker] = []
    released_logical_ids = {item.logical_id for item in revisions}
    for atom in atoms:
        status = atom.migration_status.upper()
        rule_status = atom.rule_status.lower()
        if atom.oncology and status != "VERIFIED":
            blockers.append(
                ReleaseBlocker(
                    "ONCOLOGY_ATOM_NOT_VERIFIED",
                    atom.atom_id,
                    f"rule={atom.source_rule_id},target={atom.target_id or 'missing'}",
                )
            )
        if status == "VERIFIED" and (not atom.target_id or not atom.verification_evidence):
            blockers.append(
                ReleaseBlocker("VERIFIED_ATOM_EVIDENCE_MISSING", atom.atom_id, atom.source_rule_id)
            )
        if not atom.oncology and status != "VERIFIED" and rule_status != "drafting":
            blockers.append(
                ReleaseBlocker("PENDING_RULE_NOT_DRAFTING", atom.atom_id, atom.source_rule_id)
            )
        if not atom.oncology and status == "MAPPED" and not atom.target_id:
            blockers.append(
                ReleaseBlocker("MAPPED_ATOM_TARGET_MISSING", atom.atom_id, atom.source_rule_id)
            )
        if status != "VERIFIED" and atom.source_rule_id in released_logical_ids:
            blockers.append(
                ReleaseBlocker("PENDING_ATOM_REFERENCED", atom.atom_id, atom.source_rule_id)
            )
    return blockers


def build_release_candidate(
    *,
    revisions: Sequence[ReleaseRevision],
    coverage: Sequence[CoverageItem],
    curated_atoms: Sequence[CuratedAtomCoverage],
    source_snapshot_checksum: str,
    created_by: str,
    release_operator: str,
    created_at: str,
    expected_source_item_ids: Sequence[str],
    authority_snapshot: ReleaseAuthoritySnapshot | None = None,
    asset_extras: Mapping[str, Mapping[str, Any]] | None = None,
) -> ReleaseCandidate:
    """验证发布全集并创建稳定 release candidate。"""

    blockers: list[ReleaseBlocker] = []
    authoritative_source_item_ids = tuple(expected_source_item_ids)
    authoritative_curated_atoms = tuple(curated_atoms)
    if authority_snapshot is None:
        blockers.append(
            ReleaseBlocker(
                "AUTHORITATIVE_SNAPSHOT_REQUIRED",
                "release",
                "必须使用固定 checksum 的来源与精选知识 manifest",
            )
        )
    else:
        try:
            authority_payload = {
                "source_snapshot_checksum": authority_snapshot.source_snapshot_checksum,
                "curated_manifest_checksum": authority_snapshot.curated_manifest_checksum,
                "source_item_ids": authority_snapshot.source_item_ids,
                "curated_atoms": [
                    _curated_atom_payload(item) for item in authority_snapshot.curated_atoms
                ],
            }
            authority_valid = authority_snapshot.authority_checksum == checksum(authority_payload)
        except (AttributeError, TypeError):
            authority_valid = False
        if not authority_valid:
            blockers.append(
                ReleaseBlocker("AUTHORITY_SNAPSHOT_INVALID", "release", "authority checksum")
            )
        else:
            authoritative_source_item_ids = authority_snapshot.source_item_ids
            authoritative_curated_atoms = authority_snapshot.curated_atoms
            if source_snapshot_checksum != authority_snapshot.source_snapshot_checksum:
                blockers.append(
                    ReleaseBlocker(
                        "SOURCE_SNAPSHOT_AUTHORITY_MISMATCH",
                        "release",
                        source_snapshot_checksum,
                    )
                )
            if tuple(sorted(str(item) for item in expected_source_item_ids)) != tuple(
                sorted(authority_snapshot.source_item_ids)
            ):
                blockers.append(
                    ReleaseBlocker(
                        "SOURCE_UNIVERSE_AUTHORITY_MISMATCH",
                        "release",
                        "调用方来源全集与 authority 不一致",
                    )
                )
            supplied_atoms = sorted(
                (_curated_atom_payload(item) for item in curated_atoms),
                key=lambda item: (item["source_rule_id"], item["atom_id"]),
            )
            authority_atoms = [
                _curated_atom_payload(item) for item in authority_snapshot.curated_atoms
            ]
            if supplied_atoms != authority_atoms:
                blockers.append(
                    ReleaseBlocker(
                        "CURATED_AUTHORITY_MISMATCH",
                        "release",
                        "调用方精选原子与 authority 不一致",
                    )
                )
    if not revisions:
        blockers.append(ReleaseBlocker("EMPTY_RELEASE", "release", "没有 revision"))
    if not source_snapshot_checksum.startswith("sha256:"):
        blockers.append(
            ReleaseBlocker("SOURCE_SNAPSHOT_CHECKSUM_INVALID", "release", source_snapshot_checksum)
        )
    if not created_by.strip() or not release_operator.strip() or not created_at.strip():
        blockers.append(ReleaseBlocker("RELEASE_ACTOR_OR_TIME_MISSING", "release", "必填字段为空"))
    reviewers = {item.reviewer_id for item in revisions if item.reviewer_id}
    if release_operator in reviewers:
        blockers.append(
            ReleaseBlocker("DUTY_SEPARATION_FAILED", "release", release_operator)
        )
    blockers.extend(_revision_blockers(revisions))
    blockers.extend(
        _coverage_blockers(revisions, coverage, authoritative_source_item_ids)
    )
    blockers.extend(_curated_blockers(authoritative_curated_atoms, revisions))
    blockers.extend(_cross_asset_blockers(revisions, asset_extras or {}))
    present_assets = {item.asset_name for item in revisions}
    for asset_name in ASSET_FILENAMES:
        if asset_name not in present_assets:
            blockers.append(ReleaseBlocker("ASSET_HAS_NO_REVISION", asset_name, "release 资产为空"))
    if blockers:
        raise ReleaseValidationError(sorted(blockers, key=lambda item: (item.code, item.entity_id)))

    ordered_revisions = tuple(
        sorted(revisions, key=lambda item: (item.asset_name, item.logical_id, item.entity_id, item.revision_id))
    )
    # 精选知识 backlog/验证状态也是 release 的受控内容；若只把业务 revision 与
    # source snapshot 放进 ID，同一 ID 可能对应不同 coverage manifest。
    authority_checksum = (
        authority_snapshot.authority_checksum if authority_snapshot is not None else ""
    )
    candidate_id = make_release_id(
        [
            *(item.revision_id for item in ordered_revisions),
            f"authority:{authority_checksum}",
        ],
        source_snapshot_checksum,
    )
    return ReleaseCandidate(
        release_id=candidate_id,
        source_snapshot_checksum=source_snapshot_checksum,
        created_by=created_by,
        release_operator=release_operator,
        created_at=created_at,
        revisions=ordered_revisions,
        coverage=tuple(sorted(coverage, key=lambda item: item.source_item_id)),
        curated_atoms=tuple(
            sorted(
                authoritative_curated_atoms,
                key=lambda item: (item.source_rule_id, item.atom_id),
            )
        ),
        asset_extras=copy.deepcopy(asset_extras or {}),
    )


def publish_candidate(
    candidate: ReleaseCandidate, *, published_by: str, published_at: str
) -> ReleaseCandidate:
    if candidate.status != "CANDIDATE":
        raise ValueError("只有 CANDIDATE 可以发布")
    if published_by != candidate.release_operator:
        raise ValueError("published_by 必须是 release_operator")
    if not published_at.strip():
        raise ValueError("published_at 不能为空")
    return replace(candidate, status="PUBLISHED", published_at=published_at)


def _source_refs(revisions: Sequence[ReleaseRevision]) -> list[dict[str, Any]]:
    unique: dict[bytes, dict[str, Any]] = {}
    for revision in revisions:
        for source_ref in revision.source_refs:
            value = json.loads(json.dumps(source_ref, ensure_ascii=False, default=str))
            unique[canonical_json_bytes(value)] = value
    return [unique[key] for key in sorted(unique)]


def _entry_payload(candidate: ReleaseCandidate, revision: ReleaseRevision) -> dict[str, Any]:
    payload = json.loads(json.dumps(revision.payload, ensure_ascii=False, default=str))
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "content_version": candidate.release_id,
            "effective_from": revision.effective_from.isoformat(),
            "effective_to": revision.effective_to.isoformat() if revision.effective_to else None,
            "source_refs": _source_refs([revision]),
            "review_status": "approved",
            "release_id": candidate.release_id,
            "rule_revision_id": revision.revision_id,
        }
    )
    if revision.policy_scope:
        metadata["policy_scope"] = revision.policy_scope
    payload["metadata"] = metadata
    return payload


def _asset_metadata(candidate: ReleaseCandidate, revisions: Sequence[ReleaseRevision]) -> dict[str, Any]:
    effective_to_values = [item.effective_to for item in revisions if item.effective_to is not None]
    return {
        "schema_version": ASSET_SCHEMA_VERSION,
        "content_version": candidate.release_id,
        "effective_from": min(item.effective_from for item in revisions).isoformat(),
        "effective_to": max(effective_to_values).isoformat() if effective_to_values else None,
        "source_refs": _source_refs(revisions),
        "checksum": "sha256:" + "0" * 64,
        "review_status": "approved",
        "release_id": candidate.release_id,
        "release_status": candidate.status.lower(),
        "source_snapshot_checksum": candidate.source_snapshot_checksum,
        "revision_ids": sorted(item.revision_id for item in revisions),
        "built_at": candidate.created_at,
        "published_at": candidate.published_at or None,
    }


def _compile_asset(candidate: ReleaseCandidate, asset_name: str) -> tuple[dict[str, Any], bytes]:
    revisions = [item for item in candidate.revisions if item.asset_name == asset_name]
    entries = [_entry_payload(candidate, item) for item in revisions]
    extras = json.loads(
        json.dumps(candidate.asset_extras.get(asset_name, {}), ensure_ascii=False, default=str)
    )
    metadata = _asset_metadata(candidate, revisions)

    if asset_name == DRUG_ASSET:
        asset: dict[str, Any] = {
            "version": str(extras.get("version") or "2.0"),
            "metadata": metadata,
            "sources": extras.get("sources") or {},
            "policy": extras.get("policy") or {},
            "stats": extras.get("stats")
            or {"drug_count": len(entries), "release_revision_count": len(revisions)},
            "drugs": {
                revision.entity_id: entry
                for revision, entry in sorted(
                    zip(revisions, entries, strict=True), key=lambda pair: pair[0].entity_id
                )
            },
        }
    else:
        asset_type = {
            ELIGIBILITY_ASSET: "eligibility",
            PATHOLOGY_ASSET: "pathology",
            REGIMEN_ASSET: "regimen",
        }[asset_name]
        asset = {"asset_type": asset_type, "metadata": metadata, "entries": entries}
        if asset_name == REGIMEN_ASSET:
            asset["drug_concepts"] = extras.get("drug_concepts") or []
            asset["drug_classes"] = extras.get("drug_classes") or []

    asset["metadata"]["checksum"] = asset_payload_checksum(asset)
    return asset, canonical_json_bytes(asset)


def _release_items(candidate: ReleaseCandidate) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for revision in candidate.revisions:
        items.append(
            {
                "asset_name": revision.asset_name,
                "logical_id": revision.logical_id,
                "entity_id": revision.entity_id,
                "revision_id": revision.revision_id,
                # diff 比较业务内容；release_id/content_version 等编译元数据不能让
                # 未变化的 revision 在每次发布时都被误报为 modified。
                "payload_checksum": checksum(revision.payload),
                "source_checksums": sorted(
                    str(item.get("checksum") or "") for item in revision.source_refs
                ),
                "effective_from": revision.effective_from.isoformat(),
                "effective_to": revision.effective_to.isoformat() if revision.effective_to else None,
                "reviewer_id": revision.reviewer_id,
                "policy_scope": revision.policy_scope,
            }
        )
    return sorted(items, key=lambda item: (item["asset_name"], item["logical_id"], item["entity_id"]))


def _diff_manifest(
    current_items: Sequence[Mapping[str, Any]],
    previous_manifest: Mapping[str, Any] | None,
    drafting_backlog_rules: set[str],
) -> dict[str, list[dict[str, Any]]]:
    previous_items = list((previous_manifest or {}).get("release_items") or [])

    def key(item: Mapping[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("asset_name") or ""),
            str(item.get("logical_id") or ""),
            str(item.get("entity_id") or ""),
        )

    current = {key(item): dict(item) for item in current_items}
    previous = {key(item): dict(item) for item in previous_items}
    added = [current[item] for item in sorted(current.keys() - previous.keys())]
    removed_keys = previous.keys() - current.keys()
    retired = [
        previous[item]
        for item in sorted(removed_keys)
        if str(previous[item].get("logical_id")) not in drafting_backlog_rules
    ]
    drafting_backlog = [
        previous[item]
        for item in sorted(removed_keys)
        if str(previous[item].get("logical_id")) in drafting_backlog_rules
    ]
    modified: list[dict[str, Any]] = []
    source_changes: list[dict[str, Any]] = []
    date_changes: list[dict[str, Any]] = []
    for item_key in sorted(current.keys() & previous.keys()):
        before = previous[item_key]
        after = current[item_key]
        identity = {
            "asset_name": after["asset_name"],
            "logical_id": after["logical_id"],
            "entity_id": after["entity_id"],
            "before_revision_id": before.get("revision_id"),
            "after_revision_id": after.get("revision_id"),
        }
        if (
            before.get("payload_checksum") != after.get("payload_checksum")
            or before.get("revision_id") != after.get("revision_id")
        ):
            modified.append(identity)
        if before.get("source_checksums") != after.get("source_checksums"):
            source_changes.append(identity)
        if (before.get("effective_from"), before.get("effective_to")) != (
            after.get("effective_from"),
            after.get("effective_to"),
        ):
            date_changes.append(identity)
    return {
        "added": added,
        "modified": modified,
        "retired": retired,
        "source_changes": source_changes,
        "effective_date_changes": date_changes,
        "drafting_backlog": drafting_backlog,
    }


def _with_checksum(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(payload))
    value.pop("checksum", None)
    value["checksum"] = checksum(value)
    return value


def compile_release(
    candidate: ReleaseCandidate,
    *,
    previous_manifest: Mapping[str, Any] | None = None,
) -> CompiledRelease:
    """把 candidate 确定性编译为四资产、release manifest 和 coverage manifest。"""

    assets: dict[str, bytes] = {}
    asset_checksums: dict[str, dict[str, str]] = {}
    for asset_name in ASSET_FILENAMES:
        asset, data = _compile_asset(candidate, asset_name)
        assets[asset_name] = data
        asset_checksums[asset_name] = {
            "payload_checksum": str(asset["metadata"]["checksum"]),
            "file_checksum": "sha256:" + hashlib.sha256(data).hexdigest(),
        }

    release_items = _release_items(candidate)
    partitions: dict[str, list[dict[str, Any]]] = {
        disposition: [] for disposition in sorted(_ALLOWED_COVERAGE)
    }
    for item in candidate.coverage:
        partitions[item.disposition].append(
            {
                "source_item_id": item.source_item_id,
                "source_type": item.source_type,
                "revision_id": item.revision_id,
                "reason_code": item.reason_code,
                "source_checksum": item.source_checksum,
            }
        )
    backlog = [
        {
            "atom_id": atom.atom_id,
            "source_rule_id": atom.source_rule_id,
            "migration_status": atom.migration_status,
            "rule_status": atom.rule_status,
            "target_id": atom.target_id,
        }
        for atom in candidate.curated_atoms
        if not atom.oncology and atom.migration_status.upper() != "VERIFIED"
    ]
    backlog_rules = {item["source_rule_id"] for item in backlog}
    coverage_manifest = _with_checksum(
        {
            "schema_version": RELEASE_SCHEMA_VERSION,
            "release_id": candidate.release_id,
            "source_snapshot_checksum": candidate.source_snapshot_checksum,
            "partitions": partitions,
            "partition_counts": {key: len(value) for key, value in partitions.items()},
            "curated_atoms": [
                {
                    "atom_id": atom.atom_id,
                    "source_rule_id": atom.source_rule_id,
                    "migration_status": atom.migration_status,
                    "oncology": atom.oncology,
                    "rule_status": atom.rule_status,
                    "target_id": atom.target_id,
                    "verification_evidence": atom.verification_evidence,
                }
                for atom in candidate.curated_atoms
            ],
            "drafting_backlog": backlog,
            "diff": _diff_manifest(release_items, previous_manifest, backlog_rules),
        }
    )
    manifest = _with_checksum(
        {
            "schema_version": RELEASE_SCHEMA_VERSION,
            "release_id": candidate.release_id,
            "release_status": candidate.status,
            "source_snapshot_checksum": candidate.source_snapshot_checksum,
            "created_by": candidate.created_by,
            "release_operator": candidate.release_operator,
            "created_at": candidate.created_at,
            "published_at": candidate.published_at or None,
            "revision_ids": [item.revision_id for item in candidate.revisions],
            "release_items": release_items,
            "asset_checksums": asset_checksums,
            "coverage_checksum": coverage_manifest["checksum"],
        }
    )
    return CompiledRelease(
        release_id=candidate.release_id,
        assets=assets,
        manifest=manifest,
        coverage_manifest=coverage_manifest,
    )


def _validate_document_checksum(document: Mapping[str, Any], *, name: str) -> None:
    declared = str(document.get("checksum") or "")
    payload = copy.deepcopy(dict(document))
    payload.pop("checksum", None)
    expected = checksum(payload)
    if declared != expected:
        raise ValueError(f"{name} checksum 不一致: declared={declared}, expected={expected}")


def _validated_source_ref_set(
    raw_refs: Any,
    *,
    context: str,
    registry: dict[tuple[str, str], bytes],
) -> set[bytes]:
    if not isinstance(raw_refs, list) or not raw_refs:
        raise ValueError(f"{context} source_refs 为空或结构非法")
    values: set[bytes] = set()
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, Mapping):
            raise ValueError(f"{context} source_refs 结构非法")
        try:
            source_ref = _source_reference_payload(raw_ref)
        except Exception as exc:
            raise ValueError(f"{context} source_refs schema 非法") from exc
        encoded = canonical_json_bytes(source_ref)
        identity = _source_reference_identity(source_ref)
        previous = registry.setdefault(identity, encoded)
        if previous != encoded:
            raise ValueError(f"跨资产 source_ref 冲突: {identity[0]}")
        values.add(encoded)
    return values


def _validate_compiled_asset_graph(raw_assets: Mapping[str, Mapping[str, Any]]) -> None:
    source_registry: dict[tuple[str, str], bytes] = {}
    entry_groups: dict[str, list[Mapping[str, Any]]] = {}
    for asset_name in ASSET_FILENAMES:
        raw = raw_assets[asset_name]
        metadata = raw.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError(f"{asset_name} metadata 结构非法")
        try:
            KnowledgeMetadata.model_validate(metadata)
        except Exception as exc:
            raise ValueError(f"{asset_name} metadata schema 非法") from exc
        asset_source_refs = _validated_source_ref_set(
            metadata.get("source_refs"),
            context=f"{asset_name} metadata",
            registry=source_registry,
        )
        if asset_name == DRUG_ASSET:
            drugs = raw.get("drugs")
            if not isinstance(drugs, Mapping) or not drugs:
                raise ValueError("oncology_drug_kb.json drugs 为空")
            entries = list(drugs.values())
        else:
            entries = raw.get("entries")
            if not isinstance(entries, list) or not entries:
                raise ValueError(f"{asset_name} entries 为空")
        if any(not isinstance(item, Mapping) for item in entries):
            raise ValueError(f"{asset_name} entry 结构非法")
        entry_groups[asset_name] = list(entries)
        entry_source_refs: set[bytes] = set()
        for entry in entries:
            entry_metadata = entry.get("metadata")
            if not isinstance(entry_metadata, Mapping):
                raise ValueError(f"{asset_name} entry metadata 缺失")
            try:
                KnowledgeEntryMetadata.model_validate(entry_metadata)
            except Exception as exc:
                raise ValueError(f"{asset_name} entry metadata schema 非法") from exc
            entry_source_refs.update(
                _validated_source_ref_set(
                    entry_metadata.get("source_refs"),
                    context=f"{asset_name} entry",
                    registry=source_registry,
                )
            )
        if entry_source_refs != asset_source_refs:
            raise ValueError(f"{asset_name} entry 与 asset source_refs 不一致")

    drug_concept_ids: set[str] = set()
    for entry in entry_groups[DRUG_ASSET]:
        oncology = entry.get("oncology")
        concept_id = (
            str(oncology.get("drug_concept_id") or "").strip()
            if isinstance(oncology, Mapping)
            else ""
        )
        if not concept_id:
            raise ValueError("oncology_drug_kb.json 条目缺少 oncology.drug_concept_id")
        drug_concept_ids.add(concept_id)

    eligibility = EligibilityRulesAsset.model_validate(raw_assets[ELIGIBILITY_ASSET])
    pathology = PathologyKnowledgeAsset.model_validate(raw_assets[PATHOLOGY_ASSET])
    regimen = RegimenKnowledgeAsset.model_validate(raw_assets[REGIMEN_ASSET])
    pathology_marker_ids = {item.marker_id for item in pathology.entries}
    regimen_ids = {item.regimen_id for item in regimen.entries}
    regimen_class_ids = {item.drug_class_id for item in regimen.drug_classes}

    for entry in eligibility.entries:
        if entry.drug_concept_id not in drug_concept_ids:
            raise ValueError(
                f"eligibility 引用未知 drug_concept_id: {entry.drug_concept_id}"
            )
        for node in _condition_nodes(entry.condition_tree.model_dump(mode="json")):
            if str(node.get("kind") or "") != "leaf":
                continue
            criterion_type = str(node.get("criterion_type") or "")
            if criterion_type == "unsupported":
                raise ValueError("eligibility 含 unsupported criterion_type")
            if criterion_type not in SUPPORTED_CRITERION_TYPES:
                raise ValueError(f"eligibility 含未知 criterion_type: {criterion_type}")
            if criterion_type == "biomarker":
                expected = node.get("expected") or {}
                marker_id = str(expected.get("marker_id") or "")
                if not marker_id or marker_id not in pathology_marker_ids:
                    raise ValueError(f"eligibility 引用未知 pathology marker: {marker_id}")
            if criterion_type == "combination_requirement":
                expected = node.get("expected") or {}
                target_kind = str(expected.get("target_kind") or "").upper()
                target_id = str(expected.get("target_id") or "")
                requirement = str(expected.get("requirement") or "").upper()
                if target_kind not in {"CONCEPT", "CLASS", "REGIMEN"} or not target_id:
                    raise ValueError("eligibility 联合用药目标结构非法")
                if target_kind == "CONCEPT" and target_id not in drug_concept_ids:
                    raise ValueError(
                        f"eligibility 引用未知 drug_concept_id: {target_id}"
                    )
                if target_kind == "CLASS" and target_id not in regimen_class_ids:
                    raise ValueError(
                        f"eligibility 引用未知或未批准 drug_class_id: {target_id}"
                    )
                if target_kind == "REGIMEN" and target_id not in regimen_ids:
                    raise ValueError(
                        f"eligibility 引用未知或未批准 regimen_id: {target_id}"
                    )
                if requirement not in {"REQUIRED", "OPTIONAL", "WITH_OR_WITHOUT"}:
                    raise ValueError("eligibility 联合用药 requirement 非法")

    regimen_concept_ids = [item.concept_id for item in regimen.drug_concepts]
    if len(set(regimen_concept_ids)) != len(regimen_concept_ids):
        raise ValueError("regimen drug_concepts concept_id 重复")
    for concept_id in regimen_concept_ids:
        if concept_id not in drug_concept_ids:
            raise ValueError(f"regimen 字典引用未知 drug_concept_id: {concept_id}")
    regimen_concept_set = set(regimen_concept_ids)
    for entry in regimen.entries:
        for component in entry.components:
            if component.target_kind.value == "CLASS":
                if component.target_id not in regimen_class_ids:
                    raise ValueError(
                        f"regimen 组分引用未知或未批准 drug_class_id: {component.target_id}"
                    )
                continue
            if component.target_id not in drug_concept_ids:
                raise ValueError(
                    f"regimen 组分引用未知 drug_concept_id: {component.target_id}"
                )
            if component.target_id not in regimen_concept_set:
                raise ValueError(
                    f"regimen 组分未进入 drug_concepts: {component.target_id}"
                )


def validate_compiled_release(
    compiled: CompiledRelease, *, require_published: bool = True
) -> None:
    """部署前验证 manifest、四资产 schema/checksum 与审核状态。"""

    manifest = dict(compiled.manifest)
    coverage_manifest = dict(compiled.coverage_manifest)
    if manifest.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise ValueError("release manifest schema_version 不受支持")
    if coverage_manifest.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise ValueError("coverage manifest schema_version 不受支持")
    if manifest.get("release_id") != compiled.release_id:
        raise ValueError("release manifest ID 不一致")
    if coverage_manifest.get("release_id") != compiled.release_id:
        raise ValueError("coverage manifest ID 不一致")
    if require_published and manifest.get("release_status") != "PUBLISHED":
        raise ValueError("运行时只接受 PUBLISHED release")
    if set(compiled.assets) != set(ASSET_FILENAMES):
        raise ValueError("release 必须完整包含四个 JSON 资产")
    _validate_document_checksum(manifest, name="release manifest")
    _validate_document_checksum(coverage_manifest, name="coverage manifest")
    if manifest.get("coverage_checksum") != coverage_manifest.get("checksum"):
        raise ValueError("release 与 coverage checksum 不一致")
    if coverage_manifest.get("source_snapshot_checksum") != manifest.get(
        "source_snapshot_checksum"
    ):
        raise ValueError("release 与 coverage source snapshot 不一致")
    asset_checksums = manifest.get("asset_checksums")
    if not isinstance(asset_checksums, Mapping) or set(asset_checksums) != set(
        ASSET_FILENAMES
    ):
        raise ValueError("release manifest asset_checksums 不完整")

    raw_assets: dict[str, dict[str, Any]] = {}
    for asset_name in ASSET_FILENAMES:
        data = compiled.assets[asset_name]
        raw = json.loads(data)
        if not isinstance(raw, dict):
            raise ValueError(f"{asset_name} 顶层结构非法")
        raw_assets[asset_name] = raw
        checksums = asset_checksums[asset_name]
        if not isinstance(checksums, Mapping):
            raise ValueError(f"{asset_name} manifest checksum 结构非法")
        file_checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        if checksums.get("file_checksum") != file_checksum:
            raise ValueError(f"{asset_name} file checksum 不一致")
        expected_payload = asset_payload_checksum(raw)
        if raw.get("metadata", {}).get("checksum") != expected_payload:
            raise ValueError(f"{asset_name} payload checksum 不一致")
        if checksums.get("payload_checksum") != expected_payload:
            raise ValueError(f"{asset_name} 与 manifest checksum 不一致")
        metadata = raw.get("metadata") or {}
        if metadata.get("schema_version") != ASSET_SCHEMA_VERSION:
            raise ValueError(f"{asset_name} schema_version 不受支持")
        if metadata.get("release_id") != compiled.release_id:
            raise ValueError(f"{asset_name} release_id 不一致")
        if metadata.get("release_status") != str(manifest.get("release_status")).lower():
            raise ValueError(f"{asset_name} release_status 不一致")
        if metadata.get("review_status") != "approved":
            raise ValueError(f"{asset_name} 未经批准")
        if metadata.get("source_snapshot_checksum") != manifest.get(
            "source_snapshot_checksum"
        ):
            raise ValueError(f"{asset_name} source_snapshot_checksum 不一致")
        if _contains_blocked_payload(raw):
            raise ValueError(f"{asset_name} 含 unsupported 或未审核条目")

    _validate_compiled_asset_graph(raw_assets)


def write_release_bundle(compiled: CompiledRelease, releases_dir: Path) -> Path:
    """幂等写入不可变本地 release bundle；同 ID 不允许内容漂移。"""

    bundle_dir = releases_dir / compiled.release_id
    expected = {
        **dict(compiled.assets),
        "release_manifest.json": compiled.manifest_bytes,
        "coverage_manifest.json": compiled.coverage_bytes,
    }
    if bundle_dir.exists():
        for name, data in expected.items():
            path = bundle_dir / name
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError(f"已存在 release bundle 内容漂移: {compiled.release_id}/{name}")
        return bundle_dir
    bundle_dir.mkdir(parents=True, exist_ok=False)
    for name, data in expected.items():
        (bundle_dir / name).write_bytes(data)
    return bundle_dir


def load_release_bundle(releases_dir: Path, release_id: str) -> CompiledRelease:
    bundle_dir = releases_dir / release_id
    manifest = json.loads((bundle_dir / "release_manifest.json").read_text(encoding="utf-8"))
    coverage = json.loads((bundle_dir / "coverage_manifest.json").read_text(encoding="utf-8"))
    assets = {name: (bundle_dir / name).read_bytes() for name in ASSET_FILENAMES}
    return CompiledRelease(
        release_id=release_id,
        assets=assets,
        manifest=manifest,
        coverage_manifest=coverage,
    )


def resolve_active_release_assets(releases_dir: Path) -> dict[str, Path]:
    """解析并完整验证 active published bundle，失败时不回退到未发布资产。"""
    pointer_path = releases_dir / "active_release.json"
    if not pointer_path.is_file():
        raise ValueError("active release 指针不存在")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    active_id = str(pointer.get("release_id") or "")
    if not active_id:
        raise ValueError("active release ID 为空")
    compiled = load_release_bundle(releases_dir, active_id)
    validate_compiled_release(compiled, require_published=True)
    if pointer.get("manifest_checksum") != compiled.manifest.get("checksum"):
        raise ValueError("active release pointer checksum 不一致")
    bundle_dir = releases_dir / active_id
    return {name: bundle_dir / name for name in ASSET_FILENAMES}


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validated_release_pointer(releases_dir: Path, release_id: str) -> dict[str, Any]:
    compiled = load_release_bundle(releases_dir, release_id)
    validate_compiled_release(compiled, require_published=True)
    return {
        "release_id": release_id,
        "manifest_checksum": compiled.manifest["checksum"],
    }


def activate_release(releases_dir: Path, release_id: str) -> dict[str, Any]:
    """验证已发布 bundle 后原子切换 active 指针。"""

    pointer = _validated_release_pointer(releases_dir, release_id)
    releases_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(releases_dir / "active_release.json", canonical_json_bytes(pointer))
    return pointer


def rollback_release(
    releases_dir: Path,
    *,
    target_release_id: str,
    actor: str,
    reason: str,
    occurred_at: str,
) -> dict[str, Any]:
    """切回旧的 published bundle；不删除任何 release、revision 或审计历史。"""

    pointer_path = releases_dir / "active_release.json"
    if not pointer_path.is_file():
        raise ValueError("active release 指针不存在")
    current = json.loads(pointer_path.read_text(encoding="utf-8"))
    previous_release_id = str(current.get("release_id") or "")
    if target_release_id == previous_release_id:
        raise ValueError("目标 release 已处于 active")
    if not actor.strip() or not reason.strip() or not occurred_at.strip():
        raise ValueError("rollback actor/reason/occurred_at 不能为空")

    pointer = _validated_release_pointer(releases_dir, target_release_id)
    event = {
        "event_id": stable_id(
            "rollback", previous_release_id, target_release_id, actor, occurred_at, reason
        ),
        "event_type": "RELEASE_ROLLBACK",
        "from_release_id": previous_release_id,
        "to_release_id": target_release_id,
        "actor": actor,
        "reason": reason,
        "occurred_at": occurred_at,
        "target_manifest_checksum": pointer["manifest_checksum"],
    }
    event_path = releases_dir / "release_events.jsonl"
    previous_events = event_path.read_bytes() if event_path.is_file() else None
    event_prefix = previous_events or b""
    if event_prefix and not event_prefix.endswith(b"\n"):
        event_prefix += b"\n"

    # 两个文件不能由 os.replace 一次提交：先提交事件，事件失败时 active 指针尚未
    # 改动；随后若指针提交失败，则把事件日志恢复到调用前字节。
    _atomic_write(event_path, event_prefix + canonical_json_bytes(event))
    try:
        _atomic_write(pointer_path, canonical_json_bytes(pointer))
    except Exception:
        try:
            if previous_events is None:
                event_path.unlink(missing_ok=True)
            else:
                _atomic_write(event_path, previous_events)
        except Exception as restore_error:
            raise RuntimeError("rollback 事件日志恢复失败") from restore_error
        raise
    return event
