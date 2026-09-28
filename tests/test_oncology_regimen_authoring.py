from __future__ import annotations

from pathlib import Path

from javert.oncology.authoring.models import TargetKind
from javert.oncology.authoring.regimens import (
    RegimenAliasRecord,
    RegimenContextRecord,
    build_regimen_authoring_records,
    canonical_regimen_payload,
    mark_alias_ambiguity,
)
from javert.oncology.authoring.sources import build_drug_crosswalk


ROOT = Path(__file__).resolve().parents[1]


def test_existing_regimens_and_deidentified_candidates_are_projected() -> None:
    records = build_regimen_authoring_records(ROOT)
    assert len(records["revisions"]) == 4
    assert len([item for item in records["aliases"] if item.review_status == "needs_review"]) == 121
    assert all(not hasattr(item, "patient_id") for item in records["aliases"])
    assert all(item.source_corpus_checksum for item in records["aliases"] if item.review_status == "needs_review")


def test_baseline_components_do_not_cross_contaminate() -> None:
    records = build_regimen_authoring_records(ROOT)
    revision_by_name = {item.canonical_name: item.regimen_revision_id for item in records["revisions"]}
    tokens = {
        name: {
            item.token
            for item in records["components"]
            if item.regimen_revision_id == revision_id
        }
        for name, revision_id in revision_by_name.items()
    }
    assert tokens["R-CHOP"] == {"R", "C", "H", "O", "P"}
    assert tokens["CHOP"] == {"C", "H", "O", "P"}
    assert tokens["R-GemOx"] == {"R", "Gem", "Ox"}
    assert tokens["Pola-R-GemOx"] == {"Pola", "R", "Gem", "Ox"}
    assert all(item.target_kind == TargetKind.CONCEPT for item in records["components"])
    assert {item.target_id for item in records["components"]} == {
        "polatuzumab-vedotin",
        "rituximab",
        "gemcitabine",
        "oxaliplatin",
        "cyclophosphamide",
        "doxorubicin",
        "vincristine",
        "prednisone",
    }
    concept_ids = {
        item.drug_concept_id for item in build_drug_crosswalk(ROOT)[0]
    }
    assert all(item.target_id in concept_ids for item in records["components"])
    assert records["drug_classes"] == []


def test_schedule_fields_are_reserved_and_not_used_for_publish_or_inference() -> None:
    records = build_regimen_authoring_records(ROOT)
    assert records["schedules"]
    assert all(not item.publishing_enabled and not item.inference_enabled for item in records["schedules"])
    assert all(item.dose_value is None and not item.administration_days for item in records["schedules"])


def test_ambiguous_alias_without_context_is_not_selected() -> None:
    aliases = [
        RegimenAliasRecord(alias_id="a", regimen_revision_id="r1", original_alias="SYN", normalized_alias="SYN", review_status="approved"),
        RegimenAliasRecord(alias_id="b", regimen_revision_id="r2", original_alias="SYN", normalized_alias="SYN", review_status="approved"),
    ]
    contexts = [
        RegimenContextRecord(context_id="c1", regimen_revision_id="r1", cancer_context="合成癌种"),
        RegimenContextRecord(context_id="c2", regimen_revision_id="r2", cancer_context="合成癌种"),
    ]
    assert mark_alias_ambiguity(aliases, contexts) == ["SYN"]
    assert all(item.ambiguous for item in aliases)


def test_regimen_projection_is_deterministic() -> None:
    assert canonical_regimen_payload(build_regimen_authoring_records(ROOT)) == canonical_regimen_payload(build_regimen_authoring_records(ROOT))
