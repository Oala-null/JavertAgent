# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from javert.evidence.models import (
    Assertion, AssertionOrigin, AssertionValue, CandidateAssertion, ClinicalTimeKind,
    ClinicalValidTime, ConceptDefinition, ConceptRef, Conflict, CoverageScope, DocumentLocator, EntityRef,
    EntityTypeDefinition, EvidenceBundle, EvidenceItem, EvidenceLink, Fact, FactObject,
    IsAEdge, OntologyPack, OntologyRef, PredicateDefinition, ProvenanceActivity,
    SourceArtifact, StructuredLocator,
)
from javert.evidence.ontology import ontology_payload_checksum, validate_ontology_pack
from javert.evidence.ontology import build_is_a_assertion, concept_path
from javert.evidence.privacy import synthetic_fixture_issues
from javert.evidence.serialization import checksum_value, row_fingerprint
from javert.evidence.validation import schema_documents, validate_bundle, validate_candidate

NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)
ROWS = [{"diag_name": "甲状腺乳头状癌", "diag_code": "SYNTH-PTC"}]
FIXTURES = Path(__file__).parent / "fixtures" / "evidence_contract"


def _pack() -> OntologyPack:
    pack = OntologyPack(
        ontology_id="synthetic-clinical", version="2026.08.23",
        content_checksum="sha256:" + "0" * 64,
        entity_types=tuple(EntityTypeDefinition(type_id=value) for value in (
            "ClinicalEntity", "Patient", "Disease", "Medication",
        )),
        predicates=(PredicateDefinition(
            predicate_id="has_diagnosis", domain_types=("Patient",), range_types=("Disease",),
        ),),
        concepts=(
            ConceptDefinition(
                concept=ConceptRef(system="urn:synthetic", code="PTC", version="1", display="甲状腺乳头状癌"),
                entity_type="Disease",
            ),
            ConceptDefinition(
                concept=ConceptRef(system="urn:synthetic", code="THYROID-MALIGNANCY", version="1", display="甲状腺恶性肿瘤"),
                entity_type="Disease",
            ),
        ),
        is_a_edges=(
            IsAEdge(hierarchy="entity_type", child="Patient", parent="ClinicalEntity"),
            IsAEdge(hierarchy="entity_type", child="Disease", parent="ClinicalEntity"),
            IsAEdge(hierarchy="concept", child="urn:synthetic|PTC|1", parent="urn:synthetic|THYROID-MALIGNANCY|1"),
        ),
    )
    return pack.model_copy(update={"content_checksum": ontology_payload_checksum(pack)})


def _bundle(*, subject_type: str = "Patient", unknown: bool = False) -> EvidenceBundle:
    pack = _pack()
    ontology_ref = OntologyRef(
        ontology_ref_id="ontology:synthetic:1", ontology_id=pack.ontology_id,
        version=pack.version, schema_version=pack.schema_version,
        content_checksum=pack.content_checksum,
    )
    source = SourceArtifact(
        source_artifact_id="source:diagnosis:1", artifact_version="1",
        artifact_kind="structured", recorded_at=NOW,
        content_checksum=checksum_value(ROWS),
    )
    evidence = EvidenceItem(
        evidence_id="evidence:diagnosis:1", source_artifact_id=source.source_artifact_id,
        locator=StructuredLocator(
            row_ordinal=0, row_fingerprint=row_fingerprint(ROWS[0]),
            field="diag_name", value_checksum=checksum_value(ROWS[0]["diag_name"]),
        ),
    )
    patient = EntityRef(
        entity_id="entity:patient:synthetic", entity_type=subject_type,
        ontology_ref_id=ontology_ref.ontology_ref_id,
    )
    disease = EntityRef(
        entity_id="entity:disease:ptc", entity_type="Disease",
        ontology_ref_id=ontology_ref.ontology_ref_id,
        concept=ConceptRef(system="urn:synthetic", code="PTC", version="1", display="PTC"),
    )
    fact = Fact(
        fact_id="fact:diagnosis:ptc", subject_entity_id=patient.entity_id,
        predicate="has_diagnosis", object=FactObject(entity_id=disease.entity_id),
    )
    activity = ProvenanceActivity(
        activity_id="activity:extract:1", activity_kind="structured-extraction",
        producer_id="synthetic-extractor", producer_version="1.0.0", recorded_at=NOW,
        input_record_ids=(source.source_artifact_id,),
    )
    assertion = Assertion(
        assertion_id="assertion:diagnosis:1", fact_id=fact.fact_id,
        origin=AssertionOrigin.OBSERVED,
        value=AssertionValue.UNKNOWN if unknown else AssertionValue.TRUE,
        clinical_time=ClinicalValidTime(kind=ClinicalTimeKind.UNKNOWN),
        recorded_at=NOW, producer_id=activity.producer_id,
        activity_id=activity.activity_id, ontology_ref_id=ontology_ref.ontology_ref_id,
        evidence_links=() if unknown else (EvidenceLink(relation="supports", evidence_id=evidence.evidence_id),),
        coverage=CoverageScope(
            source_artifact_ids=(source.source_artifact_id,),
            clinical_time=ClinicalValidTime(kind=ClinicalTimeKind.UNKNOWN),
            reason="已扫描结构化诊断来源",
        ) if unknown else None,
        uncertainty_reason="未见可确认诊断" if unknown else "",
    )
    return EvidenceBundle(
        deidentified=True, ontology_refs=(ontology_ref,), sources=(source,),
        evidence_items=(evidence,), entities=(patient, disease), facts=(fact,),
        activities=(activity,), assertions=(assertion,),
    )


