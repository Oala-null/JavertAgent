# -*- coding: utf-8 -*-
"""肿瘤资格共享契约和知识资产元数据测试."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from javert.oncology.contracts import (
    AuditDisposition,
    CriterionAssessment,
    CriterionState,
    EligibilityEvaluation,
    EligibilityStatus,
    ProofNode,
    project_legacy_verdict,
)
from javert.oncology.knowledge import (
    KnowledgeMetadata,
    SourceReference,
    asset_payload_checksum,
    canonical_json_bytes,
)
from scripts.build_oncology_schemas import SCHEMAS, build_schemas


def _leaf(state: CriterionState = CriterionState.UNKNOWN) -> ProofNode:
    assessment = CriterionAssessment(
        criterion_id="criterion-1",
        criterion_type="diagnosis",
        state=state,
    )
    return ProofNode(
        node_id="criterion-1",
        operator="leaf",
        state=state,
        criterion_id="criterion-1",
        criterion_type="diagnosis",
        assessment=assessment,
    )


@pytest.mark.parametrize(
    ("disposition", "verdict"),
    [
        (AuditDisposition.NO_VIOLATION_FOUND, "CLEAN"),
        (AuditDisposition.VIOLATION_FOUND, "VIOLATION"),
        (AuditDisposition.REVIEW_REQUIRED, "INCONCLUSIVE"),
    ],
)
def test_legacy_projection_is_deterministic(disposition, verdict):
    result = EligibilityEvaluation(
        audit_disposition=disposition,
        eligibility_status=EligibilityStatus.DOCUMENTATION_GAP,
        proof_tree=_leaf(),
    )
    assert result.legacy_verdict == verdict
    assert project_legacy_verdict(disposition) == verdict


def test_inconsistent_legacy_projection_is_rejected():
    with pytest.raises(ValidationError, match="投影"):
        EligibilityEvaluation(
            audit_disposition=AuditDisposition.NO_VIOLATION_FOUND,
            eligibility_status=EligibilityStatus.SATISFIED,
            legacy_verdict="VIOLATION",
            proof_tree=_leaf(CriterionState.SATISFIED),
        )


def test_proof_leaf_shape_is_validated():
    with pytest.raises(ValidationError, match="必须包含 assessment"):
        ProofNode(
            node_id="broken",
            operator="leaf",
            state=CriterionState.UNKNOWN,
        )


def test_common_metadata_requires_immutable_revision_and_valid_range():
    source = SourceReference(
        source_id="policy:test",
        title="测试医保限定",
        version="2025",
        retrieval_date=date(2026, 7, 17),
        checksum="sha256:" + "1" * 64,
    )
    metadata = KnowledgeMetadata(
        schema_version="1.0.0",
        content_version="2026.07.17",
        effective_from=date(2025, 1, 1),
        source_refs=[source],
        checksum="sha256:" + "2" * 64,
        review_status="approved",
    )
    assert metadata.review_status.value == "approved"

    with pytest.raises(ValidationError, match="不能早于"):
        metadata.model_copy(
            update={
                "effective_to": date(2024, 1, 1),
            }
        ).model_validate(
            {
                **metadata.model_dump(),
                "effective_to": "2024-01-01",
            }
        )


def test_canonical_json_and_checksum_are_byte_stable():
    left = {"b": [2, 1], "a": "肿瘤"}
    right = {"a": "肿瘤", "b": [2, 1]}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    asset = {
        "metadata": {"checksum": "sha256:" + "0" * 64, "content_version": "1"},
        "entries": [{"id": "x"}],
    }
    assert asset_payload_checksum(asset) == asset_payload_checksum(asset)


def test_json_schema_builder_is_deterministic(tmp_path: Path):
    first = build_schemas(tmp_path)
    first_bytes = {path.name: path.read_bytes() for path in first}
    second = build_schemas(tmp_path)
    assert {path.name: path.read_bytes() for path in second} == first_bytes
    assert set(first_bytes) == set(SCHEMAS)
    for raw in first_bytes.values():
        schema = json.loads(raw)
        assert "metadata" in schema["properties"]
