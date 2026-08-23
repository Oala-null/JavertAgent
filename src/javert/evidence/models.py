# -*- coding: utf-8 -*-
"""Evidence Contract v0.1 的严格、无运行时副作用模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Checksum = str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrozenStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AssertionOrigin(StrEnum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    KNOWLEDGE = "KNOWLEDGE"


class AssertionValue(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class ClinicalTimeKind(StrEnum):
    POINT = "POINT"
    INTERVAL = "INTERVAL"
    UNKNOWN = "UNKNOWN"


class ClinicalValidTime(FrozenStrictModel):
    kind: ClinicalTimeKind
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "ClinicalValidTime":
        if self.kind == ClinicalTimeKind.UNKNOWN:
            if self.start is not None or self.end is not None:
                raise ValueError("UNKNOWN clinical time 不得伪造 start/end")
        elif self.kind == ClinicalTimeKind.POINT:
            if self.start is None or self.end is not None:
                raise ValueError("POINT clinical time 必须只有 start")
        elif self.start is None or self.end is None or self.end < self.start:
            raise ValueError("INTERVAL clinical time 必须有合法 start/end")
        return self


class ConceptRef(FrozenStrictModel):
    system: str = Field(min_length=1)
    code: str = Field(min_length=1)
    version: str = Field(min_length=1)
    display: str = ""

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.system, self.code, self.version)


class OntologyRef(FrozenStrictModel):
    ontology_ref_id: str = Field(min_length=3)
    ontology_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    content_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class UpstreamLocator(FrozenStrictModel):
    database_ref: str = Field(min_length=1)
    table: str = Field(min_length=1)
    primary_key: str = Field(min_length=1)
    column: str = Field(min_length=1)


class StructuredLocator(FrozenStrictModel):
    kind: Literal["structured"] = "structured"
    row_ordinal: int = Field(ge=0)
    row_fingerprint: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    field: str = Field(min_length=1)
    value_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    upstream: UpstreamLocator | None = None


class DocumentLocator(FrozenStrictModel):
    kind: Literal["document"] = "document"
    row_ordinal: int = Field(ge=0)
    row_fingerprint: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    field: str = Field(min_length=1)
    document_id: str | None = None
    section: str = ""
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    span_checksum: Checksum | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    upstream: UpstreamLocator | None = None

    @model_validator(mode="after")
    def _validate_span(self) -> "DocumentLocator":
        values = (self.start_char, self.end_char, self.span_checksum)
        if all(value is None for value in values):
            return self
        if any(value is None for value in values):
            raise ValueError("document span 必须同时声明 start/end/checksum")
        if self.end_char <= self.start_char:
            raise ValueError("document span 采用 [start,end)，end 必须更大")
        return self


CanonicalLocator = StructuredLocator | DocumentLocator


class SourceArtifact(FrozenStrictModel):
    source_artifact_id: str = Field(min_length=3)
    artifact_version: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    recorded_at: datetime
    content_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class EvidenceItem(FrozenStrictModel):
    evidence_id: str = Field(min_length=3)
    source_artifact_id: str = Field(min_length=3)
    locator: CanonicalLocator = Field(discriminator="kind")
    excerpt_checksum: Checksum | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


class EntityRef(FrozenStrictModel):
    entity_id: str = Field(min_length=3)
    entity_type: str = Field(min_length=1)
    ontology_ref_id: str = Field(min_length=3)
    concept: ConceptRef | None = None


class FactObject(FrozenStrictModel):
    entity_id: str | None = None
    literal_type: str | None = None
    literal_value: Any = None

    @model_validator(mode="after")
    def _one_value(self) -> "FactObject":
        has_entity = self.entity_id is not None
        has_literal = self.literal_type is not None
        if has_entity == has_literal:
            raise ValueError("FactObject 必须且只能声明 entity 或 typed literal")
        if has_entity and self.literal_value is not None:
            raise ValueError("entity object 不得携带 literal_value")
        return self


class Fact(FrozenStrictModel):
    fact_id: str = Field(min_length=3)
    subject_entity_id: str = Field(min_length=3)
    predicate: str = Field(min_length=1)
    object: FactObject


class ProvenanceActivity(FrozenStrictModel):
    activity_id: str = Field(min_length=3)
    activity_kind: str = Field(min_length=1)
    producer_id: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    recorded_at: datetime
    input_record_ids: tuple[str, ...] = ()


class EvidenceLink(FrozenStrictModel):
    relation: Literal["supports", "contradicts"]
    evidence_id: str = Field(min_length=3)


class CoverageScope(FrozenStrictModel):
    source_artifact_ids: tuple[str, ...] = Field(min_length=1)
    clinical_time: ClinicalValidTime
    reason: str = Field(min_length=1)


class Assertion(FrozenStrictModel):
    assertion_id: str = Field(min_length=3)
    fact_id: str = Field(min_length=3)
    origin: AssertionOrigin
    value: AssertionValue
    clinical_time: ClinicalValidTime
    recorded_at: datetime
    producer_id: str = Field(min_length=1)
    activity_id: str = Field(min_length=3)
    ontology_ref_id: str = Field(min_length=3)
    evidence_links: tuple[EvidenceLink, ...] = ()
    inference_id: str | None = None
    coverage: CoverageScope | None = None
    uncertainty_reason: str = ""
    supersedes_assertion_id: str | None = None
    retracts_assertion_id: str | None = None


class Inference(FrozenStrictModel):
    inference_id: str = Field(min_length=3)
    inference_kind: Literal["is_a"] = "is_a"
    input_fact_ids: tuple[str, ...] = ()
    input_assertion_ids: tuple[str, ...] = ()
    ontology_ref_id: str = Field(min_length=3)
    traversed_concept_codes: tuple[str, ...] = Field(min_length=2)
    reasoner_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _has_input(self) -> "Inference":
        if not self.input_fact_ids and not self.input_assertion_ids:
            raise ValueError("Inference 至少需要一个输入")
        return self


class Conflict(FrozenStrictModel):
    conflict_id: str = Field(min_length=3)
    assertion_ids: tuple[str, ...] = Field(min_length=2)
    status: Literal["OPEN", "RESOLVED"] = "OPEN"
    recorded_at: datetime


class ReviewAction(FrozenStrictModel):
    review_action_id: str = Field(min_length=3)
    target_record_ids: tuple[str, ...] = Field(min_length=1)
    reviewer_ref: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    reason_codes: tuple[str, ...] = ()
    recorded_at: datetime
    previous_review_action_id: str | None = None


class EvidenceBundle(FrozenStrictModel):
    contract_version: Literal["0.1.0"] = "0.1.0"
    deidentified: bool
    ontology_refs: tuple[OntologyRef, ...] = ()
    sources: tuple[SourceArtifact, ...] = ()
    evidence_items: tuple[EvidenceItem, ...] = ()
    entities: tuple[EntityRef, ...] = ()
    facts: tuple[Fact, ...] = ()
    activities: tuple[ProvenanceActivity, ...] = ()
    inferences: tuple[Inference, ...] = ()
    assertions: tuple[Assertion, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    review_actions: tuple[ReviewAction, ...] = ()


class CandidateAssertion(FrozenStrictModel):
    candidate_id: str = Field(min_length=3)
    assertion_id: str = Field(min_length=3)
    bundle: EvidenceBundle


class ValidationIssue(FrozenStrictModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    record_id: str = ""
    path: str = ""


class ValidationResult(FrozenStrictModel):
    accepted_bundle: EvidenceBundle | None = None
    issues: tuple[ValidationIssue, ...] = ()


class EntityTypeDefinition(FrozenStrictModel):
    type_id: str = Field(min_length=1)


class PredicateDefinition(FrozenStrictModel):
    predicate_id: str = Field(min_length=1)
    domain_types: tuple[str, ...] = Field(min_length=1)
    range_types: tuple[str, ...] = ()
    literal_types: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _has_range(self) -> "PredicateDefinition":
        if not self.range_types and not self.literal_types:
            raise ValueError("predicate 必须声明 entity range 或 literal type")
        return self


class ConceptDefinition(FrozenStrictModel):
    concept: ConceptRef
    entity_type: str = Field(min_length=1)


class IsAEdge(FrozenStrictModel):
    hierarchy: Literal["entity_type", "concept"]
    child: str = Field(min_length=1)
    parent: str = Field(min_length=1)


class OntologyPack(FrozenStrictModel):
    ontology_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    schema_version: Literal["0.1.0"] = "0.1.0"
    content_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    entity_types: tuple[EntityTypeDefinition, ...] = ()
    predicates: tuple[PredicateDefinition, ...] = ()
    concepts: tuple[ConceptDefinition, ...] = ()
    is_a_edges: tuple[IsAEdge, ...] = ()