def test_valid_bundle_round_trips_and_validates_source_material():
    bundle = _bundle()
    result = validate_bundle(bundle, _pack(), source_material={"source:diagnosis:1": ROWS})
    assert result.issues == ()
    assert EvidenceBundle.model_validate_json(bundle.model_dump_json()) == bundle
    assert set(schema_documents()) == {"candidate_assertion", "evidence_bundle", "ontology_pack"}


def test_candidate_and_no_naked_fact_fail_closed():
    bundle = _bundle()
    candidate = CandidateAssertion(
        candidate_id="candidate:diagnosis:1",
        assertion_id=bundle.assertions[0].assertion_id,
        bundle=bundle,
    )
    assert validate_candidate(candidate, _pack()).accepted_bundle == bundle
    naked = bundle.model_copy(update={"assertions": ()})
    assert [issue.code for issue in validate_bundle(naked, _pack()).issues] == ["NAKED_FACT"]


def test_unknown_is_scoped_and_not_a_negative_or_unknown_entity():
    bundle = _bundle(unknown=True)
    result = validate_bundle(bundle, _pack())
    assert result.issues == ()
    assert bundle.assertions[0].value == AssertionValue.UNKNOWN
    assert all(entity.concept is None or entity.concept.code != "UNKNOWN" for entity in bundle.entities)


def test_ontology_rejects_illegal_medication_has_diagnosis_relation():
    bundle = _bundle(subject_type="Medication")
    issues = validate_bundle(bundle, _pack()).issues
    assert any(issue.code == "PREDICATE_DOMAIN_MISMATCH" for issue in issues)


def test_pack_checksum_cycle_and_locator_mismatch_are_deterministic():
    pack = _pack()
    bad_pack = pack.model_copy(update={"content_checksum": "sha256:" + "f" * 64})
    assert validate_ontology_pack(bad_pack)[0].code == "ONTOLOGY_CHECKSUM_MISMATCH"
    cycle_pack = pack.model_copy(update={
        "is_a_edges": (*pack.is_a_edges, IsAEdge(
            hierarchy="entity_type", child="ClinicalEntity", parent="Patient",
        )),
    })
    cycle_pack = cycle_pack.model_copy(update={"content_checksum": ontology_payload_checksum(cycle_pack)})
    assert any(issue.code == "ONTOLOGY_TYPE_CYCLE" for issue in validate_ontology_pack(cycle_pack))
    bundle = _bundle()
    bad_rows = [{"diag_name": "别的诊断", "diag_code": "SYNTH-X"}]
    first = validate_bundle(bundle, pack, source_material={"source:diagnosis:1": bad_rows}).issues
    second = validate_bundle(bundle, pack, source_material={"source:diagnosis:1": bad_rows}).issues
    assert first == second
    assert {issue.code for issue in first} == {
        "SOURCE_ARTIFACT_CHECKSUM_MISMATCH", "LOCATOR_ROW_FINGERPRINT_MISMATCH",
        "LOCATOR_VALUE_CHECKSUM_MISMATCH",
    }


def test_canonical_json_fixtures_are_versioned_and_reproducible():
    pack = OntologyPack.model_validate_json((FIXTURES / "ontology_v0_1.json").read_text())
    bundle = EvidenceBundle.model_validate_json((FIXTURES / "valid_observed_bundle.json").read_text())
    expected = (FIXTURES / "valid_observed_expected_issues.json").read_text().strip()
    assert validate_ontology_pack(pack) == ()
    assert validate_bundle(bundle, pack).issues == ()
    assert expected == "[]"


