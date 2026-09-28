# -*- coding: utf-8 -*-
"""病理标志物的别名、方法、阈值、时序与冲突归一."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .contracts import (
    CriterionAssessment,
    CriterionState,
    EvidenceAnchor,
    NormalizedFact,
)
from .knowledge import (
    KnowledgeEntryMetadata,
    KnowledgeMetadata,
    ReviewStatus,
    asset_payload_checksum,
)


class BiomarkerMethod(StrEnum):
    IHC = "IHC"
    ISH = "ISH"
    FISH = "FISH"
    MOLECULAR = "MOLECULAR"


class MarkerAlias(BaseModel):
    value: str = Field(min_length=1)
    alias_type: Literal["protein", "gene", "display", "abbreviation"]


class BiomarkerRule(BaseModel):
    entry_id: str = Field(min_length=1)
    marker_id: str = Field(min_length=1)
    aliases: list[MarkerAlias] = Field(min_length=1)
    observation_type: Literal[
        "protein_expression",
        "gene_amplification",
        "sequence_variant",
        "fusion",
        "composite_signature",
    ]
    methods: list[BiomarkerMethod] = Field(min_length=1)
    cancer_contexts: list[str] = Field(min_length=1)
    policy_context: str = Field(min_length=1)
    specimen_constraints: list[str] = Field(default_factory=list)
    scoring_system: str = ""
    accepted_values: list[str] = Field(default_factory=list)
    threshold: dict[str, Any] = Field(default_factory=dict)
    raw_condition: str = ""
    metadata: KnowledgeEntryMetadata


class PathologyKnowledgeAsset(BaseModel):
    asset_type: Literal["pathology"] = "pathology"
    metadata: KnowledgeMetadata
    entries: list[BiomarkerRule]


class PathologyInput(BaseModel):
    text: str
    source: str = "pathology"
    locator: str = ""
    anchor: dict[str, Any] | None = None
    specimen_site: str = ""
    specimen_date: date | None = None
    report_date: date | None = None


class NormalizedBiomarkerObservation(BaseModel):
    marker_id: str
    matched_alias: str
    observed_text: str
    observation_type: str
    method: BiomarkerMethod | None = None
    score: str = ""
    qualitative_result: str = ""
    molecular_result: str = ""
    cancer_context: str = ""
    specimen_site: str = ""
    specimen_date: date | None = None
    report_date: date | None = None
    anchor: EvidenceAnchor
    normalizer_version: str = "1.0.0"
    uncertainty_reason: str = ""


_HER2_ALIAS_RE = re.compile(
    r"(?i)(HER\s*[-]?\s*2\s*/\s*neu|c\s*[-]?\s*erbB\s*[-]?\s*2|"
    r"CerbB2|HER\s*[-]?\s*2|ERBB2)"
)
_SCORE_RE = re.compile(r"[\s(（:：]*(0|[123]\+)[\s)）]*")


def load_pathology_kb(path: Path) -> PathologyKnowledgeAsset:
    raw = json.loads(path.read_text(encoding="utf-8"))
    asset = PathologyKnowledgeAsset.model_validate(raw)
    expected = asset_payload_checksum(raw)
    if asset.metadata.checksum != expected:
        raise ValueError(
            f"pathology biomarker checksum 不一致: "
            f"declared={asset.metadata.checksum}, expected={expected}"
        )
    return asset


def _normalize_method(text: str, alias: str, score: str) -> tuple[BiomarkerMethod | None, str]:
    normalized = unicodedata.normalize("NFKC", text).upper()
    if "FISH" in normalized:
        return BiomarkerMethod.FISH, "gene_amplification"
    if re.search(r"\bISH\b", normalized):
        return BiomarkerMethod.ISH, "gene_amplification"
    if re.search(r"NGS|测序|突变|变异|AMPLIFICATION|扩增", normalized):
        return BiomarkerMethod.MOLECULAR, (
            "gene_amplification" if re.search(r"AMPLIFICATION|扩增", normalized) else "sequence_variant"
        )
    if "IHC" in normalized or "免疫组化" in text or score:
        return BiomarkerMethod.IHC, "protein_expression"
    if alias.upper() == "ERBB2":
        return BiomarkerMethod.MOLECULAR, "sequence_variant"
    return None, "protein_expression"


def normalize_pathology_observation(
    item: PathologyInput,
    *,
    cancer_context: str,
) -> list[NormalizedBiomarkerObservation]:
    """抽取 HER2 生物层级；不把分子结果强转为 IHC."""
    observations: list[NormalizedBiomarkerObservation] = []
    for match in _HER2_ALIAS_RE.finditer(item.text):
        alias = match.group(0)
        tail = item.text[match.end() : match.end() + 20]
        score_match = _SCORE_RE.match(tail)
        score = score_match.group(1) if score_match else ""
        method, observation_type = _normalize_method(item.text, alias, score)
        qualitative = ""
        if re.search(r"阳性|POSITIVE", tail, re.I):
            qualitative = "positive"
        elif re.search(r"阴性|NEGATIVE", tail, re.I):
            qualitative = "negative"
        uncertainty = ""
        if method is None:
            uncertainty = "检测方法或评分未明确"
        molecular = ""
        if method in {
            BiomarkerMethod.MOLECULAR,
            BiomarkerMethod.FISH,
            BiomarkerMethod.ISH,
        }:
            molecular = tail.strip(" ：:()（）,，。")[:80]
            score = ""
        observed_end = match.end() + (score_match.end() if score_match else 0)
        observations.append(
            NormalizedBiomarkerObservation(
                marker_id="HER2",
                matched_alias=alias,
                observed_text=item.text[match.start() : observed_end],
                observation_type=observation_type,
                method=method,
                score=score,
                qualitative_result=qualitative,
                molecular_result=molecular,
                cancer_context=cancer_context,
                specimen_site=item.specimen_site,
                specimen_date=item.specimen_date,
                report_date=item.report_date,
                anchor=EvidenceAnchor(
                    source=item.source,
                    locator=item.locator,
                    text=item.text,
                    anchor=item.anchor,
                ),
                uncertainty_reason=uncertainty,
            )
        )
    return observations


def _effective(metadata: KnowledgeEntryMetadata, service_date: date) -> bool:
    return metadata.effective_from <= service_date and (
        metadata.effective_to is None or service_date <= metadata.effective_to
    )


def _select_threshold(
    asset: PathologyKnowledgeAsset,
    *,
    marker_id: str,
    cancer_context: str,
    policy_context: str,
    method: BiomarkerMethod,
    service_date: date,
    enforce_effective_date: bool = True,
) -> BiomarkerRule | None:
    matches = [
        entry
        for entry in asset.entries
        if entry.marker_id == marker_id
        and cancer_context in entry.cancer_contexts
        and entry.policy_context == policy_context
        and method in entry.methods
        and entry.metadata.review_status == ReviewStatus.APPROVED
        and (not enforce_effective_date or _effective(entry.metadata, service_date))
    ]
    return matches[0] if len(matches) == 1 else None


def evaluate_biomarker_criterion(
    *,
    criterion_id: str,
    marker_id: str,
    cancer_context: str,
    policy_context: str,
    expected_method: BiomarkerMethod,
    inputs: list[PathologyInput],
    service_date: date,
    asset: PathologyKnowledgeAsset,
    enforce_effective_date: bool = True,
) -> CriterionAssessment:
    """按完整上下文选择阈值并保留时序冲突和非适用方法."""
    observations = [
        observation
        for item in inputs
        for observation in normalize_pathology_observation(
            item,
            cancer_context=cancer_context,
        )
        if observation.marker_id == marker_id
    ]
    threshold = _select_threshold(
        asset,
        marker_id=marker_id,
        cancer_context=cancer_context,
        policy_context=policy_context,
        method=expected_method,
        service_date=service_date,
        enforce_effective_date=enforce_effective_date,
    )
    expected = {
        "marker_id": marker_id,
        "method": expected_method.value,
        "policy_context": policy_context,
        "accepted_values": threshold.accepted_values if threshold else [],
        "threshold_id": threshold.entry_id if threshold else "",
    }
    facts = [
        NormalizedFact(
            fact_type="biomarker",
            value=observation.observed_text,
            normalized_value={
                "marker_id": observation.marker_id,
                "method": observation.method.value if observation.method else "",
                "score": observation.score,
                "observation_type": observation.observation_type,
            },
            source=observation.anchor.source,
            anchor=observation.anchor,
            observation_date=observation.specimen_date or observation.report_date,
            service_date=service_date,
            method=observation.method.value if observation.method else "",
            context={
                "cancer_context": cancer_context,
                "policy_context": policy_context,
                "specimen_site": observation.specimen_site,
            },
            normalizer_version=observation.normalizer_version,
            uncertainty_reason=observation.uncertainty_reason,
        )
        for observation in observations
    ]
    anchors = [observation.anchor for observation in observations]
    if threshold is None:
        return CriterionAssessment(
            criterion_id=criterion_id,
            criterion_type="biomarker",
            state=CriterionState.UNKNOWN,
            expected_condition=expected,
            normalized_facts=facts,
            evidence_anchors=anchors,
            reason="没有完整上下文匹配的已审核阈值",
            missing_items=["approved contextual threshold"],
            normalizer_version="1.0.0",
        )

    applicable: list[tuple[NormalizedBiomarkerObservation, CriterionState]] = []
    post_service = False
    for observation in observations:
        observed_date = observation.specimen_date or observation.report_date
        if observed_date and observed_date > service_date:
            post_service = True
            continue
        if observation.method != expected_method or not observation.score:
            continue
        state = (
            CriterionState.SATISFIED
            if observation.score in threshold.accepted_values
            else CriterionState.NOT_SATISFIED
        )
        applicable.append((observation, state))

    states = {state for _, state in applicable}
    if CriterionState.SATISFIED in states and CriterionState.NOT_SATISFIED in states:
        state = CriterionState.CONFLICT
        reason = "多个服务前标本产生相互冲突的阈值结果"
    elif len(states) == 1:
        state = next(iter(states))
        reason = (
            f"病理免疫组化 {applicable[0][0].score} "
            f"{'符合' if state == CriterionState.SATISFIED else '不符合'}"
            f"医保限定阈值 {'/'.join(threshold.accepted_values)}"
        )
    else:
        state = CriterionState.UNKNOWN
        reason = (
            "仅发现服务后病理结果，不能追溯证明用药时资格"
            if post_service
            else "未找到方法、评分、癌种和时序均适用的病理结果"
        )

    flags = []
    if post_service:
        flags.append("post-service pathology")
    return CriterionAssessment(
        criterion_id=criterion_id,
        criterion_type="biomarker",
        state=state,
        expected_condition=expected,
        normalized_facts=facts,
        evidence_anchors=anchors,
        reason=reason,
        missing_items=flags,
        evaluator_version="1.0.0",
        normalizer_version="1.0.0",
        source_version=threshold.metadata.content_version,
    )
