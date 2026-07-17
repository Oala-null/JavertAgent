# -*- coding: utf-8 -*-
"""方案 resolver 边界、证据优先级、时态和周期冲突金标."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from javert.oncology.regimen import (
    CancerContextStatus,
    ResolutionStatus,
    TreatmentEventStatus,
    load_regimen_kb,
    normalize_regimen_alias,
    resolve_regimen,
)
from scripts.build_oncology_regimen_kb import (
    build_regimen_asset,
    mine_regimen_candidates,
    write_assets,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "oncology"


@pytest.fixture
def regimen_asset(tmp_path: Path):
    path = tmp_path / "regimen.json"
    write_assets(
        kb_out=path,
        review_out=tmp_path / "candidates.json",
        notes_path=None,
    )
    return load_regimen_kb(path)


@pytest.mark.parametrize(
    "alias",
    ["Pola-R-GemOx", "pola-R-Gemox", "POLA R GEMOX", "Pola‑R‑GemOx"],
)
def test_format_variants_resolve_to_same_regimen(regimen_asset, alias):
    result = resolve_regimen(
        text=f"本次完成{alias}方案化疗",
        asset=regimen_asset,
        cancer_context="弥漫大B细胞淋巴瘤",
    )
    assert result.status == ResolutionStatus.RESOLVED
    assert result.regimen_id == "pola-r-gemox"
    assert {component.drug_concept_id for component in result.components} == {
        "polatuzumab-vedotin",
        "rituximab",
        "gemcitabine",
        "oxaliplatin",
    }


def test_pola_r_gemox_and_r_gemox_are_distinct(regimen_asset):
    pola = resolve_regimen(
        text="已行Pola-R-GemOx方案化疗",
        asset=regimen_asset,
        cancer_context="DLBCL",
    )
    no_pola = resolve_regimen(
        text="已行R-GemOx方案化疗",
        asset=regimen_asset,
        cancer_context="DLBCL",
    )
    assert "polatuzumab-vedotin" in {
        component.drug_concept_id for component in pola.components
    }
    assert "polatuzumab-vedotin" not in {
        component.drug_concept_id for component in no_pola.components
    }


def test_explicit_medication_evidence_precedes_regimen_inference(regimen_asset):
    result = resolve_regimen(
        text=(
            "完成Pola-R-GemOx方案化疗："
            "利妥昔单抗、优罗华、吉西他滨、奥沙利铂。"
        ),
        asset=regimen_asset,
        cancer_context="DLBCL",
    )
    assert all(component.evidence_type == "explicit" for component in result.components)
    pola = next(
        component
        for component in result.components
        if component.drug_concept_id == "polatuzumab-vedotin"
    )
    assert pola.matched_alias == "优罗华"


def test_explicit_component_conflict_is_retained(regimen_asset):
    result = resolve_regimen(
        text="完成Pola-R-GemOx方案化疗，方案组分：不含优罗华。",
        asset=regimen_asset,
        cancer_context="DLBCL",
    )
    assert "EXPLICIT_COMPONENT_NEGATED:polatuzumab-vedotin" in result.conflicts
    assert result.review_required is True
    assert result.legacy_review_verdict == "INCONCLUSIVE"


def test_planned_regimen_does_not_count_as_administered(regimen_asset):
    result = resolve_regimen(
        text="拟行Pola-R-GemOx方案",
        asset=regimen_asset,
        cancer_context="DLBCL",
    )
    assert result.event_status == TreatmentEventStatus.PLANNED


def test_historical_and_unknown_event_status(regimen_asset):
    historical = resolve_regimen(
        text="既往曾接受R-CHOP方案",
        asset=regimen_asset,
        cancer_context="DLBCL",
    )
    unknown = resolve_regimen(
        text="病程中提及R-CHOP方案",
        asset=regimen_asset,
        cancer_context="DLBCL",
    )
    assert historical.event_status == TreatmentEventStatus.HISTORICAL
    assert unknown.event_status == TreatmentEventStatus.UNKNOWN


def test_cycle_is_independent_from_line_of_therapy(regimen_asset):
    result = resolve_regimen(
        text="第四次Pola-R-GemOx方案化疗",
        asset=regimen_asset,
        cancer_context="DLBCL",
    )
    assert result.cycle_no == 4
    assert result.line_of_therapy is None
    explicit_line = resolve_regimen(
        text="二线治疗，C4 Pola-R-GemOx方案化疗",
        asset=regimen_asset,
        cancer_context="DLBCL",
    )
    assert explicit_line.cycle_no == 4
    assert explicit_line.line_of_therapy == 2


def test_fee_code_only_corroborates_and_cannot_create_regimen(regimen_asset):
    no_text = resolve_regimen(
        text="本次静脉给药",
        asset=regimen_asset,
        fee_codes=["XL01FXW129B001010181735"],
        cancer_context="DLBCL",
    )
    assert no_text.status == ResolutionStatus.NOT_FOUND
    assert no_text.components == []

    with_text = resolve_regimen(
        text="本次完成Pola-R-GemOx方案化疗",
        asset=regimen_asset,
        fee_codes=["XL01FXW129B001010181735"],
        cancer_context="DLBCL",
    )
    pola = next(
        component
        for component in with_text.components
        if component.drug_concept_id == "polatuzumab-vedotin"
    )
    assert pola.corroborating_fee_codes == ["XL01FXW129B001010181735"]


def test_missing_and_conflicting_cancer_context(regimen_asset):
    missing = resolve_regimen(
        text="已行R-GemOx方案化疗",
        asset=regimen_asset,
    )
    conflict = resolve_regimen(
        text="已行R-GemOx方案化疗",
        asset=regimen_asset,
        cancer_context="尿路上皮癌",
    )
    assert missing.cancer_context_status == CancerContextStatus.UNKNOWN
    assert missing.components
    assert conflict.status == ResolutionStatus.CONTEXT_CONFLICT
    assert conflict.cancer_context_status == CancerContextStatus.CONFLICT
    assert conflict.review_required is True
    assert conflict.components == []


def test_unreviewed_and_ambiguous_aliases_do_not_infer_components(regimen_asset):
    first = regimen_asset.entries[0]
    unreviewed_meta = first.metadata.model_copy(update={"review_status": "needs_review"})
    unreviewed = first.model_copy(update={"metadata": unreviewed_meta})
    unreviewed_asset = regimen_asset.model_copy(update={"entries": [unreviewed]})
    blocked = resolve_regimen(
        text="拟行Pola-R-GemOx方案",
        asset=unreviewed_asset,
        cancer_context="DLBCL",
    )
    assert blocked.status == ResolutionStatus.UNREVIEWED
    assert blocked.components == []

    duplicate = first.model_copy(update={"regimen_id": "pola-r-gemox-other-context"})
    ambiguous_asset = regimen_asset.model_copy(
        update={"entries": [first, duplicate, *regimen_asset.entries[1:]]}
    )
    ambiguous = resolve_regimen(
        text="拟行Pola-R-GemOx方案",
        asset=ambiguous_asset,
        cancer_context="DLBCL",
    )
    assert ambiguous.status == ResolutionStatus.AMBIGUOUS
    assert ambiguous.components == []


def test_pola_cycle_conflict_golden_resolution_requires_review(regimen_asset):
    fixture = json.loads(
        (FIXTURE_DIR / "pola_cycle_conflict.json").read_text(encoding="utf-8")
    )
    observation = fixture["treatment_observations"][0]
    result = resolve_regimen(
        text=observation["text"],
        asset=regimen_asset,
        cancer_context=fixture["cancer_context"][0],
        fee_codes=[fixture["drug_candidate"]["insurance_code"]],
        locator=observation["anchor"],
        encounter_date=date.fromisoformat(fixture["encounter_date"]),
        document_date=date.fromisoformat(observation["document_date"]),
    )
    assert result.regimen_id == "pola-r-gemox"
    assert len(result.components) == 4
    assert result.event_status == TreatmentEventStatus.ADMINISTERED
    assert result.cycle_no == 4
    assert result.line_of_therapy is None
    assert result.temporal_conflict is True
    assert result.review_required is True
    assert result.legacy_review_verdict == "INCONCLUSIVE"


def test_builder_schema_checksum_and_bytes_are_deterministic(tmp_path: Path):
    first = build_regimen_asset()
    second = build_regimen_asset()
    assert first == second
    path = tmp_path / "regimen.json"
    review = tmp_path / "review.json"
    write_assets(kb_out=path, review_out=review, notes_path=None)
    first_bytes = path.read_bytes()
    write_assets(kb_out=path, review_out=review, notes_path=None)
    assert path.read_bytes() == first_bytes
    assert load_regimen_kb(path).metadata.review_status.value == "approved"


def test_candidate_mining_filters_dose_frequency_and_never_activates(tmp_path: Path):
    notes = tmp_path / "notes.csv"
    with notes.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["patient_id", "content"])
        writer.writeheader()
        writer.writerow(
            {
                "patient_id": "SECRET-1",
                "content": "采用Pola-R-GemOx方案；剂量500 mg bid q12h。",
            }
        )
        writer.writerow(
            {
                "patient_id": "SECRET-2",
                "content": "再次采用Pola R GemOx方案；另提及FOLFOX方案和G-CHOP方案。",
            }
        )
        writer.writerow(
            {
                "patient_id": "SECRET-3",
                "content": (
                    "继续FOLFOX方案；C4-Pola-R-GemOx方案；"
                    "G-CHOP方案；TIW-D1-D14-Q3W方案；"
                    "普通英文 noise words 不属于方案。"
                ),
            }
        )
    report = mine_regimen_candidates(notes)
    raw = json.dumps(report, ensure_ascii=False)
    assert "SECRET" not in raw
    assert report["activation_policy"].startswith("frequency_never")
    assert all(item["active"] is False for item in report["candidates"])
    assert all(item["review_status"] == "needs_review" for item in report["candidates"])
    assert {item["normalized_alias"] for item in report["candidates"]} == {
        "FOLFOX",
        "G-CHOP",
        "POLA-R-GEMOX",
    }
    assert not any(
        normalize_regimen_alias("500 mg bid q12h") == item["normalized_alias"]
        for item in report["candidates"]
    )


def test_missing_corpus_produces_honest_review_artifact(tmp_path: Path):
    report = mine_regimen_candidates(tmp_path / "missing.csv")
    assert report["corpus_available"] is False
    assert report["source_status"] == "source_unavailable"
    assert report["candidates"] == []