def test_document_span_is_end_exclusive_and_checksum_verified():
    row = {"内容": "诊断：PTC"}
    bundle = _bundle()
    source = bundle.sources[0].model_copy(update={"content_checksum": checksum_value([row])})
    evidence = bundle.evidence_items[0].model_copy(update={
        "locator": DocumentLocator(
            row_ordinal=0, row_fingerprint=row_fingerprint(row), field="内容",
            section="诊断", start_char=3, end_char=6,
            span_checksum=checksum_value("PTC"),
        ),
        "excerpt_checksum": checksum_value("PTC"),
    })
    document_bundle = bundle.model_copy(update={"sources": (source,), "evidence_items": (evidence,)})
    assert validate_bundle(
        document_bundle, _pack(), source_material={source.source_artifact_id: [row]},
    ).issues == ()


def test_conflict_and_retraction_preserve_prior_assertions():
    bundle = _bundle()
    negative = bundle.assertions[0].model_copy(update={
        "assertion_id": "assertion:diagnosis:negative",
        "value": AssertionValue.FALSE,
        "evidence_links": (EvidenceLink(relation="contradicts", evidence_id=bundle.evidence_items[0].evidence_id),),
    })
    conflict = Conflict(
        conflict_id="conflict:diagnosis:1",
        assertion_ids=(bundle.assertions[0].assertion_id, negative.assertion_id),
        recorded_at=NOW,
    )
    conflicted = bundle.model_copy(update={
        "assertions": (*bundle.assertions, negative), "conflicts": (conflict,),
    })
    assert validate_bundle(conflicted, _pack()).issues == ()
    retraction = negative.model_copy(update={
        "assertion_id": "assertion:diagnosis:retraction",
        "value": AssertionValue.TRUE,
        "evidence_links": bundle.assertions[0].evidence_links,
        "retracts_assertion_id": negative.assertion_id,
    })
    retracted = bundle.model_copy(update={"assertions": (*bundle.assertions, negative, retraction)})
    assert validate_bundle(retracted, _pack()).issues == ()


def test_is_a_inference_retains_path_and_does_not_rewrite_observed_fact():
    pack = _pack()
    bundle = _bundle()
    ontology_ref = bundle.ontology_refs[0]
    broader_entity = EntityRef(
        entity_id="entity:disease:thyroid-malignancy", entity_type="Disease",
        ontology_ref_id=ontology_ref.ontology_ref_id,
        concept=ConceptRef(
            system="urn:synthetic", code="THYROID-MALIGNANCY", version="1",
            display="甲状腺恶性肿瘤",
        ),
    )
    broader_fact = Fact(
        fact_id="fact:diagnosis:thyroid-malignancy",
        subject_entity_id=bundle.facts[0].subject_entity_id,
        predicate="has_diagnosis", object=FactObject(entity_id=broader_entity.entity_id),
    )
    activity = ProvenanceActivity(
        activity_id="activity:reasoner:1", activity_kind="ontology-is-a",
        producer_id="synthetic-reasoner", producer_version="1", recorded_at=NOW,
        input_record_ids=(bundle.facts[0].fact_id, bundle.assertions[0].assertion_id),
    )
    path = concept_path(pack, "urn:synthetic|PTC|1", "urn:synthetic|THYROID-MALIGNANCY|1")
    inference, assertion = build_is_a_assertion(
        inference_id="inference:is-a:1", assertion_id="assertion:inferred:1",
        fact=broader_fact, input_fact_id=bundle.facts[0].fact_id,
        input_assertion_id=bundle.assertions[0].assertion_id,
        ontology_ref=ontology_ref, activity=activity, path=path,
        clinical_time=ClinicalValidTime(kind=ClinicalTimeKind.UNKNOWN), recorded_at=NOW,
    )
    inferred = bundle.model_copy(update={
        "entities": (*bundle.entities, broader_entity), "facts": (*bundle.facts, broader_fact),
        "activities": (*bundle.activities, activity), "inferences": (inference,),
        "assertions": (*bundle.assertions, assertion),
    })
    assert validate_bundle(inferred, pack).issues == ()
    assert inferred.assertions[0].origin == AssertionOrigin.OBSERVED
    assert assertion.origin == AssertionOrigin.INFERRED
    assert inference.traversed_concept_codes == path
    later_pack = pack.model_copy(update={"version": "2027.01.01"})
    later_pack = later_pack.model_copy(update={"content_checksum": ontology_payload_checksum(later_pack)})
    assert later_pack.version != ontology_ref.version
    assert inference.ontology_ref_id == ontology_ref.ontology_ref_id
    assert inference.traversed_concept_codes == path


def test_fixture_privacy_gate_reports_path_not_value():
    assert synthetic_fixture_issues({"deidentified": True, "case_ref": "SYNTH-1"}) == ()
    issues = synthetic_fixture_issues({"deidentified": True, "patient_id": "SECRET-PATIENT"})
    assert issues[0].path == "$.patient_id"
    assert "SECRET-PATIENT" not in issues[0].model_dump_json()
