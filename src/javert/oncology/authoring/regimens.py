"""治疗方案 authoring 投影与歧义 QA。"""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, model_validator

from javert.oncology.regimen import normalize_regimen_alias

from .ids import canonical_json_bytes, checksum, revision_id, stable_id
from .models import (
    CombinationRequirement,
    DrugClassRecord,
    EffectiveWindow,
    RevisionLifecycle,
    TargetKind,
)
from .sources import build_drug_crosswalk


class RegimenRevisionRecord(BaseModel):
    logical_regimen_id: str
    regimen_revision_id: str
    canonical_name: str
    lifecycle: RevisionLifecycle = RevisionLifecycle.DRAFT
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    effective_window: EffectiveWindow = Field(default_factory=EffectiveWindow)
    supersedes_revision_id: str | None = None


class RegimenAliasRecord(BaseModel):
    alias_id: str
    regimen_revision_id: str | None = None
    original_alias: str
    normalized_alias: str
    alias_type: str = "name"
    language: str = "und"
    aggregate_frequency: int | None = None
    source_corpus_checksum: str = ""
    review_status: str = "needs_review"
    ambiguous: bool = False


class RegimenContextRecord(BaseModel):
    context_id: str
    regimen_revision_id: str
    cancer_context: str
    histology: str = ""
    clinical_setting: str = ""


class RegimenComponentRecord(BaseModel):
    component_id: str
    regimen_revision_id: str
    target_kind: TargetKind
    target_id: str
    token: str
    component_role: str = "therapy"
    requirement: CombinationRequirement = CombinationRequirement.REQUIRED
    sibling_order: int = Field(ge=1)
    source_reference: str = ""

    @model_validator(mode="after")
    def _target_must_be_concept_or_class(self) -> "RegimenComponentRecord":
        if self.target_kind not in {TargetKind.CONCEPT, TargetKind.CLASS}:
            raise ValueError("方案组分只能引用药品概念或药物类别")
        return self


class RegimenScheduleRecord(BaseModel):
    schedule_component_id: str
    regimen_revision_id: str
    component_id: str
    dose_value: float | None = None
    dose_unit: str = ""
    dose_basis: str = ""
    route: str = ""
    administration_days: str = ""
    cycle_length_days: int | None = None
    max_cycles: int | None = None
    treatment_phase: str = ""
    sequence_no: int | None = None
    publishing_enabled: bool = False
    inference_enabled: bool = False


def _json_text(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    return canonical_json_bytes(value).decode("utf-8")


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value))


def regimen_revision_typed_values(
    payload: Mapping[str, Any],
    *,
    lifecycle: str | None = None,
) -> dict[str, Any]:
    """生成 SQL materializer 与工作簿审核共用的方案 revision canonical 行。"""
    window_value = payload.get("effective_window")
    if isinstance(window_value, BaseModel):
        window: Mapping[str, Any] = window_value.model_dump(mode="json")
    elif isinstance(window_value, str):
        decoded = json.loads(window_value)
        if not isinstance(decoded, Mapping):
            raise ValueError("方案 effective_window 必须是 object")
        window = decoded
    elif isinstance(window_value, Mapping):
        window = window_value
    else:
        raise ValueError("方案 effective_window 缺失")

    values = {
        "regimen_revision_id": str(payload["regimen_revision_id"]),
        "logical_regimen_id": str(payload["logical_regimen_id"]),
        "canonical_name": str(payload["canonical_name"]),
        "lifecycle": str(lifecycle or _enum_text(payload.get("lifecycle") or "DRAFT")),
        "effective_from": str(window["effective_from"]),
        "effective_to": str(window["effective_to"]),
        "effective_date_basis": _enum_text(window["effective_date_basis"]),
        "date_override_reason": window.get("date_override_reason") or None,
        "date_review_comment": window.get("date_review_comment") or None,
        "historical_application_policy": _enum_text(
            window["historical_application_policy"]
        ),
        "source_refs_json": _json_text(payload.get("source_refs", [])),
        "supersedes_revision_id": payload.get("supersedes_revision_id") or None,
    }
    values["content_checksum"] = checksum(
        {key: value for key, value in values.items() if key != "lifecycle"}
    )
    return values


