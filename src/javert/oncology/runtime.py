# -*- coding: utf-8 -*-
"""RD04 肿瘤医保候选的离线结构化求值装配."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .contracts import (
    AuditDisposition,
    CriterionAssessment,
    CriterionState,
    EligibilityEvaluation,
    EligibilityStatus,
    EvidenceAnchor,
    NormalizedFact,
    ProofNode,
)
from .eligibility import (
    ConditionNode,
    EligibilityRule,
    evaluate_rule,
    load_eligibility_rules,
    select_effective_rules,
)
from .guidance import (
    generate_documentation_suggestions,
    load_documentation_templates,
)
from .pathology import (
    BiomarkerMethod,
    PathologyInput,
    evaluate_biomarker_criterion,
    load_pathology_kb,
)
from .regimen import (
    RegimenResolution,
    ResolutionStatus,
    load_regimen_kb,
    resolve_regimen,
)

STRUCTURED_PREFIX = "<<<JAVERT_ONCOLOGY_JSON>>>"
STRUCTURED_SUFFIX = "<<<END_JAVERT_ONCOLOGY_JSON>>>"

_CONTENT_COLUMNS = ("内容", "content", "text", "record_content", "note_content")
_SECTION_COLUMNS = ("子阶段", "section", "sub_stage", "阶段")
_DATE_COLUMNS = ("事件时间", "document_date", "record_time", "日期", "time")
_FEE_DATE_COLUMNS = ("fee_ocur_time", "service_date", "charge_time")
_DEFAULT_GUIDANCE_PATH = (
    Path(__file__).resolve().parents[3]
    / "configs"
    / "oncology_documentation_templates.json"
)


def _first_column(frame: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    return next((name for name in candidates if name in frame.columns), None)


def _as_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        parsed = pd.to_datetime(text, errors="coerce")
        return None if pd.isna(parsed) else parsed.date()


def _note_records(notes: pd.DataFrame) -> list[dict[str, Any]]:
    if notes is None or notes.empty:
        return []
    content_col = _first_column(notes, _CONTENT_COLUMNS)
    if content_col is None:
        return []
    section_col = _first_column(notes, _SECTION_COLUMNS)
    date_col = _first_column(notes, _DATE_COLUMNS)
    records = []
    for index, row in notes.iterrows():
        text = str(row.get(content_col) or "").strip()
        if not text or text.lower() == "nan":
            continue
        section = str(row.get(section_col) or "").strip() if section_col else ""
        records.append(
            {
                "text": text,
                "section": "" if section.lower() == "nan" else section,
                "date": _as_date(row.get(date_col)) if date_col else None,
                "locator": f"note:{index}",
            }
        )
    return records


def _candidate_service_date(
    fees: pd.DataFrame,
    match: dict[str, Any],
) -> date | None:
    """只用当前药品净正收费行选服务日期，避免被患者其他费用的更早日期污染."""
    if fees is None or fees.empty:
        return None
    date_col = _first_column(fees, _FEE_DATE_COLUMNS)
    if date_col is None:
        return None
    codes = {str(item) for item in match.get("fee_codes", []) if item}
    names = {str(item) for item in match.get("fee_names", []) if item}
    dates: list[date] = []
    for _, row in fees.iterrows():
        code = str(row.get("med_list_codg") or "").strip()
        name = str(row.get("medins_list_name") or "").strip()
        if not ((code and code in codes) or name in names):
            continue
        try:
            quantity = float(row.get("cnt"))
        except (TypeError, ValueError):
            quantity = 0.0
        if quantity <= 0:
            continue
        parsed = _as_date(row.get(date_col))
        if parsed is not None:
            dates.append(parsed)
    return min(dates) if dates else None


def _anchor(source: str, locator: str, text: str) -> EvidenceAnchor:
    return EvidenceAnchor(source=source, locator=locator, text=text)


def _assessment(
    node: ConditionNode,
    state: CriterionState,
    reason: str,
    *,
    source: str = "notes",
    locator: str = "",
    text: str = "",
    normalized_value: Any = None,
    service_date: date | None = None,
    missing_items: list[str] | None = None,
) -> CriterionAssessment:
    anchors = [_anchor(source, locator, text)] if text else []
    facts = []
    if text:
        facts.append(
            NormalizedFact(
                fact_type=node.criterion_type,
                value=text,
                normalized_value=normalized_value,
                source=source,
                anchor=anchors[0],
                service_date=service_date,
                normalizer_version="oncology-runtime-1.0.0",
            )
        )
    return CriterionAssessment(
        criterion_id=node.criterion_id,
        criterion_type=node.criterion_type,
        state=state,
        expected_condition=node.expected,
        normalized_facts=facts,
        evidence_anchors=anchors,
        reason=reason,
        missing_items=missing_items or ([] if state != CriterionState.UNKNOWN else [node.criterion_id]),
        evaluator_version="1.0.0",
        normalizer_version="oncology-runtime-1.0.0",
    )


def _leaf_nodes(node: ConditionNode) -> list[ConditionNode]:
    if node.kind == "leaf":
        return [node]
    return [leaf for child in node.children for leaf in _leaf_nodes(child)]


def _find_text(
    records: list[dict[str, Any]],
    pattern: str,
) -> dict[str, Any] | None:
    regex = re.compile(pattern, re.I)
    return next((record for record in records if regex.search(record["text"])), None)


def _find_asserted_text(
    records: list[dict[str, Any]],
    positive_pattern: str,
    negative_pattern: str,
) -> dict[str, Any] | None:
    positive = re.compile(positive_pattern, re.I)
    negative = re.compile(negative_pattern, re.I)
    return next(
        (
            record
            for record in records
            if positive.search(record["text"])
            and not negative.search(record["text"])
        ),
        None,
    )


def _conflict_assessment(
    node: ConditionNode,
    reason: str,
    records: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    anchors = [
        _anchor("notes", record.get("locator", ""), record["text"])
        for record in records
    ]
    return CriterionAssessment(
        criterion_id=node.criterion_id,
        criterion_type=node.criterion_type,
        state=CriterionState.CONFLICT,
        expected_condition=node.expected,
        normalized_facts=[
            NormalizedFact(
                fact_type=node.criterion_type,
                value=record["text"],
                normalized_value="conflicting_evidence",
                source="notes",
                anchor=anchor,
                service_date=service_date,
                normalizer_version="oncology-runtime-1.0.0",
            )
            for record, anchor in zip(records, anchors)
        ],
        evidence_anchors=anchors,
        reason=reason,
        evaluator_version="1.0.0",
        normalizer_version="oncology-runtime-1.0.0",
    )


def _age(records: list[dict[str, Any]]) -> int | None:
    for record in records:
        match = re.search(r"(?<!\d)(\d{1,3})\s*岁", record["text"])
        if match:
            value = int(match.group(1))
            if 0 < value < 130:
                return value
    return None


def _diagnosis_assessment(
    node: ConditionNode,
    diagnoses: list[dict[str, Any]],
    records: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    age_gte = node.expected.get("age_gte")
    if age_gte is not None:
        age = _age(records)
        if age is None:
            return _assessment(node, CriterionState.UNKNOWN, "未找到可核验年龄")
        state = (
            CriterionState.SATISFIED
            if age >= int(age_gte)
            else CriterionState.NOT_SATISFIED
        )
        return _assessment(
            node,
            state,
            f"患者年龄 {age} 岁，要求 ≥{age_gte} 岁",
            text=f"{age}岁",
            normalized_value=age,
            service_date=service_date,
        )

    expected = [str(item) for item in node.expected.get("includes", [])]
    observed = [str(item.get("name") or "") for item in diagnoses]
    for name in observed:
        if any(term.lower() in name.lower() or name.lower() in term.lower() for term in expected):
            return _assessment(
                node,
                CriterionState.SATISFIED,
                f"病案首页诊断命中 {name}",
                source="shi_zd",
                locator=str(next((item.get("code") for item in diagnoses if item.get("name") == name), "")),
                text=name,
                normalized_value=name,
                service_date=service_date,
            )
    joined = "\n".join(record["text"] for record in records)
    hit = next((term for term in expected if term.lower() in joined.lower()), None)
    if hit:
        record = next(record for record in records if hit.lower() in record["text"].lower())
        return _assessment(
            node,
            CriterionState.SATISFIED,
            f"文书诊断语境命中 {hit}",
            locator=record["locator"],
            text=record["text"],
            normalized_value=hit,
            service_date=service_date,
        )
    return _assessment(node, CriterionState.UNKNOWN, "未找到该适应症病种的确定性诊断证据")


def _stage_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    expected = str(node.expected.get("equals") or "")
    positive = "局部晚期|晚期" if expected == "locally_advanced" else "转移|远处播散"
    negative = "非晚期|早期" if expected == "locally_advanced" else "无转移|未见转移"
    combined = [
        *records,
        *[
            {"text": str(item.get("name") or ""), "locator": str(item.get("code") or "")}
            for item in diagnoses
        ],
    ]
    neg = _find_text(combined, negative)
    pos = _find_asserted_text(combined, positive, negative)
    if neg and pos:
        return _conflict_assessment(
            node,
            "分期/转移状态存在相互矛盾的适用证据",
            [neg, pos],
            service_date,
        )
    if neg:
        return _assessment(
            node,
            CriterionState.NOT_SATISFIED,
            f"发现明确反向分期描述：{neg['text']}",
            locator=neg["locator"],
            text=neg["text"],
            normalized_value=expected,
            service_date=service_date,
        )
    if pos:
        return _assessment(
            node,
            CriterionState.SATISFIED,
            f"发现分期证据：{pos['text']}",
            locator=pos["locator"],
            text=pos["text"],
            normalized_value=expected,
            service_date=service_date,
        )
    return _assessment(node, CriterionState.UNKNOWN, "分期/转移状态未明确")


def _prior_therapy_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    regimens: list[RegimenResolution],
    service_date: date,
) -> CriterionAssessment:
    if node.expected.get("contains_class") == "platinum":
        prior = _find_text(
            records,
            r"(?:既往|曾|治疗史)[^。；\n]{0,80}(?:含铂|顺铂|卡铂|奥沙利铂|奈达铂)",
        )
        if prior:
            return _assessment(
                node,
                CriterionState.SATISFIED,
                "发现既往含铂治疗的锚定记录",
                locator=prior["locator"],
                text=prior["text"],
                normalized_value="platinum",
                service_date=service_date,
            )
        return _assessment(
            node,
            CriterionState.UNKNOWN,
            "未找到可靠既往含铂治疗记录；当前方案或治疗后反应不作为既往治疗",
        )

    if node.expected.get("equals") == "untreated":
        untreated_pattern = r"既往未经治疗|初治|未接受过(?:系统)?治疗"
        treated_pattern = (
            r"既往.{0,40}(?:治疗|化疗)|多线治疗|"
            r"(?:已|曾)(?:行|接受)?多次[^。；\n]{0,40}(?:治疗|化疗)|"
            r"第[二三四五六七八九十\d]+次"
        )
        untreated = _find_text(records, untreated_pattern)
        treated = _find_asserted_text(
            records, treated_pattern, untreated_pattern
        )
        if untreated and treated:
            return _conflict_assessment(
                node,
                "既往治疗状态存在相互矛盾的适用证据",
                [untreated, treated],
                service_date,
            )
        if untreated:
            return _assessment(
                node,
                CriterionState.SATISFIED,
                "文书明确记载既往未经治疗",
                locator=untreated["locator"],
                text=untreated["text"],
                normalized_value="untreated",
                service_date=service_date,
            )
        if treated or any(item.cycle_no and item.cycle_no > 1 for item in regimens):
            record = treated or next(
                (
                    {"locator": item.anchor.locator, "text": item.anchor.text}
                    for item in regimens
                    if item.anchor and item.cycle_no and item.cycle_no > 1
                ),
                {"locator": "", "text": ""},
            )
            return _assessment(
                node,
                CriterionState.NOT_SATISFIED,
                "已有治疗史或多周期治疗，明确不属于未经治疗",
                locator=record["locator"],
                text=record["text"],
                normalized_value="previously_treated",
                service_date=service_date,
            )
        return _assessment(node, CriterionState.UNKNOWN, "既往是否接受治疗未明确")

    return _assessment(node, CriterionState.UNKNOWN, "不支持的既往治疗条件")


def _treatment_status_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    # 鉴别诊断常含“某疾病通常治疗有效/缓解”等教科书式模板句，
    # 不能拿来反证当前肿瘤的复发/难治状态。
    status_records = [
        record
        for record in records
        if "鉴别诊断" not in str(record.get("section") or "")
    ]
    expected = str(node.expected.get("equals") or "")
    expected_zh = {"relapsed": "复发", "refractory": "难治"}.get(expected, expected)
    if expected == "relapsed":
        negative_pattern = r"无复发|未见复发"
        positive_pattern = r"复发"
    else:
        negative_pattern = r"非难治|治疗有效|疾病缓解"
        positive_pattern = r"难治|治疗后.{0,30}(?:进展|无效)|疾病进展"
    negative = _find_text(status_records, negative_pattern)
    positive = _find_asserted_text(
        status_records, positive_pattern, negative_pattern
    )
    if positive and negative:
        return _conflict_assessment(
            node,
            f"{expected_zh}状态存在相互矛盾的证据",
            [positive, negative],
            service_date,
        )
    found = positive or negative
    if found:
        state = CriterionState.SATISFIED if positive else CriterionState.NOT_SATISFIED
        return _assessment(
            node,
            state,
            f"文书{'支持' if positive else '不支持'}{expected_zh}状态",
            locator=found["locator"],
            text=found["text"],
            normalized_value=expected,
            service_date=service_date,
        )
    return _assessment(node, CriterionState.UNKNOWN, f"文书未见明确的{expected_zh}状态")


def _clinician_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    ineligible = _find_text(records, r"不适合.{0,20}(?:造血干细胞|干细胞)移植|移植不耐受")
    suitable = _find_asserted_text(
        records,
        r"适合.{0,20}(?:造血干细胞|干细胞)移植|拟行.{0,20}移植",
        r"不适合.{0,20}(?:造血干细胞|干细胞)移植|移植不耐受",
    )
    if ineligible and suitable:
        return _conflict_assessment(
            node,
            "造血干细胞移植适合性评估存在相互矛盾的适用证据",
            [ineligible, suitable],
            service_date,
        )
    found = ineligible or suitable
    if found:
        return _assessment(
            node,
            CriterionState.SATISFIED if ineligible else CriterionState.NOT_SATISFIED,
            "找到移植适合性临床评估",
            locator=found["locator"],
            text=found["text"],
            normalized_value="hsct_ineligible" if ineligible else "hsct_suitable",
            service_date=service_date,
        )
    return _assessment(
        node,
        CriterionState.UNKNOWN,
        "未见不适合造血干细胞移植的临床评估及简要原因",
        missing_items=["不适合造血干细胞移植的临床评估及简要原因"],
    )


def _resolve_regimens(
    records: list[dict[str, Any]],
    *,
    regimen_path: Path,
    cancer_context: str,
    fee_codes: list[str],
    service_date: date | None,
) -> list[RegimenResolution]:
    asset = load_regimen_kb(regimen_path)
    out = []
    for record in records:
        resolution = resolve_regimen(
            text=record["text"],
            asset=asset,
            cancer_context=cancer_context or None,
            fee_codes=fee_codes,
            locator=record["locator"],
            encounter_date=service_date,
            document_date=record["date"],
        )
        if resolution.status != ResolutionStatus.NOT_FOUND:
            out.append(resolution)
    return out


def _concept_index(
    regimen_path: Path,
) -> tuple[dict[str, str], dict[str, str], dict[str, list[str]]]:
    asset = load_regimen_kb(regimen_path)
    by_name = {item.generic_name: item.concept_id for item in asset.drug_concepts}
    by_code = {
        code: item.concept_id
        for item in asset.drug_concepts
        for code in item.insurance_codes
    }
    terms = {
        item.concept_id: [
            item.generic_name,
            *[
                alias.value
                for alias in item.aliases
                if len(alias.value.strip()) > 1
            ],
        ]
        for item in asset.drug_concepts
    }
    return by_name, by_code, terms


def _find_confirmed_self_pay(
    records: list[dict[str, Any]],
    concept_terms: list[str],
) -> dict[str, Any] | None:
    positive = re.compile(r"自费药品|同意自费|自费使用|全自费|患者自费")
    uncertain_or_negative = re.compile(
        r"非自费|不自费|不是自费|是否自费|自费(?:情况)?待(?:确认|核实)"
    )
    for record in records:
        text = record["text"]
        if uncertain_or_negative.search(text) or not positive.search(text):
            continue
        if any(term.lower() in text.lower() for term in concept_terms):
            return record
    return None


def _self_pay_evaluation(
    *,
    concept_id: str,
    record: dict[str, Any],
) -> EligibilityEvaluation:
    assessment = CriterionAssessment(
        criterion_id=f"{concept_id}-payer-scope",
        criterion_type="payer_scope",
        state=CriterionState.SATISFIED,
        expected_condition={"payer": "self_pay"},
        normalized_facts=[
            NormalizedFact(
                fact_type="payer_scope",
                value=record["text"],
                normalized_value="self_pay",
                source="notes",
                anchor=_anchor("notes", record["locator"], record["text"]),
                normalizer_version="oncology-runtime-1.0.0",
            )
        ],
        evidence_anchors=[
            _anchor("notes", record["locator"], record["text"])
        ],
        reason="自费药品使用记录命中，该药不进入医保支付资格裁决",
    )
    proof = ProofNode(
        node_id=assessment.criterion_id,
        operator="leaf",
        state=CriterionState.SATISFIED,
        criterion_id=assessment.criterion_id,
        criterion_type=assessment.criterion_type,
        assessment=assessment,
        reason=assessment.reason,
    )
    return EligibilityEvaluation(
        audit_disposition=AuditDisposition.NO_VIOLATION_FOUND,
        eligibility_status=EligibilityStatus.SATISFIED,
        rule_id="payer-scope-self-pay",
        indication_branch_id=concept_id,
        criterion_assessments=[assessment],
        proof_tree=proof,
        data_quality_flags=["SELF_PAY_EXCLUDED"],
    )


def _unknown_evaluation(
    *,
    concept_id: str,
    data_quality_flags: list[str],
) -> EligibilityEvaluation:
    assessment = CriterionAssessment(
        criterion_id=f"{concept_id or 'unknown'}-uncompiled-rule",
        criterion_type="unsupported",
        state=CriterionState.UNKNOWN,
        reason="该肿瘤医保限定尚无生效且已审核的结构化条件树",
        missing_items=["approved eligibility condition tree"],
    )
    proof = ProofNode(
        node_id=assessment.criterion_id,
        operator="leaf",
        state=CriterionState.UNKNOWN,
        criterion_id=assessment.criterion_id,
        criterion_type=assessment.criterion_type,
        assessment=assessment,
        reason=assessment.reason,
    )
    return EligibilityEvaluation(
        audit_disposition=AuditDisposition.REVIEW_REQUIRED,
        eligibility_status=EligibilityStatus.DOCUMENTATION_GAP,
        rule_id="uncompiled-oncology-restriction",
        indication_branch_id=concept_id,
        criterion_assessments=[assessment],
        proof_tree=proof,
        data_quality_flags=sorted(set(data_quality_flags + ["NO_APPROVED_ELIGIBILITY_RULE"])),
    )


def _best_evaluation(evaluations: list[EligibilityEvaluation]) -> EligibilityEvaluation:
    disposition_rank = {
        AuditDisposition.NO_VIOLATION_FOUND: 0,
        AuditDisposition.REVIEW_REQUIRED: 1,
        AuditDisposition.VIOLATION_FOUND: 2,
    }
    status_rank = {
        EligibilityStatus.SATISFIED: 0,
        EligibilityStatus.DOCUMENTATION_GAP: 1,
        EligibilityStatus.CONFLICT: 2,
        EligibilityStatus.NOT_SATISFIED: 3,
    }
    return min(
        evaluations,
        key=lambda item: (
            disposition_rank[item.audit_disposition],
            status_rank[item.eligibility_status],
            item.indication_branch_id,
        ),
    )


def _worst_evaluation(evaluations: list[EligibilityEvaluation]) -> EligibilityEvaluation:
    """不同收费候选聚合时任一违规即违规，其次人工复核，最后才是无违规."""
    disposition_rank = {
        AuditDisposition.NO_VIOLATION_FOUND: 0,
        AuditDisposition.REVIEW_REQUIRED: 1,
        AuditDisposition.VIOLATION_FOUND: 2,
    }
    status_rank = {
        EligibilityStatus.SATISFIED: 0,
        EligibilityStatus.DOCUMENTATION_GAP: 1,
        EligibilityStatus.CONFLICT: 2,
        EligibilityStatus.NOT_SATISFIED: 3,
    }
    return max(
        evaluations,
        key=lambda item: (
            disposition_rank[item.audit_disposition],
            status_rank[item.eligibility_status],
            item.indication_branch_id,
        ),
    )


def _evaluate_rule_leaves(
    rule: EligibilityRule,
    *,
    diagnoses: list[dict[str, Any]],
    records: list[dict[str, Any]],
    regimens: list[RegimenResolution],
    service_date: date,
    pathology_path: Path,
    cancer_context: str,
    enforce_effective_date: bool = True,
) -> dict[str, CriterionAssessment]:
    pathology_asset = load_pathology_kb(pathology_path)
    applicable_records = [
        record
        for record in records
        if record["date"] is None or record["date"] <= service_date
    ]
    pathology_inputs = [
        PathologyInput(
            text=record["text"],
            locator=record["locator"],
            specimen_date=record["date"],
            report_date=record["date"],
        )
        for record in records
        if re.search(r"HER\s*[-]?\s*2|CerbB2|c-erbB-2|ERBB2", record["text"], re.I)
    ]
    assessments: dict[str, CriterionAssessment] = {}
    for node in _leaf_nodes(rule.condition_tree):
        if node.criterion_type == "diagnosis":
            item = _diagnosis_assessment(
                node, diagnoses, applicable_records, service_date
            )
        elif node.criterion_type == "stage":
            item = _stage_assessment(
                node, applicable_records, diagnoses, service_date
            )
        elif node.criterion_type == "prior_therapy":
            item = _prior_therapy_assessment(
                node, applicable_records, regimens, service_date
            )
        elif node.criterion_type == "treatment_status":
            item = _treatment_status_assessment(
                node, applicable_records, service_date
            )
        elif node.criterion_type == "clinician_assessment":
            item = _clinician_assessment(
                node, applicable_records, service_date
            )
        elif node.criterion_type == "biomarker":
            item = evaluate_biomarker_criterion(
                criterion_id=node.criterion_id,
                marker_id=str(node.expected.get("marker_id") or ""),
                cancer_context=cancer_context,
                policy_context="disitamab-urothelial-insurance",
                expected_method=BiomarkerMethod(str(node.expected.get("method") or "IHC")),
                inputs=pathology_inputs,
                service_date=service_date,
                asset=pathology_asset,
                enforce_effective_date=enforce_effective_date,
            )
        else:
            item = _assessment(node, CriterionState.UNKNOWN, "不支持的确定性条件类型")
        assessments[node.criterion_id] = item
    return assessments


def evaluate_oncology_matches(
    *,
    matches: list[dict[str, Any]],
    fees: pd.DataFrame,
    notes: pd.DataFrame,
    diagnoses: list[dict[str, Any]],
    eligibility_path: Path,
    pathology_path: Path,
    regimen_path: Path,
    documentation_templates_path: Path | None = None,
    enforce_effective_date: bool = True,
) -> dict[str, Any]:
    """为已有净正收费的 RD04 matches 附加方案证据与条件树结果.

    enforce_effective_date=False 时不按声明生效期过滤 (不分时间全部生效)，就诊日落在
    声明窗口外的候选会追加"核查生效时间"提示，前端 fail-loud 展示。
    """
    rules_asset = load_eligibility_rules(eligibility_path)
    by_name, by_code, concept_terms = _concept_index(regimen_path)
    records = _note_records(notes)
    diagnosis_text = " / ".join(str(item.get("name") or "") for item in diagnoses)
    # 诊断语境也纳入文书原文: 病案首页可能只编码为「移行细胞癌/膀胱恶性肿瘤」等,
    # 而文书病理明确写「尿路上皮癌」组织学型 (与 diagnosis 叶子同源判断口径).
    context_text = diagnosis_text + " / " + " ".join(r["text"] for r in records)
    guidance_path = documentation_templates_path or _DEFAULT_GUIDANCE_PATH
    guidance_templates = (
        load_documentation_templates(guidance_path)
        if guidance_path.exists()
        else None
    )
    # 移行细胞癌 / 移行上皮癌 是尿路上皮癌 (WHO 2004 前后) 异名, 归一到医保限定用语.
    if any(k in context_text for k in ("尿路上皮", "移行细胞癌", "移行上皮癌")):
        cancer_context = "尿路上皮癌"
    elif "弥漫大B" in diagnosis_text or "DLBCL" in diagnosis_text.upper():
        cancer_context = "弥漫大B细胞淋巴瘤"
    else:
        cancer_context = diagnosis_text

    candidate_rows = []
    candidate_dates: list[date] = []
    all_evaluations: list[EligibilityEvaluation] = []
    for match in matches:
        service_date = _candidate_service_date(fees, match)
        if service_date is not None:
            candidate_dates.append(service_date)
        codes = [str(item) for item in match.get("fee_codes", []) if item]
        concept_id = next(
            (by_code[code] for code in codes if code in by_code),
            by_name.get(str(match.get("generic_name") or ""), ""),
        )
        regimens = _resolve_regimens(
            [
                record
                for record in records
                if service_date is None
                or record["date"] is None
                or record["date"] <= service_date
            ],
            regimen_path=regimen_path,
            cancer_context=cancer_context,
            fee_codes=codes,
            service_date=service_date,
        )
        self_pay_record = _find_confirmed_self_pay(
            records,
            concept_terms.get(concept_id, []),
        )
        if self_pay_record:
            evaluations = [
                _self_pay_evaluation(
                    concept_id=concept_id,
                    record=self_pay_record,
                )
            ]
        else:
            evaluations = []
            if service_date is None:
                evaluations = [
                    _unknown_evaluation(
                        concept_id=concept_id,
                        data_quality_flags=["MISSING_CANDIDATE_SERVICE_DATE"],
                    )
                ]
            else:
                selection = select_effective_rules(
                    rules_asset,
                    drug_concept_id=concept_id,
                    service_date=service_date,
                    enforce_effective_date=enforce_effective_date,
                )
                for rule in selection.rules:
                    assessments = _evaluate_rule_leaves(
                        rule,
                        diagnoses=diagnoses,
                        records=records,
                        regimens=regimens,
                        service_date=service_date,
                        pathology_path=pathology_path,
                        cancer_context=cancer_context,
                        enforce_effective_date=enforce_effective_date,
                    )
                    evaluation = evaluate_rule(rule, assessments)
                    flags = list(evaluation.data_quality_flags)
                    for regimen in regimens:
                        flags.extend(regimen.conflicts)
                        if regimen.temporal_conflict:
                            flags.append("治疗叙述年份与就诊/收费年份冲突")
                    # 生效期核查: 未强制过滤且就诊日在声明窗口外 → fail-loud 提示 (不静默)
                    if (
                        not enforce_effective_date
                        and evaluation.rule_effective_from is not None
                        and (
                            service_date < evaluation.rule_effective_from
                            or (
                                evaluation.rule_effective_to is not None
                                and service_date > evaluation.rule_effective_to
                            )
                        )
                    ):
                        flags.append("未按生效期过滤·需核查就诊时该医保限定是否已生效")
                    evaluation = evaluation.model_copy(
                        update={"data_quality_flags": sorted(set(flags))}
                    )
                    evaluations.append(evaluation)
                if not evaluations:
                    evaluations = [
                        _unknown_evaluation(
                            concept_id=concept_id,
                            data_quality_flags=selection.data_quality_flags,
                        )
                    ]
        selected = _best_evaluation(evaluations)
        selected = selected.model_copy(
            update={
                "documentation_suggestions": generate_documentation_suggestions(
                    selected,
                    patient_context={
                        "age": _age(records),
                        "multi_line_treatment": bool(
                            _find_text(records, r"多线治疗|多次治疗|第[二三四五六七八九十\d]+线")
                        ),
                    },
                    templates=guidance_templates,
                ),
                "evaluated_service_date": service_date,
                "effective_date_enforced": enforce_effective_date,
            }
        )
        evaluations = [
            selected if item.indication_branch_id == selected.indication_branch_id else item
            for item in evaluations
        ]
        all_evaluations.append(selected)
        candidate_rows.append(
            {
                "generic_name": match.get("generic_name", ""),
                "drug_concept_id": concept_id,
                "ownership_key": match.get("ownership_key", ""),
                "net_quantity": match.get("net_quantity"),
                "service_date": (
                    service_date.isoformat() if service_date is not None else None
                ),
                "regimen_evidence": [item.model_dump(mode="json") for item in regimens],
                "eligibility_evaluations": [
                    item.model_dump(mode="json") for item in evaluations
                ],
                "selected_eligibility_evaluation": selected.model_dump(mode="json"),
            }
        )

    selected_overall = _worst_evaluation(all_evaluations) if all_evaluations else None
    return {
        "service_date": min(candidate_dates).isoformat() if candidate_dates else None,
        "candidate_evaluations": candidate_rows,
        "selected_eligibility_evaluation": (
            selected_overall.model_dump(mode="json") if selected_overall else None
        ),
    }


def ownership_key(
    *,
    patient_id: str,
    generic_name: str,
    fee_names: list[str],
    fee_codes: list[str],
    basis: str,
    source_refs: list[str],
) -> str:
    payload = json.dumps(
        {
            "patient_id": patient_id,
            "generic_name": generic_name,
            "fee_names": sorted(fee_names),
            "fee_codes": sorted(fee_codes),
            "basis": basis,
            "source_refs": sorted(source_refs),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "onc:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def encode_structured_payload(payload: dict[str, Any]) -> str:
    return (
        STRUCTURED_PREFIX
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + STRUCTURED_SUFFIX
    )


def decode_structured_payload(text: str) -> dict[str, Any] | None:
    start = text.find(STRUCTURED_PREFIX)
    if start < 0:
        return None
    start += len(STRUCTURED_PREFIX)
    end = text.find(STRUCTURED_SUFFIX, start)
    if end < 0:
        return None
    try:
        payload = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
