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

from pydantic import BaseModel, Field

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


class RegimenComponent(BaseModel):
    drug_concept_id: str = Field(min_length=1)
    token: str = Field(min_length=1)


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
    entries: list[RegimenEntry]


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
    drug_concept_id: str
    generic_name: str
    token: str
    evidence_type: Literal["explicit", "regimen_inference"]
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
    return bool(re.search(r"不含|未用|未使用|不包括|排除", prefix))


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
    fee_code_set = set(fee_codes or [])
    conflicts: list[str] = []
    components: list[ResolvedComponent] = []
    for component in entry.components:
        concept = concept_index[component.drug_concept_id]
        explicit_match = explicit.get(component.drug_concept_id)
        if explicit_match:
            matched_alias = explicit_match[1]
            evidence_type: Literal["explicit", "regimen_inference"] = "explicit"
            if _negated(text, explicit_match[2]):
                conflicts.append(
                    f"EXPLICIT_COMPONENT_NEGATED:{component.drug_concept_id}"
                )
        else:
            matched_alias = selected[4].group(0)
            evidence_type = "regimen_inference"
        corroborating = sorted(fee_code_set & set(concept.insurance_codes))
        components.append(
            ResolvedComponent(
                drug_concept_id=concept.concept_id,
                generic_name=concept.generic_name,
                token=component.token,
                evidence_type=evidence_type,
                matched_alias=matched_alias,
                insurance_codes=concept.insurance_codes,
                corroborating_fee_codes=corroborating,
                anchor=evidence_anchor,
            )
        )

    mapped_ids = {component.drug_concept_id for component in entry.components}
    explicit_extra = sorted(set(explicit) - mapped_ids)
    if re.search(r"方案(?:组分|药物|包括)\s*[:：]", text) and explicit_extra:
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
