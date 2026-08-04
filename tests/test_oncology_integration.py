# -*- coding: utf-8 -*-
"""RD04/R007 所有权、净收费、Runner 与三例金标端到端回归."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from javert.audit.rule import Rule
from javert.audit.runner import Runner
from javert.config import JavertConfig
from javert.data.loader import DataLoader
from javert.oncology.guidance import TRANSPLANT_SUGGESTION_TEXT
from javert.oncology.knowledge import asset_payload_checksum
from javert.store.audit_store import SqliteStore
from javert.tools import drug_audit_lookup as dal
from javert.tools.tool_executor import ToolExecutor
from javert.web.api.routes_audit import _result_payload
from scripts.oncology_shadow_report import build_direct_report, build_report


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "oncology"
ELIGIBILITY = ROOT / "configs" / "oncology_eligibility_rules.json"
PATHOLOGY = ROOT / "configs" / "pathology_biomarker_kb.json"
REGIMEN = ROOT / "configs" / "oncology_regimen_kb.json"
DRUG_KB = ROOT / "configs" / "drug_audit_kb.json"


class StubLoader(DataLoader):
    def __init__(self, fees: pd.DataFrame, notes: pd.DataFrame):
        self.fees = fees
        self.notes = notes

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        return self.notes

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return self.fees

    def all_notes(self) -> pd.DataFrame:
        return self.notes

    def all_fees(self) -> pd.DataFrame:
        return self.fees


class FakeProvider:
    model_name = "deterministic-test"

    def __init__(self, contents: list[str]):
        self.contents = list(contents)

    def chat_with_retry(self, messages, **kwargs):
        return {
            "content": self.contents.pop(0),
            "reasoning_content": "",
            "usage": None,
            "raw_response": {},
        }


def _rule() -> Rule:
    return Rule(
        rule_id="RD04",
        domain="药品",
        violation_type="超医保限定支付适应症用药",
        question="肿瘤药超医保限定支付",
        status="abandoned",
        priority="P1",
        drug_rule_type="限适应症",
    )


def _write_zd(path: Path, patient_id: str, diagnoses: list[str]) -> Path:
    pd.DataFrame(
        {
            "ba_id": [f"H-{patient_id}"] * len(diagnoses),
            "diag_name": diagnoses,
            "diag_code": [f"D{i}" for i in range(len(diagnoses))],
            "maindiag_flag": ["1", *(["0"] * (len(diagnoses) - 1))],
        }
    ).to_csv(path, index=False)
    return path


def _golden_loader(
    fixture_name: str,
    tmp_path: Path,
) -> tuple[str, StubLoader, Path, Path]:
    fixture = json.loads((FIXTURES / f"{fixture_name}.json").read_text(encoding="utf-8"))
    patient_id = f"GOLDEN-{fixture_name.upper().replace('_', '-')}"
    drug = fixture["drug_candidate"]
    fee = pd.DataFrame(
        {
            "bah": [f"H-{patient_id}"],
            "fee_ocur_time": [
                fixture.get("service_date") or fixture.get("encounter_date")
            ],
            "cnt": [drug["net_quantity"]],
            "medins_list_name": [drug["generic_name"]],
            "medins_chrgitm_type": ["西药"],
            "med_list_codg": [drug["insurance_code"]],
        }
    )
    note_rows: list[dict[str, str]] = []
    if fixture_name == "urothelial_her2_low":
        item = fixture["pathology_observations"][0]
        note_rows.append(
            {
                "住院号": patient_id,
                "事件时间": item["specimen_date"],
                "子阶段": item["document_type"],
                "内容": item["text"],
            }
        )
    elif fixture_name == "pola_cycle_conflict":
        item = fixture["treatment_observations"][0]
        note_rows.append(
            {
                "住院号": patient_id,
                "事件时间": item["document_date"],
                "子阶段": item["document_type"],
                "内容": item["text"],
            }
        )
    else:
        note_rows.append(
            {
                "住院号": patient_id,
                "事件时间": fixture["service_date"],
                "子阶段": "病程记录",
                "内容": "患者74岁，弥漫大B细胞淋巴瘤，既往多线治疗，治疗后疾病进展。",
            }
        )
    notes = pd.DataFrame(note_rows)
    zd = _write_zd(
        tmp_path / f"{fixture_name}-zd.csv",
        patient_id,
        fixture["cancer_context"],
    )
    kb_path = DRUG_KB
    if fixture_name == "urothelial_her2_low":
        # 金标码已去标识；只在临时 KB 将该实体码替换为 fixture 码，不修改生产资产。
        kb = json.loads(DRUG_KB.read_text(encoding="utf-8"))
        kb["drugs"][drug["generic_name"]]["codes"] = [drug["insurance_code"]]
        kb_path = tmp_path / "golden-drug-kb.json"
        kb_path.write_text(json.dumps(kb, ensure_ascii=False), encoding="utf-8")
    return patient_id, StubLoader(fee, notes), zd, kb_path


def _lookup(
    patient_id: str,
    loader: StubLoader,
    zd: Path,
    kb: Path,
    *,
    mode: str = "on",
    rule_id: str = "RD04",
    eligibility: Path = ELIGIBILITY,
) -> dict:
    return dal.lookup_patient_drugs(
        patient_id,
        loader,
        kb,
        zd,
        rule_type="限适应症",
        source_type="insurance" if rule_id == "RD04" else None,
        oncology_v2_mode=mode,
        audit_rule_id=rule_id,
        eligibility_path=eligibility,
        pathology_path=PATHOLOGY,
        regimen_path=REGIMEN,
    )


def _published_release_eligibility(
    tmp_path: Path,
    *,
    dual_scope: bool = False,
    guideline_effective_to: str | None = None,
) -> Path:
    raw = json.loads(ELIGIBILITY.read_text(encoding="utf-8"))
    raw["metadata"].update(
        {
            "schema_version": "2.0.0",
            "content_version": "release_synthetic",
            "release_id": "release_synthetic",
            "release_status": "published",
            "source_snapshot_checksum": "sha256:" + "1" * 64,
        }
    )
    for index, entry in enumerate(raw["entries"]):
        entry["metadata"].update(
            {
                "content_version": "release_synthetic",
                "release_id": "release_synthetic",
                "rule_revision_id": f"rev-{index}",
                "policy_scope": "INSURANCE_PAYMENT",
            }
        )
        for ref_index, source_ref in enumerate(entry["metadata"]["source_refs"]):
            source_ref["source_fragment_id"] = (
                f"fragment-insurance-{index}-{ref_index}"
            )
    if dual_scope:
        guideline_entries = []
        for index, entry in enumerate(raw["entries"]):
            duplicate = json.loads(json.dumps(entry, ensure_ascii=False))
            duplicate["rule_id"] = f"{entry['rule_id']}-guideline"
            duplicate["indication_branch_id"] = f"{entry['indication_branch_id']}-guideline"
            duplicate["metadata"]["rule_revision_id"] = f"rev-guideline-{index}"
            duplicate["metadata"]["policy_scope"] = "GUIDELINE_INDICATION"
            if guideline_effective_to is not None:
                duplicate["metadata"]["effective_to"] = guideline_effective_to
            for ref_index, source_ref in enumerate(
                duplicate["metadata"]["source_refs"]
            ):
                source_ref["source_fragment_id"] = (
                    f"fragment-guideline-{index}-{ref_index}"
                )
            guideline_entries.append(duplicate)
        raw["entries"].extend(guideline_entries)
    raw["metadata"]["revision_ids"] = [
        entry["metadata"]["rule_revision_id"] for entry in raw["entries"]
    ]
    raw["metadata"]["checksum"] = asset_payload_checksum(raw)
    target = tmp_path / "published-eligibility.json"
    target.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return target


@pytest.mark.parametrize(
    ("fixture_name", "legacy", "status"),
    [
        ("urothelial_her2_low", "VIOLATION", "NOT_SATISFIED"),
        ("pola_cycle_conflict", "INCONCLUSIVE", "DOCUMENTATION_GAP"),
        ("pola_transplant_gap", "CLEAN", "DOCUMENTATION_GAP"),
    ],
)
def test_golden_lookup_structured_results(
    fixture_name: str,
    legacy: str,
    status: str,
    tmp_path: Path,
):
    patient_id, loader, zd, kb = _golden_loader(fixture_name, tmp_path)
    result = _lookup(patient_id, loader, zd, kb)
    structured = result["oncology_structured"]
    evaluation = structured["selected_eligibility_evaluation"]
    assert evaluation["legacy_verdict"] == legacy
    assert evaluation["eligibility_status"] == status
    assert result["matches"][0]["net_quantity"] == 1

    if fixture_name == "urothelial_her2_low":
        states = {
            item["criterion_id"]: item["state"]
            for item in evaluation["criterion_assessments"]
        }
        assert states["urothelial-her2-overexpression"] == "NOT_SATISFIED"
        assert states["urothelial-prior-platinum"] == "UNKNOWN"
    elif fixture_name == "pola_cycle_conflict":
        regimen = structured["candidate_evaluations"][0]["regimen_evidence"][0]
        assert {item["drug_concept_id"] for item in regimen["components"]} == {
            "polatuzumab-vedotin",
            "rituximab",
            "gemcitabine",
            "oxaliplatin",
        }
        assert regimen["cycle_no"] == 4
        assert regimen["line_of_therapy"] is None
        assert regimen["temporal_conflict"] is True
        assert "治疗叙述年份与就诊/收费年份冲突" in evaluation["data_quality_flags"]
    else:
        assert evaluation["audit_disposition"] == "NO_VIOLATION_FOUND"
        assert evaluation["documentation_suggestions"][0]["suggested_content"] == (
            TRANSPLANT_SUGGESTION_TEXT
        )


@pytest.mark.parametrize(
    ("service_date", "expected_temporal", "expected_legacy", "warning"),
    [
        ("2025-06-18", "BEFORE_EFFECTIVE_WINDOW", "VIOLATION", True),
        ("2026-06-18", "IN_WINDOW", "VIOLATION", False),
        ("2028-01-01", "AFTER_EFFECTIVE_WINDOW", "INCONCLUSIVE", True),
    ],
)
def test_published_release_non_symmetric_temporal_goldens(
    tmp_path: Path,
    service_date: str,
    expected_temporal: str,
    expected_legacy: str,
    warning: bool,
) -> None:
    patient_id, loader, zd, kb = _golden_loader("urothelial_her2_low", tmp_path)
    loader.fees["fee_ocur_time"] = service_date
    loader.notes["事件时间"] = service_date
    result = _lookup(
        patient_id,
        loader,
        zd,
        kb,
        eligibility=_published_release_eligibility(tmp_path),
    )
    evaluation = result["oncology_structured"]["selected_eligibility_evaluation"]
    assert evaluation["release_id"] == "release_synthetic"
    assert evaluation["temporal_applicability"] == expected_temporal
    assert evaluation["legacy_verdict"] == expected_legacy
    assert bool(evaluation["temporal_warning"]) is warning
    if expected_temporal == "BEFORE_EFFECTIVE_WINDOW":
        assert evaluation["effective_date_enforced"] is False
    elif expected_temporal == "AFTER_EFFECTIVE_WINDOW":
        assert "AFTER_EFFECTIVE_WINDOW" in evaluation["data_quality_flags"]


def test_published_release_returns_one_status_per_policy_scope(tmp_path: Path) -> None:
    patient_id, loader, zd, kb = _golden_loader("urothelial_her2_low", tmp_path)
    result = _lookup(
        patient_id,
        loader,
        zd,
        kb,
        eligibility=_published_release_eligibility(tmp_path, dual_scope=True),
    )
    candidate = result["oncology_structured"]["candidate_evaluations"][0]
    evaluations = candidate["eligibility_evaluations"]
    assert {item["policy_scope"] for item in evaluations} == {
        "INSURANCE_PAYMENT",
        "GUIDELINE_INDICATION",
    }
    assert len(evaluations) == 2
    assert all(item["release_id"] == "release_synthetic" for item in evaluations)
    assert {item["policy_scope_display_label"] for item in evaluations} == {
        "医保支付限定",
        "指南适应证",
    }
    selected = result["oncology_structured"]["selected_eligibility_evaluation"]
    assert selected["legacy_verdict"] == "VIOLATION"
    scope_evaluations = selected["scope_evaluations"]
    assert len(scope_evaluations) == 2
    assert len(
        {
            (item["drug_concept_id"], item["policy_scope"])
            for item in scope_evaluations
        }
    ) == 2
    by_scope = {item["policy_scope"]: item for item in scope_evaluations}
    assert all(item["criterion_assessments"] for item in scope_evaluations)
    assert all(item["proof_tree"] for item in scope_evaluations)
    assert all(item["source_fragment_ids"] for item in scope_evaluations)
    assert all(
        fragment.startswith("fragment-insurance-")
        for fragment in by_scope["INSURANCE_PAYMENT"]["source_fragment_ids"]
    )
    assert all(
        fragment.startswith("fragment-guideline-")
        for fragment in by_scope["GUIDELINE_INDICATION"]["source_fragment_ids"]
    )
    assert all(item["scope_evaluations"] == [] for item in evaluations)


def test_dual_scope_uses_independent_offset_windows(tmp_path: Path) -> None:
    patient_id, loader, zd, kb = _golden_loader("urothelial_her2_low", tmp_path)
    loader.fees["fee_ocur_time"] = "2027-06-18"
    loader.notes["事件时间"] = "2027-06-18"
    result = _lookup(
        patient_id,
        loader,
        zd,
        kb,
        eligibility=_published_release_eligibility(
            tmp_path,
            dual_scope=True,
            guideline_effective_to="2026-12-31",
        ),
    )
    selected = result["oncology_structured"]["selected_eligibility_evaluation"]
    by_scope = {
        item["policy_scope"]: item for item in selected["scope_evaluations"]
    }

    insurance = by_scope["INSURANCE_PAYMENT"]
    assert insurance["temporal_applicability"] == "IN_WINDOW"
    assert insurance["legacy_verdict"] == "VIOLATION"
    assert insurance["temporal_warning"] == ""

    guideline = by_scope["GUIDELINE_INDICATION"]
    assert guideline["temporal_applicability"] == "AFTER_EFFECTIVE_WINDOW"
    assert guideline["audit_disposition"] == "REVIEW_REQUIRED"
    assert guideline["legacy_verdict"] == "INCONCLUSIVE"
    assert guideline["temporal_warning"]
    assert "AFTER_EFFECTIVE_WINDOW" in guideline["data_quality_flags"]

    # 只有指南 scope 过期；医保 scope 的明确不符合仍是最严重旧三态投影。
    assert selected["audit_disposition"] == "VIOLATION_FOUND"
    assert selected["legacy_verdict"] == "VIOLATION"


def test_differential_template_does_not_create_pola_status_conflict(tmp_path: Path):
    patient_id, loader, zd, kb = _golden_loader("pola_transplant_gap", tmp_path)
    loader.notes = pd.DataFrame(
        [
            {
                "住院号": patient_id,
                "事件时间": "2026-04-01",
                "子阶段": "鉴别诊断",
                "内容": "感染性淋巴结炎通常激素治疗有效，当前患者已排除。",
            },
            {
                "住院号": patient_id,
                "事件时间": "2026-05-01",
                "子阶段": "治疗经过",
                "内容": (
                    "患者74岁，弥漫大B细胞淋巴瘤，已行多次R-CHOP方案化疗，"
                    "此次淋巴结进行性增大，考虑疾病进展。"
                ),
            },
        ]
    )

    result = _lookup(patient_id, loader, zd, kb)
    candidate = result["oncology_structured"]["candidate_evaluations"][0]
    selected = candidate["selected_eligibility_evaluation"]
    assert selected["indication_branch_id"] == (
        "dlbcl-relapsed-refractory-transplant-ineligible"
    )
    assert selected["legacy_verdict"] == "CLEAN"
    assert selected["eligibility_status"] == "DOCUMENTATION_GAP"

    by_branch = {
        item["indication_branch_id"]: item
        for item in candidate["eligibility_evaluations"]
    }
    rr_states = {
        item["criterion_id"]: item["state"]
        for item in by_branch[
            "dlbcl-relapsed-refractory-transplant-ineligible"
        ]["criterion_assessments"]
    }
    untreated_states = {
        item["criterion_id"]: item["state"]
        for item in by_branch["dlbcl-untreated"]["criterion_assessments"]
    }
    assert rr_states["pola-refractory"] == "SATISFIED"
    assert rr_states["pola-transplant-ineligible"] == "UNKNOWN"
    assert untreated_states["pola-no-prior-treatment"] == "NOT_SATISFIED"


def test_refund_netting_prevents_regimen_from_creating_candidate(tmp_path: Path):
    patient_id = "GOLDEN-NETTING"
    fees = pd.DataFrame(
        {
            "bah": [f"H-{patient_id}", f"H-{patient_id}"],
            "fee_ocur_time": ["2026-03-12", "2026-03-12"],
            "cnt": [1, -1],
            "medins_list_name": ["注射用维泊妥珠单抗"] * 2,
            "medins_chrgitm_type": ["西药", "西药"],
            "med_list_codg": ["XL01FXW129B001010181735"] * 2,
        }
    )
    notes = pd.DataFrame(
        {
            "住院号": [patient_id],
            "事件时间": ["2026-03-12"],
            "子阶段": ["治疗记录"],
            "内容": ["完成Pola-R-GemOx方案化疗"],
        }
    )
    loader = StubLoader(fees, notes)
    zd = _write_zd(tmp_path / "zd.csv", patient_id, ["弥漫大B细胞淋巴瘤"])
    result = _lookup(patient_id, loader, zd, DRUG_KB)
    assert result["matches"] == []
    assert result["oncology_structured"]["no_candidate"] is True
    assert result["oncology_structured"]["candidate_evaluations"] == []


def test_partial_refund_keeps_candidate_and_reports_net_quantity(tmp_path: Path):
    patient_id = "GOLDEN-PARTIAL-REFUND"
    fees = pd.DataFrame(
        {
            "bah": [f"H-{patient_id}"] * 2,
            "fee_ocur_time": ["2026-05-09"] * 2,
            "cnt": [2, -1],
            "medins_list_name": ["注射用维泊妥珠单抗"] * 2,
            "medins_chrgitm_type": ["西药", "西药"],
            "med_list_codg": ["XL01FXW129B001010181735"] * 2,
        }
    )
    loader = StubLoader(fees, pd.DataFrame())
    zd = _write_zd(tmp_path / "zd.csv", patient_id, ["弥漫大B细胞淋巴瘤"])
    result = _lookup(patient_id, loader, zd, DRUG_KB)
    assert len(result["matches"]) == 1
    assert result["matches"][0]["net_quantity"] == 1
    assert result["oncology_structured"]["candidate_evaluations"][0]["net_quantity"] == 1


def test_candidate_service_date_ignores_unrelated_earlier_fee(tmp_path: Path):
    patient_id, loader, zd, kb = _golden_loader(
        "urothelial_her2_low", tmp_path
    )
    loader.fees = pd.concat(
        [
            pd.DataFrame(
                {
                    "bah": [f"H-{patient_id}"],
                    "fee_ocur_time": ["2020-01-01"],
                    "cnt": [1],
                    "medins_list_name": ["普通诊察费"],
                    "medins_chrgitm_type": ["诊疗"],
                    "med_list_codg": ["UNRELATED"],
                }
            ),
            loader.fees,
        ],
        ignore_index=True,
    )
    result = _lookup(patient_id, loader, zd, kb)
    candidate = result["oncology_structured"]["candidate_evaluations"][0]
    assert candidate["service_date"] == "2026-06-18"
    assert result["oncology_structured"]["selected_eligibility_evaluation"][
        "legacy_verdict"
    ] == "VIOLATION"


def test_missing_candidate_service_date_requires_review(tmp_path: Path):
    patient_id, loader, zd, kb = _golden_loader(
        "urothelial_her2_low", tmp_path
    )
    loader.fees["fee_ocur_time"] = ""
    result = _lookup(patient_id, loader, zd, kb)
    evaluation = result["oncology_structured"]["selected_eligibility_evaluation"]
    assert evaluation["legacy_verdict"] == "INCONCLUSIVE"
    assert "MISSING_CANDIDATE_SERVICE_DATE" in evaluation["data_quality_flags"]


def test_multiple_candidates_use_most_severe_disposition(tmp_path: Path):
    patient_id, loader, zd, kb = _golden_loader(
        "urothelial_her2_low", tmp_path
    )
    loader.fees = pd.concat(
        [
            loader.fees,
            pd.DataFrame(
                {
                    "bah": [f"H-{patient_id}"],
                    "fee_ocur_time": ["2026-06-18"],
                    "cnt": [1],
                    "medins_list_name": ["注射用维泊妥珠单抗"],
                    "medins_chrgitm_type": ["西药"],
                    "med_list_codg": ["XL01FXW129B001010181735"],
                }
            ),
        ],
        ignore_index=True,
    )
    result = _lookup(patient_id, loader, zd, kb)
    evaluations = result["oncology_structured"]["candidate_evaluations"]
    assert len(evaluations) == 2
    assert result["oncology_structured"]["selected_eligibility_evaluation"][
        "legacy_verdict"
    ] == "VIOLATION"


def test_self_pay_note_excludes_candidate_from_violation(tmp_path: Path):
    patient_id, loader, zd, kb = _golden_loader(
        "urothelial_her2_low", tmp_path
    )
    loader.notes = pd.concat(
        [
            loader.notes,
            pd.DataFrame(
                {
                    "住院号": [patient_id],
                    "事件时间": ["2026-06-10"],
                    "子阶段": ["自费药品使用同意书"],
                    "内容": ["自费药品名称：注射用维迪西妥单抗。患者同意自费使用。"],
                }
            ),
        ],
        ignore_index=True,
    )
    result = _lookup(patient_id, loader, zd, kb)
    evaluation = result["oncology_structured"]["selected_eligibility_evaluation"]
    assert evaluation["legacy_verdict"] == "CLEAN"
    assert "SELF_PAY_EXCLUDED" in evaluation["data_quality_flags"]
    assert evaluation["scope_evaluations"] == []


@pytest.mark.parametrize(
    (
        "service_date",
        "guideline_effective_to",
        "guideline_legacy",
        "guideline_temporal",
        "top_legacy",
    ),
    [
        ("2026-06-18", None, "VIOLATION", "IN_WINDOW", "VIOLATION"),
        (
            "2027-06-18",
            "2026-12-31",
            "INCONCLUSIVE",
            "AFTER_EFFECTIVE_WINDOW",
            "INCONCLUSIVE",
        ),
    ],
)
def test_published_dual_scope_self_pay_only_short_circuits_insurance(
    tmp_path: Path,
    service_date: str,
    guideline_effective_to: str | None,
    guideline_legacy: str,
    guideline_temporal: str,
    top_legacy: str,
) -> None:
    patient_id, loader, zd, kb = _golden_loader("urothelial_her2_low", tmp_path)
    loader.fees["fee_ocur_time"] = service_date
    loader.notes["事件时间"] = service_date
    loader.notes = pd.concat(
        [
            loader.notes,
            pd.DataFrame(
                {
                    "住院号": [patient_id],
                    "事件时间": [service_date],
                    "子阶段": ["自费药品使用同意书"],
                    "内容": [
                        "自费药品名称：注射用维迪西妥单抗。患者同意自费使用。"
                    ],
                }
            ),
        ],
        ignore_index=True,
    )
    result = _lookup(
        patient_id,
        loader,
        zd,
        kb,
        eligibility=_published_release_eligibility(
            tmp_path,
            dual_scope=True,
            guideline_effective_to=guideline_effective_to,
        ),
    )
    selected = result["oncology_structured"]["selected_eligibility_evaluation"]
    by_scope = {
        item["policy_scope"]: item for item in selected["scope_evaluations"]
    }

    insurance = by_scope["INSURANCE_PAYMENT"]
    assert insurance["legacy_verdict"] == "CLEAN"
    assert insurance["rule_id"] == "payer-scope-self-pay"
    assert "SELF_PAY_EXCLUDED" in insurance["data_quality_flags"]

    guideline = by_scope["GUIDELINE_INDICATION"]
    assert guideline["legacy_verdict"] == guideline_legacy
    assert guideline["temporal_applicability"] == guideline_temporal
    assert guideline["rule_id"] != "payer-scope-self-pay"
    assert "SELF_PAY_EXCLUDED" not in guideline["data_quality_flags"]
    assert selected["legacy_verdict"] == top_legacy


def test_uncertain_self_pay_note_does_not_exclude_violation(tmp_path: Path):
    patient_id, loader, zd, kb = _golden_loader(
        "urothelial_her2_low", tmp_path
    )
    loader.notes = pd.concat(
        [
            loader.notes,
            pd.DataFrame(
                {
                    "住院号": [patient_id],
                    "事件时间": ["2026-06-10"],
                    "子阶段": ["费用说明"],
                    "内容": ["注射用维迪西妥单抗是否自费待确认。"],
                }
            ),
        ],
        ignore_index=True,
    )
    result = _lookup(patient_id, loader, zd, kb)
    evaluation = result["oncology_structured"]["selected_eligibility_evaluation"]
    assert evaluation["legacy_verdict"] == "VIOLATION"
    assert "SELF_PAY_EXCLUDED" not in evaluation["data_quality_flags"]


def test_rd04_r007_on_ownership_is_disjoint_and_off_is_compatible(tmp_path: Path):
    patient_id = "GOLDEN-OWNERSHIP"
    fees = pd.DataFrame(
        {
            "bah": [f"H-{patient_id}"] * 2,
            "fee_ocur_time": ["2026-05-09"] * 2,
            "cnt": [1, 1],
            "medins_list_name": ["注射用维泊妥珠单抗", "人血白蛋白"],
            "medins_chrgitm_type": ["西药", "西药"],
            "med_list_codg": [
                "XL01FXW129B001010181735",
                "XB05AAR021B001010100348",
            ],
        }
    )
    loader = StubLoader(fees, pd.DataFrame())
    zd = _write_zd(tmp_path / "zd.csv", patient_id, ["弥漫大B细胞淋巴瘤"])
    rd04 = _lookup(patient_id, loader, zd, DRUG_KB, rule_id="RD04")
    r007_on = _lookup(patient_id, loader, zd, DRUG_KB, rule_id="R007")
    r007_off = _lookup(
        patient_id, loader, zd, DRUG_KB, mode="off", rule_id="R007"
    )
    assert {item["generic_name"] for item in rd04["matches"]} == {
        "注射用维泊妥珠单抗"
    }
    assert {item["generic_name"] for item in r007_on["matches"]} == {"人血白蛋白"}
    assert {item["generic_name"] for item in r007_off["matches"]} == {
        "注射用维泊妥珠单抗",
        "人血白蛋白",
    }
    assert not (
        {item["ownership_key"] for item in rd04["matches"]}
        & {item["ownership_key"] for item in r007_on["matches"]}
    )


def _runner(
    *,
    tmp_path: Path,
    patient_id: str,
    loader: StubLoader,
    zd: Path,
    kb: Path,
    mode: str,
    contents: list[str],
    eligibility: Path = ELIGIBILITY,
) -> Runner:
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    (prompts / "base.txt").write_text("you are auditor.", encoding="utf-8")
    cfg = JavertConfig(
        prompts_dir=str(prompts),
        data_dir=str(tmp_path),
        zd_file=zd.name,
        oncology_eligibility_v2=mode,
        verdict_gate="on",
        sql_enabled=False,
        audit_db=str(tmp_path / "audit.sqlite"),
    )
    executor = ToolExecutor()
    executor.register(
        "drug_audit_lookup",
        dal.create_executor(
            loader,
            kb,
            zd,
            oncology_v2_mode=mode,
            eligibility_path=eligibility,
            pathology_path=PATHOLOGY,
            regimen_path=REGIMEN,
        ),
        description=dal.DESCRIPTION,
        requires_patient_id=True,
    )
    return Runner(
        executor=executor,
        provider=FakeProvider(contents),
        config=cfg,
        loader=loader,
    )


@pytest.mark.parametrize(
    ("fixture_name", "expected"),
    [
        ("urothelial_her2_low", "VIOLATION"),
        ("pola_cycle_conflict", "INCONCLUSIVE"),
        ("pola_transplant_gap", "CLEAN"),
    ],
)
def test_golden_runner_store_and_api_path(
    fixture_name: str,
    expected: str,
    tmp_path: Path,
):
    patient_id, loader, zd, kb = _golden_loader(fixture_name, tmp_path)
    runner = _runner(
        tmp_path=tmp_path,
        patient_id=patient_id,
        loader=loader,
        zd=zd,
        kb=kb,
        mode="on",
        contents=[
            '```json\n{"verdict":"CLEAN","confidence":0.9,"reasoning":"legacy",'
            '"evidence":[]}\n```'
        ],
    )
    result = runner.audit(_rule(), patient_id)
    assert result.verdict == expected
    assert result.eligibility_evaluation is not None
    assert result.gate_tag == ""
    assert result.tool_calls[0].structured_output is not None

    store = SqliteStore(tmp_path / "result.sqlite")
    store.init_schema()
    store.write(result)
    loaded = store.find_by_run_id(result.run_id)
    assert loaded is not None
    assert loaded.eligibility_evaluation == result.eligibility_evaluation
    assert _result_payload(loaded)["eligibility_evaluation"]["legacy_verdict"] == expected
    store.close()


def test_dual_scope_runner_persists_all_scopes_from_release_metadata(
    tmp_path: Path,
) -> None:
    patient_id, loader, zd, kb = _golden_loader("urothelial_her2_low", tmp_path)
    runner = _runner(
        tmp_path=tmp_path,
        patient_id=patient_id,
        loader=loader,
        zd=zd,
        kb=kb,
        mode="on",
        contents=[
            '```json\n{"verdict":"CLEAN","confidence":0.9,"reasoning":"legacy",'
            '"evidence":[]}\n```'
        ],
        eligibility=_published_release_eligibility(tmp_path, dual_scope=True),
    )
    result = runner.audit(_rule(), patient_id)
    assert result.eligibility_evaluation is not None
    scopes = result.eligibility_evaluation.scope_evaluations
    assert {(item.drug_concept_id, item.policy_scope) for item in scopes} == {
        ("disitamab-vedotin", "INSURANCE_PAYMENT"),
        ("disitamab-vedotin", "GUIDELINE_INDICATION"),
    }
    assert all(item.source_fragment_ids for item in scopes)
    assert result.verdict == "VIOLATION"
    assert "[医保支付限定]" in result.reasoning
    assert "[指南适应证]" in result.reasoning
    assert "法定说明书" not in result.reasoning

    store = SqliteStore(tmp_path / "dual-runner.sqlite")
    store.init_schema()
    store.write(result)
    loaded = store.find_by_run_id(result.run_id)
    store.close()
    assert loaded is not None and loaded.eligibility_evaluation is not None
    assert len(loaded.eligibility_evaluation.scope_evaluations) == 2
    assert len(
        _result_payload(loaded)["eligibility_evaluation"]["scope_evaluations"]
    ) == 2


def test_shadow_persists_comparison_without_changing_legacy_verdict(tmp_path: Path):
    patient_id, loader, zd, kb = _golden_loader(
        "urothelial_her2_low", tmp_path
    )
    runner = _runner(
        tmp_path=tmp_path,
        patient_id=patient_id,
        loader=loader,
        zd=zd,
        kb=kb,
        mode="shadow",
        contents=[
            (
                '<tool_call>{"name":"drug_audit_lookup","arguments":'
                '{"rule_type":"限适应症","source_type":"insurance"}}</tool_call>'
            ),
            '```json\n{"verdict":"CLEAN","confidence":0.9,"reasoning":"legacy",'
            '"evidence":[]}\n```',
        ],
    )
    result = runner.audit(_rule(), patient_id)
    assert result.verdict == "CLEAN"
    assert result.eligibility_evaluation is None
    shadow = result.tool_calls[0].structured_output
    assert shadow is not None
    assert shadow["mode"] == "shadow"
    assert shadow["selected_eligibility_evaluation"]["legacy_verdict"] == "VIOLATION"
    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].structured_output is not None
    assert result.tool_calls[0].result


def test_on_mode_no_candidate_short_circuits_without_llm(tmp_path: Path):
    patient_id = "GOLDEN-NO-CANDIDATE"
    loader = StubLoader(
        pd.DataFrame(
            {
                "bah": [f"H-{patient_id}"],
                "fee_ocur_time": ["2026-01-01"],
                "cnt": [1],
                "medins_list_name": ["普通诊察费"],
                "medins_chrgitm_type": ["诊疗"],
                "med_list_codg": ["UNRELATED"],
            }
        ),
        pd.DataFrame(),
    )
    zd = _write_zd(tmp_path / "zd.csv", patient_id, ["弥漫大B细胞淋巴瘤"])
    runner = _runner(
        tmp_path=tmp_path,
        patient_id=patient_id,
        loader=loader,
        zd=zd,
        kb=DRUG_KB,
        mode="on",
        contents=[],
    )
    result = runner.audit(_rule(), patient_id)
    assert result.verdict == "CLEAN"
    assert result.eligibility_evaluation is None
    assert result.tool_calls[0].structured_output["no_candidate"] is True


def test_shadow_report_is_deidentified_and_counts_duplicate_ownership():
    evaluation = {
        "legacy_verdict": "VIOLATION",
        "audit_disposition": "VIOLATION_FOUND",
        "eligibility_status": "NOT_SATISFIED",
        "criterion_assessments": [
            {"criterion_id": "her2", "state": "NOT_SATISFIED"}
        ],
        "proof_tree": {"decisive_child_ids": ["her2"]},
        "documentation_suggestions": [],
        "data_quality_flags": [],
    }
    structured = {
        "mode": "shadow",
        "candidate_evaluations": [
            {
                "drug_concept_id": "disitamab-vedotin",
                "ownership_key": "same-key",
                "selected_eligibility_evaluation": evaluation,
            }
        ],
    }
    tool_calls = json.dumps(
        [{"tool_name": "drug_audit_lookup", "structured_output": structured}]
    )
    rows = [
        {
            "run_id": f"aud-shadow-{index}",
            "patient_id": "RAW-PATIENT-ID",
            "verdict": "CLEAN",
            "tool_calls_json": tool_calls,
        }
        for index in (1, 2)
    ]
    report = build_report(rows, salt="test-salt")
    encoded = json.dumps(report, ensure_ascii=False)
    assert report["comparison_count"] == 2
    assert report["duplicate_count"] == 1
    assert report["comparisons"][0]["decisive_criteria"] == ["her2"]
    assert "RAW-PATIENT-ID" not in encoded
    assert "same-key" not in encoded


def test_direct_shadow_report_joins_history_and_latest_expert_review():
    assessment = {
        "criterion_id": "her2",
        "criterion_type": "biomarker",
        "state": "NOT_SATISFIED",
        "expected_condition": {"accepted_scores": ["2+", "3+"]},
        "normalized_facts": [],
        "evidence_anchors": [],
        "reason": "合成反证",
        "missing_items": [],
    }
    evaluation = {
        "legacy_verdict": "VIOLATION",
        "audit_disposition": "VIOLATION_FOUND",
        "eligibility_status": "NOT_SATISFIED",
        "rule_id": "synthetic-rule",
        "rule_version": "2026.1",
        "indication_branch_id": "synthetic-branch",
        "source_versions": ["synthetic-source@1"],
        "criterion_assessments": [assessment],
        "proof_tree": {
            "node_id": "her2",
            "operator": "leaf",
            "criterion_id": "her2",
            "criterion_type": "biomarker",
            "state": "NOT_SATISFIED",
            "children": [],
            "assessment": assessment,
        },
        "documentation_suggestions": [],
        "data_quality_flags": [],
    }
    shadow_runs = [{
        "patient_id": "RAW-PATIENT-ID",
        "structured": {
            "mode": "shadow",
            "selected_eligibility_evaluation": evaluation,
            "candidate_evaluations": [{
                "generic_name": "测试药",
                "drug_concept_id": "test-drug",
                "ownership_key": "raw-ownership-key",
                "selected_eligibility_evaluation": evaluation,
            }],
        },
    }]
    history = {
        "RAW-PATIENT-ID": {
            "run_id": "raw-run-id",
            "verdict": "CLEAN",
            "latest_expert_review": {
                "run_id": "raw-reviewed-run-id",
                "review_run_old_verdict": "CLEAN",
                "review_verdict": "V",
            },
        }
    }
    report = build_direct_report(
        shadow_runs,
        history_by_patient=history,
        salt="test-salt",
    )
    encoded = json.dumps(report, ensure_ascii=False)
    assert report["patient_count"] == 1
    assert report["comparison_count"] == 1
    assert report["expert_reviewed_patient_count"] == 1
    assert report["expert_review_new_agreement_rate"] == 1.0
    assert report["expert_review_old_agreement_rate"] == 0.0
    assert report["comparisons"][0]["decisive_criteria"] == ["her2"]
    for raw in (
        "RAW-PATIENT-ID",
        "raw-run-id",
        "raw-reviewed-run-id",
        "raw-ownership-key",
    ):
        assert raw not in encoded
