# -*- coding: utf-8 -*-
"""Ontology v0.1 的本地、确定性校验与最小 is_a 推理。"""

from __future__ import annotations

from collections import defaultdict

from .models import (
    Assertion, AssertionOrigin, AssertionValue, ClinicalValidTime, Fact,
    Inference, OntologyPack, OntologyRef, ProvenanceActivity, ValidationIssue,
)
from .serialization import canonical_json_bytes, sha256_digest


def ontology_payload_checksum(pack: OntologyPack) -> str:
    payload = pack.model_dump(mode="json")
    payload.pop("content_checksum", None)
    return sha256_digest(canonical_json_bytes(payload))


def _cycle(nodes: set[str], parents: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for parent in sorted(parents.get(node, ())):
            if visit(parent):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in sorted(nodes))


def validate_ontology_pack(pack: OntologyPack) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if pack.content_checksum != ontology_payload_checksum(pack):
        issues.append(ValidationIssue(code="ONTOLOGY_CHECKSUM_MISMATCH", path="content_checksum"))

    type_ids = [item.type_id for item in pack.entity_types]
    predicate_ids = [item.predicate_id for item in pack.predicates]
    concept_keys = ["|".join(item.concept.identity) for item in pack.concepts]
    for label, values in (("TYPE", type_ids), ("PREDICATE", predicate_ids), ("CONCEPT", concept_keys)):
        if len(values) != len(set(values)):
            issues.append(ValidationIssue(code=f"ONTOLOGY_DUPLICATE_{label}"))

    type_set = set(type_ids)
    concept_set = set(concept_keys)
    parents: dict[str, dict[str, set[str]]] = {
        "entity_type": defaultdict(set), "concept": defaultdict(set),
    }
    for index, edge in enumerate(pack.is_a_edges):
        valid = type_set if edge.hierarchy == "entity_type" else concept_set
        path = f"is_a_edges[{index}]"
        if edge.child == edge.parent:
            issues.append(ValidationIssue(code="ONTOLOGY_SELF_EDGE", path=path))
        if edge.child not in valid or edge.parent not in valid:
            issues.append(ValidationIssue(code="ONTOLOGY_EDGE_ENDPOINT_MISSING", path=path))
        parents[edge.hierarchy][edge.child].add(edge.parent)
    edge_keys = [(edge.hierarchy, edge.child, edge.parent) for edge in pack.is_a_edges]
    if len(edge_keys) != len(set(edge_keys)):
        issues.append(ValidationIssue(code="ONTOLOGY_DUPLICATE_EDGE"))
    if _cycle(type_set, parents["entity_type"]):
        issues.append(ValidationIssue(code="ONTOLOGY_TYPE_CYCLE"))
    if _cycle(concept_set, parents["concept"]):
        issues.append(ValidationIssue(code="ONTOLOGY_CONCEPT_CYCLE"))

    for index, predicate in enumerate(pack.predicates):
        for type_id in (*predicate.domain_types, *predicate.range_types):
            if type_id not in type_set:
                issues.append(ValidationIssue(
                    code="ONTOLOGY_PREDICATE_TYPE_MISSING",
                    record_id=predicate.predicate_id,
                    path=f"predicates[{index}]",
                ))
    for index, concept in enumerate(pack.concepts):
        if concept.entity_type not in type_set:
            issues.append(ValidationIssue(
                code="ONTOLOGY_CONCEPT_TYPE_MISSING",
                path=f"concepts[{index}].entity_type",
            ))
    return tuple(sorted(issues, key=lambda item: (item.code, item.record_id, item.path)))


def _parents(pack: OntologyPack, hierarchy: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for edge in pack.is_a_edges:
        if edge.hierarchy == hierarchy:
            result[edge.child].add(edge.parent)
    return result


def is_subtype(pack: OntologyPack, child: str, parent: str) -> bool:
    if child == parent:
        return True
    parents = _parents(pack, "entity_type")
    pending = [child]
    seen: set[str] = set()
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        for candidate in parents.get(node, ()):
            if candidate == parent:
                return True
            pending.append(candidate)
    return False


def concept_path(pack: OntologyPack, child: str, parent: str) -> tuple[str, ...]:
    if child == parent:
        return (child,)
    parents = _parents(pack, "concept")
    pending: list[tuple[str, tuple[str, ...]]] = [(child, (child,))]
    seen: set[str] = set()
    while pending:
        node, path = pending.pop(0)
        if node in seen:
            continue
        seen.add(node)
        for candidate in sorted(parents.get(node, ())):
            if candidate == parent:
                return (*path, candidate)
            pending.append((candidate, (*path, candidate)))
    return ()


def build_is_a_assertion(
    *, inference_id: str, assertion_id: str, fact: Fact, input_fact_id: str,
    input_assertion_id: str, ontology_ref: OntologyRef,
    activity: ProvenanceActivity, path: tuple[str, ...],
    clinical_time: ClinicalValidTime, recorded_at,
    reasoner_version: str = "evidence-is-a/0.1.0",
) -> tuple[Inference, Assertion]:
    inference = Inference(
        inference_id=inference_id,
        input_fact_ids=(input_fact_id,),
        input_assertion_ids=(input_assertion_id,),
        ontology_ref_id=ontology_ref.ontology_ref_id,
        traversed_concept_codes=path,
        reasoner_version=reasoner_version,
    )
    assertion = Assertion(
        assertion_id=assertion_id,
        fact_id=fact.fact_id,
        origin=AssertionOrigin.INFERRED,
        value=AssertionValue.TRUE,
        clinical_time=clinical_time,
        recorded_at=recorded_at,
        producer_id=activity.producer_id,
        activity_id=activity.activity_id,
        ontology_ref_id=ontology_ref.ontology_ref_id,
        inference_id=inference_id,
    )
    return inference, assertion
