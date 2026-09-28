# -*- coding: utf-8 -*-
"""肿瘤方案知识资产与确定性 resolver."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .contracts import EvidenceAnchor
from .knowledge import (
    KnowledgeEntryMetadata,
    KnowledgeMetadata,
    ReviewStatus,
    asset_payload_checksum,
)


class AliasType(StrEnum):
    GENERIC = "generic"
    ENGLISH_GENERIC = "english_generic"
    TRADE = "trade"
    REGIMEN_TOKEN = "regimen_token"


class TypedDrugAlias(BaseModel):
    value: str = Field(min_length=1)
    alias_type: AliasType


class DrugConcept(BaseModel):
    concept_id: str = Field(min_length=1)
    generic_name: str = Field(min_length=1)
    aliases: list[TypedDrugAlias] = Field(default_factory=list)
    insurance_codes: list[str] = Field(default_factory=list)


class DrugClass(BaseModel):
    drug_class_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    match_terms: list[str] = Field(min_length=1)
    lifecycle: Literal["APPROVED", "RELEASED"]
    reviewer_id: str = Field(min_length=1)


class ComponentTargetKind(StrEnum):
    CONCEPT = "CONCEPT"
    CLASS = "CLASS"


class ComponentRequirement(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    WITH_OR_WITHOUT = "WITH_OR_WITHOUT"


class RegimenComponent(BaseModel):
    # drug_concept_id 保留为旧资产兼容字段；新资产以 target_kind/target_id
    # 表达具体药品或药物类别，不能把 CLASS 强行降格成某个药品概念。
    target_kind: ComponentTargetKind = ComponentTargetKind.CONCEPT
    target_id: str = Field(default="", min_length=1)
    token: str = Field(min_length=1)
    requirement: ComponentRequirement = ComponentRequirement.REQUIRED
    drug_concept_id: str = ""

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_component(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        upgraded = dict(value)
        legacy_id = str(upgraded.get("drug_concept_id") or "").strip()
        target_kind = str(upgraded.get("target_kind") or "CONCEPT").upper()
        upgraded["target_kind"] = target_kind
        upgraded.setdefault("requirement", "REQUIRED")
        if not str(upgraded.get("target_id") or "").strip() and legacy_id:
            upgraded["target_id"] = legacy_id
        if target_kind == ComponentTargetKind.CONCEPT and not legacy_id:
            upgraded["drug_concept_id"] = upgraded.get("target_id") or ""
        return upgraded

    @model_validator(mode="after")
    def _validate_target(self) -> "RegimenComponent":
        if not self.target_id.strip():
            raise ValueError("方案组分 target_id 不能为空")
        if self.target_kind == ComponentTargetKind.CONCEPT:
            if self.drug_concept_id and self.drug_concept_id != self.target_id:
                raise ValueError("CONCEPT 组分的 target_id 与 drug_concept_id 不一致")
            self.drug_concept_id = self.target_id
        elif self.drug_concept_id:
            raise ValueError("CLASS 组分不得伪装成 drug_concept_id")
        return self


class RegimenEntry(BaseModel):
    regimen_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    aliases: list[str] = Field(min_length=1)
    cancer_contexts: list[str] = Field(min_length=1)
    components: list[RegimenComponent] = Field(min_length=1)
    metadata: KnowledgeEntryMetadata


class RegimenKnowledgeAsset(BaseModel):
    asset_type: Literal["regimen"] = "regimen"
    metadata: KnowledgeMetadata
    drug_concepts: list[DrugConcept]
    drug_classes: list[DrugClass] = Field(default_factory=list)
    entries: list[RegimenEntry]

    @model_validator(mode="after")
    def _validate_component_concepts(self) -> "RegimenKnowledgeAsset":
        concept_ids = {item.concept_id for item in self.drug_concepts}
        class_ids = {item.drug_class_id for item in self.drug_classes}
        if len(class_ids) != len(self.drug_classes):
            raise ValueError("regimen drug_classes drug_class_id 重复")
        for entry in self.entries:
            for component in entry.components:
                if (
                    component.target_kind == ComponentTargetKind.CONCEPT
                    and component.target_id not in concept_ids
                ):
                    raise ValueError(
                        f"方案组分引用未知 drug_concept_id: {component.target_id}"
                    )
                if (
                    component.target_kind == ComponentTargetKind.CLASS
                    and component.target_id not in class_ids
                ):
                    raise ValueError(
                        f"方案组分引用未知或未批准 drug_class_id: {component.target_id}"
                    )
        return self


class TreatmentEventStatus(StrEnum):
    PLANNED = "PLANNED"
    ADMINISTERED = "ADMINISTERED"
    HISTORICAL = "HISTORICAL"
    UNKNOWN = "UNKNOWN"


class CancerContextStatus(StrEnum):
    MATCHED = "MATCHED"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class ResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNREVIEWED = "UNREVIEWED"
    CONTEXT_CONFLICT = "CONTEXT_CONFLICT"
    NOT_FOUND = "NOT_FOUND"


class ResolvedComponent(BaseModel):
    # 对 CLASS 目标保持 class-level 证据，drug_concept_id 为空，避免运行时
    # 任意挑选一个药品成员。旧消费者仍可读取 drug_concept_id。
    target_kind: ComponentTargetKind = ComponentTargetKind.CONCEPT
    target_id: str = ""
    requirement: ComponentRequirement = ComponentRequirement.REQUIRED
    drug_concept_id: str = ""
    generic_name: str = ""
    token: str
    evidence_type: Literal["explicit", "regimen_inference"]
    target_matched: bool = True
    matched_alias: str = ""
    insurance_codes: list[str] = Field(default_factory=list)
    corroborating_fee_codes: list[str] = Field(default_factory=list)
    anchor: EvidenceAnchor


class RegimenResolution(BaseModel):
    status: ResolutionStatus
    regimen_id: str = ""
    canonical_name: str = ""
    matched_text: str = ""
    components: list[ResolvedComponent] = Field(default_factory=list)
    event_status: TreatmentEventStatus = TreatmentEventStatus.UNKNOWN
    cycle_no: int | None = None
    line_of_therapy: int | None = None
    cancer_context_status: CancerContextStatus = CancerContextStatus.UNKNOWN
    cancer_context: str = ""
    encounter_date: date | None = None
    document_date: date | None = None
    treatment_date: date | None = None
    temporal_conflict: bool = False
    conflicts: list[str] = Field(default_factory=list)
    review_required: bool = False
    legacy_review_verdict: Literal["INCONCLUSIVE"] | None = None
    source_versions: list[str] = Field(default_factory=list)
    anchor: EvidenceAnchor | None = None


_HYPHENS = "‐‑‒–—―−－"
_CHINESE_NUMERALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def load_regimen_kb(path: Path) -> RegimenKnowledgeAsset:
    raw = json.loads(path.read_text(encoding="utf-8"))
    asset = RegimenKnowledgeAsset.model_validate(raw)
    expected = asset_payload_checksum(raw)
    if asset.metadata.checksum != expected:
        raise ValueError(
            f"oncology regimen checksum 不一致: "
            f"declared={asset.metadata.checksum}, expected={expected}"
        )
    return asset


def normalize_regimen_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").upper()
    text = re.sub(f"[{re.escape(_HYPHENS)}]", "-", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def _alias_pattern(alias: str) -> re.Pattern[str]:
    normalized = unicodedata.normalize("NFKC", alias)
    parts = [part for part in re.split(rf"[\s\-_/{re.escape(_HYPHENS)}]+", normalized) if part]
    separator = rf"[\s\-_/{re.escape(_HYPHENS)}]*"
    body = separator.join(re.escape(part) for part in parts)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.I)


def _contains_alias(text: str, alias: str) -> re.Match[str] | None:
    return _alias_pattern(alias).search(text)


def _number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value in _CHINESE_NUMERALS:
        return _CHINESE_NUMERALS[value]
    if len(value) == 2 and value.startswith("十") and value[1] in _CHINESE_NUMERALS:
        return 10 + _CHINESE_NUMERALS[value[1]]
    if len(value) == 2 and value.endswith("十") and value[0] in _CHINESE_NUMERALS:
        return _CHINESE_NUMERALS[value[0]] * 10
    return None


def _event_status(text: str) -> TreatmentEventStatus:
    if re.search(r"拟行|拟予|计划|准备|考虑采用|建议行", text):
        return TreatmentEventStatus.PLANNED
    if re.search(r"既往|曾行|曾接受|治疗史|历史上", text):
        return TreatmentEventStatus.HISTORICAL
    if re.search(r"完成|已行|已予|接受了|予以|方案化疗|输注", text):
        return TreatmentEventStatus.ADMINISTERED
    return TreatmentEventStatus.UNKNOWN


def _cycle_no(text: str) -> int | None:
    match = re.search(r"第([一二三四五六七八九十\d]+)(?:次|周期)", text)
    if match:
        return _number(match.group(1))
    match = re.search(r"(?<![A-Za-z0-9])C(\d+)(?!\d)", text, re.I)
    return int(match.group(1)) if match else None


def _line_of_therapy(text: str) -> int | None:
    match = re.search(r"第?([一二三四五六七八九十\d]+)线(?:治疗|方案|用药)?", text)
    return _number(match.group(1)) if match else None


def _explicit_matches(
    text: str,
    concepts: list[DrugConcept],
) -> dict[str, tuple[DrugConcept, str, int]]:
    matches: dict[str, tuple[DrugConcept, str, int]] = {}
    for concept in concepts:
        aliases = [
            concept.generic_name,
            *[
                alias.value
                for alias in concept.aliases
                if alias.alias_type != AliasType.REGIMEN_TOKEN
            ],
        ]
        for alias in sorted(set(aliases), key=len, reverse=True):
            match = _contains_alias(text, alias)
            if match:
                matches[concept.concept_id] = (concept, match.group(0), match.start())
                break
    return matches


def _negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 8) : start]
    return bool(
        re.search(r"不含|未用|未使用|不包括|排除", prefix)
        # class token 本身可能以“含”开头，例如“不含铂”匹配“含铂”时
        # start 前只剩“不”，仍必须识别为否定。
        or re.search(r"(?:不|未|无)\s*$", prefix)
    )


def _class_match(
    text: str,
    component: RegimenComponent,
    drug_class: DrugClass,
) -> re.Match[str] | None:
    """只匹配经审核保留的 class ID/token，不猜测类别成员。"""

    terms = sorted(
        {
            term.strip()
            for term in (
                component.token,
                component.target_id,
                drug_class.canonical_name,
                *drug_class.match_terms,
            )
            if term and term.strip()
        },
        key=len,
        reverse=True,
    )
    for term in terms:
        match = _contains_alias(text, term)
        if match is None and any(ord(char) > 127 for char in term):
            # 中文 class token 常直接跟在英文方案缩写之后，不应被 ASCII
            # 单词边界误挡；仍只匹配审核过的完整 token。
            match = re.search(re.escape(term), text, re.I)
        if match:
            return match
    return None


def _context_status(
    entry: RegimenEntry,
    cancer_context: str | None,
) -> CancerContextStatus:
    if not cancer_context:
        return CancerContextStatus.UNKNOWN
    if any(
        context in cancer_context or cancer_context in context
        for context in entry.cancer_contexts
    ):
        return CancerContextStatus.MATCHED
    return CancerContextStatus.CONFLICT


def _source_versions(entry: RegimenEntry) -> list[str]:
    return sorted(
        {
            f"{source.source_id}@"
            f"{source.version or source.effective_date or source.publication_date}"
            for source in entry.metadata.source_refs
        }
    )


def resolve_regimen(
    *,
    text: str,
    asset: RegimenKnowledgeAsset,
    cancer_context: str | None = None,
    fee_codes: list[str] | None = None,
    source: str = "note",
    locator: str = "",
    anchor: dict[str, Any] | None = None,
    encounter_date: date | None = None,
    document_date: date | None = None,
    treatment_date: date | None = None,
) -> RegimenResolution:
    """最长 approved alias + 上下文 + 显式药名优先的单事件解析."""
    evidence_anchor = EvidenceAnchor(
        source=source,
        locator=locator,
        text=text,
        anchor=anchor,
    )
    alias_hits: list[tuple[int, int, str, RegimenEntry, re.Match[str]]] = []
    for entry in asset.entries:
        for alias in entry.aliases:
            match = _contains_alias(text, alias)
            if match:
                alias_hits.append(
                    (
                        len(normalize_regimen_alias(alias)),
                        -match.start(),
                        alias,
                        entry,
                        match,
                    )
                )
    if not alias_hits:
        return RegimenResolution(
            status=ResolutionStatus.NOT_FOUND,
            event_status=_event_status(text),
            cycle_no=_cycle_no(text),
            line_of_therapy=_line_of_therapy(text),
            cancer_context=cancer_context or "",
            anchor=evidence_anchor,
        )

    max_length = max(item[0] for item in alias_hits)
    longest = [item for item in alias_hits if item[0] == max_length]
    approved = [
        item
        for item in longest
        if item[3].metadata.review_status == ReviewStatus.APPROVED
    ]
    if not approved:
        return RegimenResolution(
            status=ResolutionStatus.UNREVIEWED,
            matched_text=longest[0][4].group(0),
            event_status=_event_status(text),
            cycle_no=_cycle_no(text),
            line_of_therapy=_line_of_therapy(text),
            cancer_context=cancer_context or "",
            review_required=True,
            legacy_review_verdict="INCONCLUSIVE",
            anchor=evidence_anchor,
        )

    compatible = [
        item
        for item in approved
        if _context_status(item[3], cancer_context) != CancerContextStatus.CONFLICT
    ]
    if cancer_context and not compatible:
        return RegimenResolution(
            status=ResolutionStatus.CONTEXT_CONFLICT,
            matched_text=approved[0][4].group(0),
            event_status=_event_status(text),
            cycle_no=_cycle_no(text),
            line_of_therapy=_line_of_therapy(text),
            cancer_context_status=CancerContextStatus.CONFLICT,
            cancer_context=cancer_context,
            review_required=True,
            legacy_review_verdict="INCONCLUSIVE",
            conflicts=["CANCER_CONTEXT_CONFLICT"],
            anchor=evidence_anchor,
        )
    distinct = {item[3].regimen_id for item in compatible}
    if len(distinct) != 1:
        return RegimenResolution(
            status=ResolutionStatus.AMBIGUOUS,
            matched_text=approved[0][4].group(0),
            event_status=_event_status(text),
            cycle_no=_cycle_no(text),
            line_of_therapy=_line_of_therapy(text),
            cancer_context=cancer_context or "",
            review_required=True,
            legacy_review_verdict="INCONCLUSIVE",
            conflicts=["AMBIGUOUS_REGIMEN_ALIAS"],
            anchor=evidence_anchor,
        )
    selected = next(item for item in compatible if item[3].regimen_id in distinct)
    entry = selected[3]
    context_status = _context_status(entry, cancer_context)
    explicit = _explicit_matches(text, asset.drug_concepts)
    concept_index = {concept.concept_id: concept for concept in asset.drug_concepts}
    class_index = {
        drug_class.drug_class_id: drug_class for drug_class in asset.drug_classes
    }
    fee_code_set = set(fee_codes or [])
    conflicts: list[str] = []
    components: list[ResolvedComponent] = []
    composition_declared = bool(re.search(r"方案(?:组分|药物|包括)\s*[:：]", text))
    for component in entry.components:
        requirement = component.requirement
        if component.target_kind == ComponentTargetKind.CONCEPT:
            concept = concept_index[component.target_id]
            explicit_match = explicit.get(component.target_id)
            explicit_negated = bool(
                explicit_match and _negated(text, explicit_match[2])
            )
            if explicit_negated:
                if requirement == ComponentRequirement.REQUIRED:
                    conflicts.append(
                        f"EXPLICIT_COMPONENT_NEGATED:{component.target_id}"
                    )
                else:
                    # 可选/联合或不联合成分被明确排除时，不把它投影为已用药。
                    continue
            if explicit_match and not explicit_negated:
                matched_alias = explicit_match[1]
                evidence_type: Literal["explicit", "regimen_inference"] = "explicit"
                target_matched = True
            elif requirement != ComponentRequirement.REQUIRED:
                # 方案名本身不能证明可选药实际使用。
                continue
            else:
                matched_alias = selected[4].group(0)
                evidence_type = "regimen_inference"
                target_matched = not explicit_negated
                if composition_declared:
                    conflicts.append(
                        f"REQUIRED_COMPONENT_MISSING:{component.target_id}"
                    )
            corroborating = sorted(fee_code_set & set(concept.insurance_codes))
            components.append(
                ResolvedComponent(
                    target_kind=component.target_kind,
                    target_id=component.target_id,
                    requirement=requirement,
                    drug_concept_id=concept.concept_id,
                    generic_name=concept.generic_name,
                    token=component.token,
                    evidence_type=evidence_type,
                    target_matched=target_matched,
                    matched_alias=matched_alias,
                    insurance_codes=concept.insurance_codes,
                    corroborating_fee_codes=corroborating,
                    anchor=evidence_anchor,
                )
            )
            continue

        drug_class = class_index.get(component.target_id)
        if drug_class is None:
            conflicts.append(f"CLASS_AUTHORITY_MISSING:{component.target_id}")
            class_match = None
        else:
            class_match = _class_match(text, component, drug_class)
        class_negated = bool(class_match and _negated(text, class_match.start()))
        if class_negated and requirement != ComponentRequirement.REQUIRED:
            continue
        if class_negated:
            conflicts.append(f"EXPLICIT_COMPONENT_NEGATED:{component.target_id}")
        if not class_match and requirement != ComponentRequirement.REQUIRED:
            continue
        target_matched = bool(class_match and not class_negated)
        if not target_matched:
            # 缺少 class member 字典时保留类别目标，并要求人工复核；绝不任选药品。
            conflicts.append(
                f"REQUIRED_CLASS_COMPONENT_UNRESOLVED:{component.target_id}"
            )
        components.append(
            ResolvedComponent(
                target_kind=component.target_kind,
                target_id=component.target_id,
                requirement=requirement,
                drug_concept_id="",
                generic_name=component.target_id,
                token=component.token,
                evidence_type="explicit" if target_matched else "regimen_inference",
                target_matched=target_matched,
                matched_alias=(
                    class_match.group(0) if target_matched else selected[4].group(0)
                ),
                anchor=evidence_anchor,
            )
        )

    mapped_ids = {
        component.target_id
        for component in entry.components
        if component.target_kind == ComponentTargetKind.CONCEPT
    }
    explicit_extra = sorted(set(explicit) - mapped_ids)
    if composition_declared and explicit_extra:
        if any(
            component.target_kind == ComponentTargetKind.CLASS
            for component in entry.components
        ):
            conflicts.extend(
                f"CLASS_MEMBERSHIP_UNVERIFIED:{concept_id}"
                for concept_id in explicit_extra
            )
        else:
            conflicts.extend(
                f"EXPLICIT_COMPONENT_CONFLICT:{concept_id}"
                for concept_id in explicit_extra
            )

    dates = [value for value in (encounter_date, document_date, treatment_date) if value]
    temporal_conflict = len({value.year for value in dates}) > 1
    if temporal_conflict:
        conflicts.append("TEMPORAL_CONFLICT")
    if context_status == CancerContextStatus.CONFLICT:
        conflicts.append("CANCER_CONTEXT_CONFLICT")

    review_required = bool(conflicts) or context_status == CancerContextStatus.CONFLICT
    status = (
        ResolutionStatus.CONTEXT_CONFLICT
        if context_status == CancerContextStatus.CONFLICT
        else ResolutionStatus.RESOLVED
    )
    return RegimenResolution(
        status=status,
        regimen_id=entry.regimen_id,
        canonical_name=entry.canonical_name,
        matched_text=selected[4].group(0),
        components=components,
        event_status=_event_status(text),
        cycle_no=_cycle_no(text),
        line_of_therapy=_line_of_therapy(text),
        cancer_context_status=context_status,
        cancer_context=cancer_context or "",
        encounter_date=encounter_date,
        document_date=document_date,
        treatment_date=treatment_date,
        temporal_conflict=temporal_conflict,
        conflicts=sorted(set(conflicts)),
        review_required=review_required,
        legacy_review_verdict="INCONCLUSIVE" if review_required else None,
        source_versions=_source_versions(entry),
        anchor=evidence_anchor,
    )
