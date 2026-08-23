# -*- coding: utf-8 -*-
"""Diagnosis evidence shadow 的纯 extractor 与 terminology mapping。"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field

from javert.tools.note_diagnosis import DIAGNOSIS_STAGES, SPLIT_PATTERN

from .models import (
    Assertion, AssertionOrigin, AssertionValue, CandidateAssertion, ClinicalTimeKind,
    ClinicalValidTime, ConceptRef, CoverageScope, EntityRef, EvidenceBundle,
    EvidenceItem, EvidenceLink, Fact, FactObject, FrozenStrictModel, OntologyPack,
    OntologyRef, ProvenanceActivity, SourceArtifact, StructuredLocator,
    DocumentLocator, ValidationIssue,
)
from .ontology import ontology_payload_checksum
from .serialization import checksum_value, row_fingerprint, sha256_digest


class DiagnosisConcept(FrozenStrictModel):
    code: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()


class DiagnosisTerminology(FrozenStrictModel):
    terminology_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    concept_system: str = Field(min_length=1)
    concept_version: str = Field(min_length=1)
    concepts: tuple[DiagnosisConcept, ...]
    content_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


def terminology_checksum(value: DiagnosisTerminology | Mapping[str, Any]) -> str:
    raw = value.model_dump(mode="json") if isinstance(value, DiagnosisTerminology) else dict(value)
    raw.pop("content_checksum", None)
    return checksum_value(raw)


def build_terminology(**kwargs: Any) -> DiagnosisTerminology:
    raw = dict(kwargs)
    raw.setdefault("content_checksum", "sha256:" + "0" * 64)
    provisional = DiagnosisTerminology.model_validate(raw)
    raw["content_checksum"] = terminology_checksum(provisional)
    return DiagnosisTerminology.model_validate(raw)


def _normalized_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).lower().split()).strip("：:，,。.;；")


class DiagnosisResolver:
    def __init__(self, terminology: DiagnosisTerminology):
        if terminology.content_checksum != terminology_checksum(terminology):
            raise ValueError("diagnosis terminology checksum mismatch")
        self.terminology = terminology
        self._by_code = {item.code: item for item in terminology.concepts}
        self._by_text: dict[str, DiagnosisConcept] = {}
        for concept in terminology.concepts:
            for value in (concept.canonical_name, *concept.aliases):
                key = _normalized_text(value)
                existing = self._by_text.get(key)
                if existing is not None and existing.code != concept.code:
                    raise ValueError("diagnosis terminology alias collision")
                self._by_text[key] = concept

    def resolve(self, *, code: str = "", text: str = "") -> DiagnosisConcept | None:
        if code.strip():
            return self._by_code.get(code.strip())
        return self._by_text.get(_normalized_text(text))

    def concept_ref(self, concept: DiagnosisConcept) -> ConceptRef:
        return ConceptRef(
            system=self.terminology.concept_system, code=concept.code,
            version=self.terminology.concept_version, display=concept.canonical_name,
        )


class DiagnosisExtraction(FrozenStrictModel):
    candidates: tuple[CandidateAssertion, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()
    scanned_row_count: int = Field(default=0, ge=0)
    occurrence_count: int = Field(default=0, ge=0)


def source_artifact_for_rows(
    *, source_artifact_id: str, artifact_version: str, artifact_kind: str,
    rows: Sequence[Mapping[str, Any]], recorded_at: datetime,
) -> SourceArtifact:
    return SourceArtifact(
        source_artifact_id=source_artifact_id, artifact_version=artifact_version,
        artifact_kind=artifact_kind, recorded_at=recorded_at,
        content_checksum=checksum_value(list(rows)),
    )


def ontology_ref_for_pack(pack: OntologyPack, *, ref_id: str = "ontology:diagnosis:v0.1") -> OntologyRef:
    if pack.content_checksum != ontology_payload_checksum(pack):
        raise ValueError("ontology pack checksum mismatch")
    return OntologyRef(
        ontology_ref_id=ref_id, ontology_id=pack.ontology_id,
        version=pack.version, schema_version=pack.schema_version,
        content_checksum=pack.content_checksum,
    )


def _candidate(
    *, token: str, source: SourceArtifact, locator,
    patient_entity_id: str, concept_ref: ConceptRef, ontology_ref: OntologyRef,
    recorded_at: datetime, value: AssertionValue,
    uncertainty_reason: str = "",
) -> CandidateAssertion:
    disease_token = sha256_digest(
        f"{concept_ref.system}|{concept_ref.code}|{concept_ref.version}".encode("utf-8")
    )[7:19]
    patient = EntityRef(
        entity_id=patient_entity_id, entity_type="Patient",
        ontology_ref_id=ontology_ref.ontology_ref_id,
    )
    disease = EntityRef(
        entity_id=f"entity:disease:{disease_token}", entity_type="Disease",
        ontology_ref_id=ontology_ref.ontology_ref_id, concept=concept_ref,
    )
    fact = Fact(
        fact_id=f"fact:{token}", subject_entity_id=patient.entity_id,
        predicate="has_diagnosis", object=FactObject(entity_id=disease.entity_id),
    )
    evidence = EvidenceItem(
        evidence_id=f"evidence:{token}", source_artifact_id=source.source_artifact_id,
        locator=locator,
        excerpt_checksum=(
            locator.span_checksum if isinstance(locator, DocumentLocator)
            else locator.value_checksum
        ),
    )
    activity = ProvenanceActivity(
        activity_id=f"activity:{token}", activity_kind="diagnosis-extraction",
        producer_id="diagnosis-shadow-extractor", producer_version="0.1.0",
        recorded_at=recorded_at, input_record_ids=(source.source_artifact_id,),
    )
    unknown = value == AssertionValue.UNKNOWN
    assertion = Assertion(
        assertion_id=f"assertion:{token}", fact_id=fact.fact_id,
        origin=AssertionOrigin.OBSERVED, value=value,
        clinical_time=ClinicalValidTime(kind=ClinicalTimeKind.UNKNOWN),
        recorded_at=recorded_at, producer_id=activity.producer_id,
        activity_id=activity.activity_id, ontology_ref_id=ontology_ref.ontology_ref_id,
        evidence_links=() if unknown else (
            EvidenceLink(
                relation="contradicts" if value == AssertionValue.FALSE else "supports",
                evidence_id=evidence.evidence_id,
            ),
        ),
        coverage=CoverageScope(
            source_artifact_ids=(source.source_artifact_id,),
            clinical_time=ClinicalValidTime(kind=ClinicalTimeKind.UNKNOWN),
            reason="诊断来源已扫描",
        ) if unknown else None,
        uncertainty_reason=uncertainty_reason,
    )
    bundle = EvidenceBundle(
        deidentified=True, ontology_refs=(ontology_ref,), sources=(source,),
        evidence_items=(evidence,), entities=(patient, disease), facts=(fact,),
        activities=(activity,), assertions=(assertion,),
    )
    return CandidateAssertion(
        candidate_id=f"candidate:{token}", assertion_id=assertion.assertion_id,
        bundle=bundle,
    )


def extract_structured_diagnoses(
    rows: Sequence[Mapping[str, Any]], *, source: SourceArtifact,
    patient_entity_id: str, resolver: DiagnosisResolver,
    ontology_ref: OntologyRef, recorded_at: datetime,
) -> DiagnosisExtraction:
    candidates: list[CandidateAssertion] = []
    issues: list[ValidationIssue] = []
    for ordinal, row in enumerate(rows):
        code_field = next((field for field in ("diag_code", "inhosp_diag_code") if str(row.get(field) or "").strip()), "")
        name_field = next((field for field in ("diag_name", "inhosp_diag_name") if str(row.get(field) or "").strip()), "")
        code = str(row.get(code_field) or "").strip() if code_field else ""
        name = str(row.get(name_field) or "").strip() if name_field else ""
        if not code and not name:
            issues.append(ValidationIssue(code="DIAGNOSIS_ROW_EMPTY", record_id=f"row:{ordinal}"))
            continue
        concept = resolver.resolve(code=code, text=name)
        if concept is None:
            issues.append(ValidationIssue(code="DIAGNOSIS_UNMAPPED", record_id=f"row:{ordinal}"))
            continue
        consumed_field = code_field or name_field
        token = (
            f"structured:{source.source_artifact_id}:{source.artifact_version}:"
            f"{source.content_checksum[7:19]}:{ordinal}"
        )
        candidates.append(_candidate(
            token=token, source=source,
            locator=StructuredLocator(
                row_ordinal=ordinal, row_fingerprint=row_fingerprint(row),
                field=consumed_field, value_checksum=checksum_value(row[consumed_field]),
            ),
            patient_entity_id=patient_entity_id,
            concept_ref=resolver.concept_ref(concept), ontology_ref=ontology_ref,
            recorded_at=recorded_at, value=AssertionValue.TRUE,
        ))
    return DiagnosisExtraction(
        candidates=tuple(candidates),
        issues=tuple(sorted(issues, key=lambda issue: (issue.code, issue.record_id))),
        scanned_row_count=len(rows), occurrence_count=len(candidates),
    )


def _segments(text: str) -> list[tuple[int, int, str]]:
    matches = list(SPLIT_PATTERN.finditer(text))
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for match in matches:
        if match.start() > cursor:
            ranges.append((cursor, match.start()))
        cursor = match.end()
    if cursor < len(text):
        ranges.append((cursor, len(text)))
    if not matches and text:
        ranges = [(0, len(text))]
    output = []
    for start, end in ranges:
        raw = text[start:end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start += leading
        end -= trailing
        value = text[start:end]
        if len(value) >= 2:
            output.append((start, end, value))
    return output


_POLARITY_PREFIX = re.compile(r"^(?P<prefix>否认|排除|疑似|待排|考虑)[：:\s]*")


def _polarity(value: str) -> tuple[AssertionValue, str, str]:
    match = _POLARITY_PREFIX.match(value.strip())
    if not match:
        return AssertionValue.TRUE, value.strip(), ""
    prefix = match.group("prefix")
    proposition = value.strip()[match.end():].strip()
    if prefix in {"否认", "排除"}:
        return AssertionValue.FALSE, proposition, ""
    return AssertionValue.UNKNOWN, proposition, f"文书表达{prefix}，不足以确认真或假"


def extract_document_diagnoses(
    rows: Sequence[Mapping[str, Any]], *, source: SourceArtifact,
    patient_entity_id: str, resolver: DiagnosisResolver,
    ontology_ref: OntologyRef, recorded_at: datetime,
) -> DiagnosisExtraction:
    candidates: list[CandidateAssertion] = []
    issues: list[ValidationIssue] = []
    occurrences = 0
    for ordinal, row in enumerate(rows):
        section = str(row.get("子阶段") or "").strip()
        if section not in DIAGNOSIS_STAGES:
            continue
        text = str(row.get("内容") or "")
        for segment_index, (start, end, raw) in enumerate(_segments(text)):
            occurrences += 1
            value, proposition, uncertainty = _polarity(raw)
            concept = resolver.resolve(text=proposition)
            if concept is None:
                issues.append(ValidationIssue(
                    code="DIAGNOSIS_TEXT_UNMAPPED",
                    record_id=f"row:{ordinal}:segment:{segment_index}",
                ))
                continue
            token = (
                f"document:{source.source_artifact_id}:{source.artifact_version}:"
                f"{source.content_checksum[7:19]}:{ordinal}:{segment_index}"
            )
            candidates.append(_candidate(
                token=token, source=source,
                locator=DocumentLocator(
                    row_ordinal=ordinal, row_fingerprint=row_fingerprint(row),
                    field="内容", section=section, start_char=start, end_char=end,
                    span_checksum=checksum_value(raw),
                ),
                patient_entity_id=patient_entity_id,
                concept_ref=resolver.concept_ref(concept), ontology_ref=ontology_ref,
                recorded_at=recorded_at, value=value,
                uncertainty_reason=uncertainty,
            ))
    return DiagnosisExtraction(
        candidates=tuple(candidates),
        issues=tuple(sorted(issues, key=lambda issue: (issue.code, issue.record_id))),
        scanned_row_count=len(rows), occurrence_count=occurrences,
    )
