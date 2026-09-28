from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from javert.oncology.authoring.baseline import build_baseline
from javert.oncology.authoring.ids import (
    branch_id,
    import_batch_id,
    logical_rule_id,
    node_id,
    release_id,
    revision_id,
    source_document_id,
    source_fragment_id,
)
from javert.oncology.authoring.models import (
    AuthoringSnapshot,
    EffectiveDateBasis,
    EffectiveWindow,
    HistoricalApplicationPolicy,
    RevisionLifecycle,
    SourceDocument,
    SourceRule,
    SourceType,
    TemporalApplicability,
)
from javert.oncology.contracts import EligibilityEvaluation
from javert.oncology.eligibility import load_eligibility_rules
from javert.oncology.pathology import load_pathology_kb
from javert.oncology.regimen import load_regimen_kb
from javert.oncology.authoring.sources import build_authoring_snapshot


ROOT = Path(__file__).resolve().parents[1]


def test_stable_ids_repeat_and_ignore_dict_order() -> None:
    document = source_document_id("INSURANCE_PAYMENT", "合成来源", "2026")
    fragment = source_fragment_id(document, "row:1", "合成限定")
    logical = logical_rule_id("INSURANCE_PAYMENT", "drug-a", fragment)
    left = revision_id(logical, {"b": 2, "a": 1})
    right = revision_id(logical, {"a": 1, "b": 2})
    assert left == right
    branch = branch_id(left, "0:4", 1)
    assert node_id(branch, "0/1", {"x": 1}) == node_id(branch, "0/1", {"x": 1})
    assert release_id([left, "rev_b"], "sha256:x") == release_id(["rev_b", left], "sha256:x")
    assert import_batch_id("sha256:workbook", "1.0.0") == import_batch_id("sha256:workbook", "1.0.0")


def test_contract_schema_contains_reserved_source_and_required_enums() -> None:
    schema = SourceRule.model_json_schema()
    dumped = json.dumps(schema, ensure_ascii=False)
    for value in (
        "INSURANCE_PAYMENT",
        "GUIDELINE_INDICATION",
        "NMPA_LABEL",
        "CURRENT_FILE_ASSUMPTION",
        "APPLY_CURRENT_RELEASE_WITH_WARNING",
    ):
        assert value in dumped
    assert set(TemporalApplicability) == {
        TemporalApplicability.BEFORE_EFFECTIVE_WINDOW,
        TemporalApplicability.IN_WINDOW,
        TemporalApplicability.AFTER_EFFECTIVE_WINDOW,
    }


def test_default_window_is_frozen_and_independent_from_document_year() -> None:
    window = EffectiveWindow()
    assert window.effective_from == date(2026, 1, 1)
    assert window.effective_to == date(2027, 12, 31)
    assert window.effective_date_basis == EffectiveDateBasis.CURRENT_FILE_ASSUMPTION
    assert window.historical_application_policy == HistoricalApplicationPolicy.APPLY_CURRENT_RELEASE_WITH_WARNING
    source = SourceDocument(
        source_document_id="srcdoc_x",
        source_type=SourceType.GUIDELINE_INDICATION,
        title="合成指南",
        document_version="2025",
        document_year=2025,
        retrieval_date=date(2026, 7, 21),
        content_checksum="sha256:" + "a" * 64,
    )
    assert source.document_year == 2025
    assert window.effective_from.year == 2026


def test_expert_override_requires_reason_and_date_review_comment() -> None:
    with pytest.raises(ValidationError):
        EffectiveWindow(effective_date_basis=EffectiveDateBasis.EXPERT_OVERRIDE)


def test_current_snapshot_rejects_reserved_nmpa_label() -> None:
    label = SourceDocument(
        source_document_id="srcdoc_label",
        source_type=SourceType.NMPA_LABEL,
        title="合成说明书",
        document_version="1",
        retrieval_date=date(2026, 7, 21),
        content_checksum="sha256:" + "a" * 64,
    )
    with pytest.raises(ValidationError, match="不得生成预留 NMPA_LABEL"):
        AuthoringSnapshot(source_documents=[label], source_fragments=[], source_rules=[])


def test_current_assets_and_old_eligibility_json_remain_readable() -> None:
    eligibility = load_eligibility_rules(ROOT / "configs/oncology_eligibility_rules.json")
    pathology = load_pathology_kb(ROOT / "configs/pathology_biomarker_kb.json")
    regimen = load_regimen_kb(ROOT / "configs/oncology_regimen_kb.json")
    assert len(eligibility.entries) == 3
    assert pathology.entries
    assert len(regimen.entries) == 4

    fixture = json.loads((ROOT / "tests/fixtures/oncology/urothelial_her2_low.json").read_text())
    old_payload = fixture.get("legacy_eligibility_json")
    if old_payload:
        EligibilityEvaluation.model_validate(old_payload)


def test_baseline_is_dynamic_and_deterministic() -> None:
    first = build_baseline(ROOT)
    second = build_baseline(ROOT)
    assert first == second
    assert first["counts"]["drug_entities"] == len(
        json.loads((ROOT / "configs/oncology_drug_kb.json").read_text())["drugs"]
    )
    assert first["counts"]["insurance_restrictions"] > 0
    assert first["counts"]["guideline_indications"] > 0
    assert first["snapshot_checksum"].startswith("sha256:")


def test_source_fragments_merge_identical_text_anchors_without_losing_rules() -> None:
    snapshot = build_authoring_snapshot(ROOT)
    authority_keys = [
        (item.source_document_id, item.content_checksum)
        for item in snapshot.source_fragments
    ]
    fragment_ids = {item.source_fragment_id for item in snapshot.source_fragments}

    assert len(authority_keys) == len(set(authority_keys))
    assert all(item.source_fragment_id in fragment_ids for item in snapshot.source_rules)
    assert len(snapshot.source_rules) > len(snapshot.source_fragments)


def test_minimal_fixture_contains_only_synthetic_content() -> None:
    fixture = json.loads(
        (ROOT / "tests/fixtures/oncology_authoring/minimal_knowledge.json").read_text()
    )
    assert fixture["synthetic_only"] is True
    dumped = json.dumps(fixture, ensure_ascii=False)
    assert "synthetic" in dumped
    assert "患者" not in dumped
