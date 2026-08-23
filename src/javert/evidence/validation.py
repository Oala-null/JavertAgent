# -*- coding: utf-8 -*-
"""Evidence Bundle 的原子、确定性验证。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .models import (
    AssertionOrigin, AssertionValue, CandidateAssertion, DocumentLocator, EvidenceBundle,
    OntologyPack, StructuredLocator, ValidationIssue, ValidationResult,
)
from .ontology import is_subtype, validate_ontology_pack
from .serialization import checksum_value, row_fingerprint

SourceMaterial = Mapping[str, Sequence[Mapping[str, Any]]]


def _add(
    issues: list[ValidationIssue], code: str, *, record_id: str = "", path: str = "",
) -> None:
    issues.append(ValidationIssue(code=code, record_id=record_id, path=path))


def _record_sets(bundle: EvidenceBundle) -> dict[str, set[str]]:
    return {
        "ontology": {item.ontology_ref_id for item in bundle.ontology_refs},
        "source": {item.source_artifact_id for item in bundle.sources},
        "evidence": {item.evidence_id for item in bundle.evidence_items},
        "entity": {item.entity_id for item in bundle.entities},
        "fact": {item.fact_id for item in bundle.facts},
        "activity": {item.activity_id for item in bundle.activities},
        "inference": {item.inference_id for item in bundle.inferences},
        "assertion": {item.assertion_id for item in bundle.assertions},
        "conflict": {item.conflict_id for item in bundle.conflicts},
        "review": {item.review_action_id for item in bundle.review_actions},
    }


def _all_ids(bundle: EvidenceBundle) -> list[str]:
    return [
        *[item.ontology_ref_id for item in bundle.ontology_refs],
        *[item.source_artifact_id for item in bundle.sources],
        *[item.evidence_id for item in bundle.evidence_items],
        *[item.entity_id for item in bundle.entities],
        *[item.fact_id for item in bundle.facts],
        *[item.activity_id for item in bundle.activities],
        *[item.inference_id for item in bundle.inferences],
        *[item.assertion_id for item in bundle.assertions],
        *[item.conflict_id for item in bundle.conflicts],
        *[item.review_action_id for item in bundle.review_actions],
    ]


def _time_overlaps(left, right) -> bool:
    if left.kind.value == "UNKNOWN" or right.kind.value == "UNKNOWN":
        return True
    left_start = left.start
    right_start = right.start
    left_end = left.end or left.start
    right_end = right.end or right.start
    return bool(left_start and right_start and left_end >= right_start and right_end >= left_start)


def _validate_lineage(
    bundle: EvidenceBundle, issues: list[ValidationIssue], source_material: SourceMaterial | None,
) -> None:
    source_ids = {item.source_artifact_id for item in bundle.sources}
    source_by_id = {item.source_artifact_id: item for item in bundle.sources}
    if source_material:
        for source_id, rows in source_material.items():
            source = source_by_id.get(source_id)
            if source is not None and checksum_value(list(rows)) != source.content_checksum:
                _add(issues, "SOURCE_ARTIFACT_CHECKSUM_MISMATCH", record_id=source_id)
    for evidence in bundle.evidence_items:
        if evidence.source_artifact_id not in source_ids:
            _add(issues, "EVIDENCE_SOURCE_MISSING", record_id=evidence.evidence_id, path="source_artifact_id")
            continue
        rows = source_material.get(evidence.source_artifact_id) if source_material else None
        if rows is None:
            continue
        locator = evidence.locator
        if locator.row_ordinal >= len(rows):
            _add(issues, "LOCATOR_ROW_OUT_OF_BOUNDS", record_id=evidence.evidence_id, path="locator.row_ordinal")
            continue
        row = rows[locator.row_ordinal]
        if row_fingerprint(row) != locator.row_fingerprint:
            _add(issues, "LOCATOR_ROW_FINGERPRINT_MISMATCH", record_id=evidence.evidence_id, path="locator.row_fingerprint")
        if locator.field not in row:
            _add(issues, "LOCATOR_FIELD_MISSING", record_id=evidence.evidence_id, path="locator.field")
            continue
        value = row[locator.field]
        if isinstance(locator, StructuredLocator):
            if checksum_value(value) != locator.value_checksum:
                _add(issues, "LOCATOR_VALUE_CHECKSUM_MISMATCH", record_id=evidence.evidence_id, path="locator.value_checksum")
        elif isinstance(locator, DocumentLocator) and locator.start_char is not None:
            text = str(value)
            if locator.end_char is None or locator.end_char > len(text):
                _add(issues, "LOCATOR_SPAN_OUT_OF_BOUNDS", record_id=evidence.evidence_id, path="locator.end_char")
            else:
                span = text[locator.start_char:locator.end_char]
                if checksum_value(span) != locator.span_checksum:
                    _add(issues, "LOCATOR_SPAN_CHECKSUM_MISMATCH", record_id=evidence.evidence_id, path="locator.span_checksum")


def _validate_ontology(bundle: EvidenceBundle, pack: OntologyPack, issues: list[ValidationIssue]) -> None:
    issues.extend(validate_ontology_pack(pack))
    matching_refs = [
        ref for ref in bundle.ontology_refs
        if ref.ontology_id == pack.ontology_id and ref.version == pack.version
    ]
    if not matching_refs:
        _add(issues, "ONTOLOGY_REF_MISSING")
    for ref in matching_refs:
        if ref.schema_version != pack.schema_version or ref.content_checksum != pack.content_checksum:
            _add(issues, "ONTOLOGY_REF_MISMATCH", record_id=ref.ontology_ref_id)

    type_ids = {item.type_id for item in pack.entity_types}
    concept_defs = {item.concept.identity: item for item in pack.concepts}
    entity_by_id = {item.entity_id: item for item in bundle.entities}
    for entity in bundle.entities:
        if entity.entity_type not in type_ids:
            _add(issues, "ENTITY_TYPE_UNKNOWN", record_id=entity.entity_id, path="entity_type")
        if entity.concept is not None:
            definition = concept_defs.get(entity.concept.identity)
            if definition is None:
                _add(issues, "CONCEPT_UNKNOWN", record_id=entity.entity_id, path="concept")
            elif not is_subtype(pack, definition.entity_type, entity.entity_type):
                _add(issues, "CONCEPT_ENTITY_TYPE_MISMATCH", record_id=entity.entity_id, path="concept")

    predicates = {item.predicate_id: item for item in pack.predicates}
    for fact in bundle.facts:
        predicate = predicates.get(fact.predicate)
        if predicate is None:
            _add(issues, "PREDICATE_UNKNOWN", record_id=fact.fact_id, path="predicate")
            continue
        subject = entity_by_id.get(fact.subject_entity_id)
        if subject is not None and not any(is_subtype(pack, subject.entity_type, allowed) for allowed in predicate.domain_types):
            _add(issues, "PREDICATE_DOMAIN_MISMATCH", record_id=fact.fact_id, path="subject_entity_id")
        if fact.object.entity_id:
            obj = entity_by_id.get(fact.object.entity_id)
            if obj is not None and not any(is_subtype(pack, obj.entity_type, allowed) for allowed in predicate.range_types):
                _add(issues, "PREDICATE_RANGE_MISMATCH", record_id=fact.fact_id, path="object.entity_id")
        elif fact.object.literal_type not in predicate.literal_types:
            _add(issues, "PREDICATE_LITERAL_MISMATCH", record_id=fact.fact_id, path="object.literal_type")


def validate_bundle(
    bundle: EvidenceBundle, pack: OntologyPack, *, source_material: SourceMaterial | None = None,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    sets = _record_sets(bundle)
    all_ids = _all_ids(bundle)
    if len(all_ids) != len(set(all_ids)):
        _add(issues, "DUPLICATE_RECORD_ID")

    entity_ids = sets["entity"]
    for entity in bundle.entities:
        if entity.ontology_ref_id not in sets["ontology"]:
            _add(issues, "ENTITY_ONTOLOGY_MISSING", record_id=entity.entity_id, path="ontology_ref_id")
    for fact in bundle.facts:
        if fact.subject_entity_id not in entity_ids:
            _add(issues, "FACT_SUBJECT_MISSING", record_id=fact.fact_id, path="subject_entity_id")
        if fact.object.entity_id and fact.object.entity_id not in entity_ids:
            _add(issues, "FACT_OBJECT_MISSING", record_id=fact.fact_id, path="object.entity_id")
    proposition_keys = [
        (fact.subject_entity_id, fact.predicate, checksum_value(fact.object)) for fact in bundle.facts
    ]
    if len(proposition_keys) != len(set(proposition_keys)):
        _add(issues, "DUPLICATE_FACT_PROPOSITION")

    assertions_by_fact: dict[str, list] = {fact_id: [] for fact_id in sets["fact"]}
    assertion_by_id = {item.assertion_id: item for item in bundle.assertions}
    for assertion in bundle.assertions:
        if assertion.fact_id not in sets["fact"]:
            _add(issues, "ASSERTION_FACT_MISSING", record_id=assertion.assertion_id, path="fact_id")
        else:
            assertions_by_fact[assertion.fact_id].append(assertion)
        if assertion.activity_id not in sets["activity"]:
            _add(issues, "ASSERTION_ACTIVITY_MISSING", record_id=assertion.assertion_id, path="activity_id")
        if assertion.ontology_ref_id not in sets["ontology"]:
            _add(issues, "ASSERTION_ONTOLOGY_MISSING", record_id=assertion.assertion_id, path="ontology_ref_id")
        for link in assertion.evidence_links:
            if link.evidence_id not in sets["evidence"]:
                _add(issues, "ASSERTION_EVIDENCE_MISSING", record_id=assertion.assertion_id, path="evidence_links")
        if assertion.value == AssertionValue.UNKNOWN:
            if assertion.coverage is None or not assertion.uncertainty_reason.strip():
                _add(issues, "UNKNOWN_COVERAGE_REQUIRED", record_id=assertion.assertion_id)
            if assertion.evidence_links:
                _add(issues, "UNKNOWN_EVIDENCE_FORBIDDEN", record_id=assertion.assertion_id)
            if assertion.coverage:
                for source_id in assertion.coverage.source_artifact_ids:
                    if source_id not in sets["source"]:
                        _add(issues, "UNKNOWN_COVERAGE_SOURCE_MISSING", record_id=assertion.assertion_id)
        elif not assertion.evidence_links and assertion.inference_id is None:
            _add(issues, "ASSERTION_GROUNDING_REQUIRED", record_id=assertion.assertion_id)
        if assertion.origin == AssertionOrigin.INFERRED and assertion.inference_id not in sets["inference"]:
            _add(issues, "INFERRED_ASSERTION_INPUT_MISSING", record_id=assertion.assertion_id, path="inference_id")
        if assertion.origin != AssertionOrigin.INFERRED and assertion.inference_id is not None:
            _add(issues, "INFERENCE_ORIGIN_MISMATCH", record_id=assertion.assertion_id, path="inference_id")
        for field, target in (
            ("supersedes_assertion_id", assertion.supersedes_assertion_id),
            ("retracts_assertion_id", assertion.retracts_assertion_id),
        ):
            if target and target not in sets["assertion"]:
                _add(issues, "ASSERTION_HISTORY_TARGET_MISSING", record_id=assertion.assertion_id, path=field)
    for fact_id, assertions in assertions_by_fact.items():
        if not assertions:
            _add(issues, "NAKED_FACT", record_id=fact_id)

    for inference in bundle.inferences:
        for fact_id in inference.input_fact_ids:
            if fact_id not in sets["fact"]:
                _add(issues, "INFERENCE_FACT_MISSING", record_id=inference.inference_id)
        for assertion_id in inference.input_assertion_ids:
            if assertion_id not in sets["assertion"]:
                _add(issues, "INFERENCE_ASSERTION_MISSING", record_id=inference.inference_id)
        if inference.ontology_ref_id not in sets["ontology"]:
            _add(issues, "INFERENCE_ONTOLOGY_MISSING", record_id=inference.inference_id)
    for activity in bundle.activities:
        for input_id in activity.input_record_ids:
            if input_id not in set(all_ids):
                _add(issues, "ACTIVITY_INPUT_MISSING", record_id=activity.activity_id, path="input_record_ids")

    for conflict in bundle.conflicts:
        members = [assertion_by_id.get(item) for item in conflict.assertion_ids]
        if any(item is None for item in members):
            _add(issues, "CONFLICT_ASSERTION_MISSING", record_id=conflict.conflict_id)
            continue
        facts = {item.fact_id for item in members if item is not None}
        values = {item.value for item in members if item is not None}
        overlap = all(_time_overlaps(members[0].clinical_time, item.clinical_time) for item in members[1:])
        if len(facts) != 1 or not {AssertionValue.TRUE, AssertionValue.FALSE}.issubset(values) or not overlap:
            _add(issues, "CONFLICT_SEMANTICS_INVALID", record_id=conflict.conflict_id)

    known_ids = set(all_ids)
    for review in bundle.review_actions:
        for target in review.target_record_ids:
            if target not in known_ids:
                _add(issues, "REVIEW_TARGET_MISSING", record_id=review.review_action_id)
        if review.previous_review_action_id and review.previous_review_action_id not in sets["review"]:
            _add(issues, "REVIEW_PREVIOUS_MISSING", record_id=review.review_action_id)

    _validate_lineage(bundle, issues, source_material)
    _validate_ontology(bundle, pack, issues)
    ordered = tuple(sorted(issues, key=lambda item: (item.code, item.record_id, item.path)))
    return ValidationResult(accepted_bundle=None if ordered else bundle, issues=ordered)


def validate_candidate(
    candidate: CandidateAssertion, pack: OntologyPack, *, source_material: SourceMaterial | None = None,
) -> ValidationResult:
    if candidate.assertion_id not in {item.assertion_id for item in candidate.bundle.assertions}:
        return ValidationResult(issues=(ValidationIssue(
            code="CANDIDATE_ASSERTION_MISSING", record_id=candidate.candidate_id,
            path="assertion_id",
        ),))
    return validate_bundle(candidate.bundle, pack, source_material=source_material)


def schema_documents() -> dict[str, Any]:
    """从 Pydantic 权威模型导出 JSON Schema，不维护手写副本。"""
    return {
        "candidate_assertion": CandidateAssertion.model_json_schema(),
        "evidence_bundle": EvidenceBundle.model_json_schema(),
        "ontology_pack": OntologyPack.model_json_schema(),
    }
