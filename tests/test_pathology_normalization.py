# -*- coding: utf-8 -*-
"""HER2 别名、方法隔离、阈值、时序与冲突回归."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from javert.oncology.contracts import CriterionState
from javert.oncology.pathology import (
    BiomarkerMethod,
    PathologyInput,
    evaluate_biomarker_criterion,
    load_pathology_kb,
    normalize_pathology_observation,
)
from scripts.build_oncology_eligibility_assets import write_assets


@pytest.fixture
def pathology_asset(tmp_path: Path):
    path = tmp_path / "pathology.json"
    write_assets(
        eligibility_out=tmp_path / "eligibility.json",
        pathology_out=path,
        review_out=tmp_path / "review.json",
        pathology_review_out=tmp_path / "pathology-review.json",
    )
    return load_pathology_kb(path)


@pytest.mark.parametrize(
    "alias",
    ["HER2", "HER-2", "HER2/neu", "c-erbB-2", "CerbB2"],
)
@pytest.mark.parametrize(
    ("score", "expected"),
    [
        ("0", CriterionState.NOT_SATISFIED),
        ("1+", CriterionState.NOT_SATISFIED),
        ("2+", CriterionState.SATISFIED),
        ("3+", CriterionState.SATISFIED),
    ],
)
def test_her2_aliases_and_ihc_scores(pathology_asset, alias, score, expected):
    result = evaluate_biomarker_criterion(
        criterion_id="her2",
        marker_id="HER2",
        cancer_context="尿路上皮癌",
        policy_context="disitamab-urothelial-insurance",
        expected_method=BiomarkerMethod.IHC,
        inputs=[
            PathologyInput(
                text=f"免疫组化 IHC：{alias}({score})",
                specimen_date=date(2026, 1, 1),
                locator="pathology:1",
            )
        ],
        service_date=date(2026, 2, 1),
        asset=pathology_asset,
    )
    assert result.state == expected
    assert result.normalized_facts[0].normalized_value["score"] == score


def test_erbb2_molecular_result_is_not_coerced_to_ihc(pathology_asset):
    observations = normalize_pathology_observation(
        PathologyInput(
            text="NGS检测：ERBB2 p.V777L突变",
            locator="molecular:1",
        ),
        cancer_context="尿路上皮癌",
    )
    assert observations[0].method == BiomarkerMethod.MOLECULAR
    assert observations[0].score == ""
    result = evaluate_biomarker_criterion(
        criterion_id="her2",
        marker_id="HER2",
        cancer_context="尿路上皮癌",
        policy_context="disitamab-urothelial-insurance",
        expected_method=BiomarkerMethod.IHC,
        inputs=[PathologyInput(text="NGS检测：ERBB2 p.V777L突变")],
        service_date=date(2026, 2, 1),
        asset=pathology_asset,
    )
    assert result.state == CriterionState.UNKNOWN


def test_untyped_her2_positive_stays_unknown(pathology_asset):
    result = evaluate_biomarker_criterion(
        criterion_id="her2",
        marker_id="HER2",
        cancer_context="尿路上皮癌",
        policy_context="disitamab-urothelial-insurance",
        expected_method=BiomarkerMethod.IHC,
        inputs=[PathologyInput(text="病程记录：HER2阳性")],
        service_date=date(2026, 2, 1),
        asset=pathology_asset,
    )
    assert result.state == CriterionState.UNKNOWN
    assert result.normalized_facts[0].normalized_value["method"] == ""


def test_post_service_pathology_cannot_retroactively_satisfy(pathology_asset):
    result = evaluate_biomarker_criterion(
        criterion_id="her2",
        marker_id="HER2",
        cancer_context="尿路上皮癌",
        policy_context="disitamab-urothelial-insurance",
        expected_method=BiomarkerMethod.IHC,
        inputs=[
            PathologyInput(
                text="IHC HER2(3+)",
                specimen_date=date(2026, 3, 1),
            )
        ],
        service_date=date(2026, 2, 1),
        asset=pathology_asset,
    )
    assert result.state == CriterionState.UNKNOWN
    assert "post-service pathology" in result.missing_items


def test_conflicting_pre_service_specimens_remain_conflict(pathology_asset):
    result = evaluate_biomarker_criterion(
        criterion_id="her2",
        marker_id="HER2",
        cancer_context="尿路上皮癌",
        policy_context="disitamab-urothelial-insurance",
        expected_method=BiomarkerMethod.IHC,
        inputs=[
            PathologyInput(
                text="IHC HER2(1+)",
                specimen_site="标本A",
                specimen_date=date(2026, 1, 1),
            ),
            PathologyInput(
                text="IHC HER2(3+)",
                specimen_site="标本B",
                specimen_date=date(2026, 1, 2),
            ),
        ],
        service_date=date(2026, 2, 1),
        asset=pathology_asset,
    )
    assert result.state == CriterionState.CONFLICT
    assert len(result.normalized_facts) == 2


def test_other_cancer_context_does_not_reuse_urothelial_threshold(pathology_asset):
    result = evaluate_biomarker_criterion(
        criterion_id="her2",
        marker_id="HER2",
        cancer_context="乳腺癌",
        policy_context="disitamab-urothelial-insurance",
        expected_method=BiomarkerMethod.IHC,
        inputs=[PathologyInput(text="IHC HER2(2+)")],
        service_date=date(2026, 2, 1),
        asset=pathology_asset,
    )
    assert result.state == CriterionState.UNKNOWN
    assert "完整上下文" in result.reason
