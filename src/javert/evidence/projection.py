# -*- coding: utf-8 -*-
"""从 accepted stream 确定性重建 Diagnosis 只读 projection。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from pydantic import Field

from .ledger import LedgerEvent
from .models import (
    AssertionValue, CandidateAssertion, FrozenStrictModel,
)
from .serialization import checksum_value

LiteralState = Literal["TRUE", "FALSE", "UNKNOWN", "CONFLICT"]


class DiagnosisProjectionItem(FrozenStrictModel):
    semantic_key: str
    subject_entity_id: str
    predicate: str
    concept_system: str
    concept_code: str
    concept_version: str
    displays: tuple[str, ...] = ()
    state: LiteralState
    assertion_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    locators: tuple[dict[str, Any], ...]
    coverage_reasons: tuple[str, ...] = ()
class DiagnosisProjection(FrozenStrictModel):
    items: tuple[DiagnosisProjectionItem, ...]
    source_event_count: int = Field(ge=0)
    canonical_checksum: str


def rebuild_diagnosis_projection(events: list[LedgerEvent]) -> DiagnosisProjection:
    grouped: dict[tuple[str, str, str, str, str, str], list[CandidateAssertion]] = defaultdict(list)
    for event in events:
        if event.stream != "accepted":
            continue
        candidate = CandidateAssertion.model_validate(event.payload)
        assertion = next(item for item in candidate.bundle.assertions if item.assertion_id == candidate.assertion_id)
        fact = next(item for item in candidate.bundle.facts if item.fact_id == assertion.fact_id)
        object_entity = next(item for item in candidate.bundle.entities if item.entity_id == fact.object.entity_id)
        if object_entity.concept is None:
            continue
        time_key = checksum_value(assertion.clinical_time)
        key = (
            fact.subject_entity_id, fact.predicate,
            object_entity.concept.system, object_entity.concept.code,
            object_entity.concept.version, time_key,
        )
        grouped[key].append(candidate)
    items: list[DiagnosisProjectionItem] = []
    for key, candidates in sorted(grouped.items()):
        assertions = []
        evidence_ids: set[str] = set()
        locators: dict[str, dict[str, Any]] = {}
        displays: set[str] = set()
        coverage_reasons: set[str] = set()
        values: set[AssertionValue] = set()
        for candidate in candidates:
            bundle = candidate.bundle
            assertion = next(item for item in bundle.assertions if item.assertion_id == candidate.assertion_id)
            fact = next(item for item in bundle.facts if item.fact_id == assertion.fact_id)
            object_entity = next(item for item in bundle.entities if item.entity_id == fact.object.entity_id)
            assertions.append(assertion.assertion_id)
            values.add(assertion.value)
            if object_entity.concept:
                displays.add(object_entity.concept.display)
            if assertion.coverage:
                coverage_reasons.add(assertion.coverage.reason)
            for link in assertion.evidence_links:
                evidence = next(item for item in bundle.evidence_items if item.evidence_id == link.evidence_id)
                evidence_ids.add(evidence.evidence_id)
                locators[evidence.evidence_id] = evidence.locator.model_dump(mode="json")
        state: LiteralState
        if AssertionValue.TRUE in values and AssertionValue.FALSE in values:
            state = "CONFLICT"
        elif AssertionValue.TRUE in values:
            state = "TRUE"
        elif AssertionValue.FALSE in values:
            state = "FALSE"
        else:
            state = "UNKNOWN"
        items.append(DiagnosisProjectionItem(
            semantic_key=checksum_value(key), subject_entity_id=key[0], predicate=key[1],
            concept_system=key[2], concept_code=key[3], concept_version=key[4],
            displays=tuple(sorted(displays)), state=state,
            assertion_ids=tuple(sorted(assertions)), evidence_ids=tuple(sorted(evidence_ids)),
            locators=tuple(locators[item] for item in sorted(locators)),
            coverage_reasons=tuple(sorted(coverage_reasons)),
        ))
    payload = [item.model_dump(mode="json") for item in items]
    return DiagnosisProjection(
        items=tuple(items), source_event_count=sum(event.stream == "accepted" for event in events),
        canonical_checksum=checksum_value(payload),
    )