def build_regimen_authoring_records(root: Path) -> dict[str, list[BaseModel]]:
    asset = json.loads((root / "configs/oncology_regimen_kb.json").read_text(encoding="utf-8"))
    drug_classes = [
        DrugClassRecord.model_validate(item)
        for item in asset.get("drug_classes", [])
    ]
    concept_ids = {
        concept.drug_concept_id for concept in build_drug_crosswalk(root)[0]
    }
    declared_concept_ids = {
        str(item.get("concept_id") or "").strip()
        for item in asset.get("drug_concepts", [])
        if str(item.get("concept_id") or "").strip()
    }
    used_legacy_components = {
        str(component.get("drug_concept_id") or "").strip()
        for entry in asset.get("entries", [])
        for component in entry.get("components", [])
        if not component.get("target_kind") and not component.get("target_id")
    }
    missing_authority = sorted(
        item
        for item in used_legacy_components
        if item not in declared_concept_ids or item not in concept_ids
    )
    if missing_authority:
        raise ValueError(
            "方案旧组分缺少 drug_concepts authority: " + ",".join(missing_authority)
        )
    class_ids = {item.drug_class_id for item in drug_classes}
    revisions: list[RegimenRevisionRecord] = []
    aliases: list[RegimenAliasRecord] = []
    contexts: list[RegimenContextRecord] = []
    components: list[RegimenComponentRecord] = []
    schedules: list[RegimenScheduleRecord] = []
    for entry in asset.get("entries", []):
        logical_id = stable_id("regimen", entry["regimen_id"])
        payload = {
            "canonical_name": entry["canonical_name"],
            "aliases": entry.get("aliases", []),
            "contexts": entry.get("cancer_contexts", []),
            "components": entry.get("components", []),
        }
        rev_id = revision_id(logical_id, payload)
        metadata = entry.get("metadata", {})
        revisions.append(
            RegimenRevisionRecord(
                logical_regimen_id=logical_id,
                regimen_revision_id=rev_id,
                canonical_name=entry["canonical_name"],
                lifecycle=RevisionLifecycle.APPROVED,
                source_refs=metadata.get("source_refs", []),
            )
        )
        for alias in entry.get("aliases", []):
            aliases.append(
                RegimenAliasRecord(
                    alias_id=stable_id("alias", rev_id, normalize_regimen_alias(alias), alias),
                    regimen_revision_id=rev_id,
                    original_alias=alias,
                    normalized_alias=normalize_regimen_alias(alias),
                    review_status="approved",
                )
            )
        for context in entry.get("cancer_contexts", []):
            contexts.append(
                RegimenContextRecord(
                    context_id=stable_id("context", rev_id, context),
                    regimen_revision_id=rev_id,
                    cancer_context=context,
                )
            )
        for index, component in enumerate(entry.get("components", []), start=1):
            # 旧资产只有 drug_concept_id；新 release 保留 target_kind/target_id
            # 与 requirement，导出再回导不得把 CLASS 或可选性静默改写。
            explicit_target = component.get("target_kind") or component.get("target_id")
            if explicit_target:
                target_kind = TargetKind(
                    str(component.get("target_kind") or TargetKind.CONCEPT).upper()
                )
                target = str(
                    component.get("target_id")
                    or component.get("drug_concept_id")
                    or ""
                ).strip()
            else:
                legacy_id = str(component.get("drug_concept_id") or "").strip()
                target_kind = TargetKind.CONCEPT
                target = legacy_id
            if target_kind == TargetKind.CONCEPT and target not in concept_ids:
                raise ValueError(f"方案组分引用未知 drug_concept: {target}")
            if target_kind == TargetKind.CLASS and target not in class_ids:
                raise ValueError(f"方案组分引用未知 drug_class: {target}")
            requirement = CombinationRequirement(
                str(
                    component.get("requirement")
                    or CombinationRequirement.REQUIRED
                ).upper()
            )
            component_id = stable_id(
                "component", rev_id, index, target_kind.value, target, requirement.value
            )
            components.append(
                RegimenComponentRecord(
                    component_id=component_id,
                    regimen_revision_id=rev_id,
                    target_kind=target_kind,
                    target_id=target,
                    token=component.get("token", ""),
                    requirement=requirement,
                    sibling_order=index,
                )
            )
            schedules.append(
                RegimenScheduleRecord(
                    schedule_component_id=stable_id("schedule", component_id),
                    regimen_revision_id=rev_id,
                    component_id=component_id,
                )
            )

    mined = json.loads(
        (root / "docs/oncology/regimen_alias_candidates.json").read_text(encoding="utf-8")
    )
    corpus_checksum = str(mined.get("source_checksum") or "")
    for item in mined.get("candidates", []):
        normalized = str(item["normalized_alias"])
        aliases.append(
            RegimenAliasRecord(
                alias_id=stable_id("alias-candidate", normalized, corpus_checksum),
                original_alias=normalized,
                normalized_alias=normalize_regimen_alias(normalized),
                aggregate_frequency=int(item.get("frequency") or 0),
                source_corpus_checksum=corpus_checksum,
                review_status="needs_review",
            )
        )
    mark_alias_ambiguity(aliases, contexts)
    return {
        "drug_classes": drug_classes,
        "revisions": revisions,
        "aliases": aliases,
        "contexts": contexts,
        "components": components,
        "schedules": schedules,
    }


def mark_alias_ambiguity(
    aliases: list[RegimenAliasRecord],
    contexts: list[RegimenContextRecord],
) -> list[str]:
    """上下文无法消歧时保留 ambiguous，绝不自动选择方案。"""
    contexts_by_revision: dict[str, set[str]] = {}
    for item in contexts:
        contexts_by_revision.setdefault(item.regimen_revision_id, set()).add(item.cancer_context)
    by_alias: dict[str, list[RegimenAliasRecord]] = {}
    for alias in aliases:
        if alias.regimen_revision_id:
            by_alias.setdefault(alias.normalized_alias, []).append(alias)
    ambiguous: list[str] = []
    for normalized, items in by_alias.items():
        revisions = {item.regimen_revision_id for item in items}
        if len(revisions) < 2:
            continue
        context_sets = [contexts_by_revision.get(revision or "", set()) for revision in revisions]
        overlap = set.intersection(*context_sets) if context_sets and all(context_sets) else set()
        if overlap or any(not values for values in context_sets):
            ambiguous.append(normalized)
            for item in items:
                item.ambiguous = True
    return sorted(ambiguous)


def canonical_regimen_payload(records: dict[str, list[BaseModel]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, values in records.items():
        dumped = [
            item.model_dump(mode="json")
            for item in sorted(values, key=lambda row: next(iter(row.model_dump())))
        ]
        if key == "revisions":
            for item in dumped:
                item["content_checksum"] = regimen_revision_typed_values(item)[
                    "content_checksum"
                ]
        payload[key] = dumped
    payload["checksum"] = checksum(payload)
    return payload
