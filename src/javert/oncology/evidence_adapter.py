# -*- coding: utf-8 -*-
"""现有 oncology payload 到公共 Evidence Contract 的只读引用式 adapter。"""

from __future__ import annotations

from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from javert.evidence.serialization import checksum_value

from .contracts import EligibilityEvaluation, ProofNode


class OncologyCriterionConformanceLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str
    state: str
    criterion_pointer: str
    proof_pointers: tuple[str, ...] = ()
    public_evidence_ids: tuple[str, ...] = ()
    public_fact_ids: tuple[str, ...] = ()
    evaluator_version: str = ""
    source_version: str = ""


class OncologyConformanceLinks(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authoritative_payload_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authoritative_legacy_verdict: str
    release_id: str | None = None
    source_versions: tuple[str, ...] = ()
    criterion_links: tuple[OncologyCriterionConformanceLink, ...]


def _proof_pointers(node: ProofNode, pointer: str, output: dict[str, list[str]]) -> None:
    if node.criterion_id:
        output.setdefault(node.criterion_id, []).append(pointer)
    for index, child in enumerate(node.children):
        _proof_pointers(child, f"{pointer}/children/{index}", output)


def build_oncology_conformance_links(
    evaluation: EligibilityEvaluation,
    *,
    evidence_refs: Mapping[tuple[str, int], str] | None = None,
    fact_refs: Mapping[tuple[str, int], str] | None = None,
) -> OncologyConformanceLinks:
    """只产生引用，不改写或重建 EligibilityEvaluation。"""
    evidence_refs = evidence_refs or {}
    fact_refs = fact_refs or {}
    links = []
    snapshots = [("", evaluation)] + [
        (f"/scope_evaluations/{index}", scope)
        for index, scope in enumerate(evaluation.scope_evaluations)
    ]
    for prefix, snapshot in snapshots:
        proof_by_criterion: dict[str, list[str]] = {}
        _proof_pointers(snapshot.proof_tree, f"{prefix}/proof_tree", proof_by_criterion)
        for index, assessment in enumerate(snapshot.criterion_assessments):
            links.append(OncologyCriterionConformanceLink(
                criterion_id=assessment.criterion_id,
                state=assessment.state.value,
                criterion_pointer=f"{prefix}/criterion_assessments/{index}",
                proof_pointers=tuple(sorted(proof_by_criterion.get(assessment.criterion_id, ()))),
                public_evidence_ids=tuple(
                    evidence_refs[(assessment.criterion_id, anchor_index)]
                    for anchor_index, _ in enumerate(assessment.evidence_anchors)
                    if (assessment.criterion_id, anchor_index) in evidence_refs
                ),
                public_fact_ids=tuple(
                    fact_refs[(assessment.criterion_id, fact_index)]
                    for fact_index, _ in enumerate(assessment.normalized_facts)
                    if (assessment.criterion_id, fact_index) in fact_refs
                ),
                evaluator_version=assessment.evaluator_version,
                source_version=assessment.source_version,
            ))
    return OncologyConformanceLinks(
        authoritative_payload_checksum=checksum_value(evaluation.model_dump(mode="json")),
        authoritative_legacy_verdict=evaluation.legacy_verdict or "",
        release_id=evaluation.release_id,
        source_versions=tuple(evaluation.source_versions),
        criterion_links=tuple(links),
    )
