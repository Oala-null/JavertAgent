# -*- coding: utf-8 -*-
"""RD04 肿瘤医保候选的离线结构化求值装配."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
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
    OncologyPolicyScope,
    ProofNode,
)
from .eligibility import (
    ConditionNode,
    EligibilityRule,
    EligibilityRulesAsset,
    RuleSelection,
    evaluate_rule,
    load_eligibility_rules,
    select_effective_rules,
)
from .authoring.runtime_policy import TemporalSelection, select_temporal_policy
from .knowledge import ReviewStatus
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

    if node.expected.get("exists") is True:
        prior = _find_text(
            records,
            r"既往.{0,80}(?:治疗|化疗|放疗|靶向|免疫)|"
            r"曾(?:行|接受|使用).{0,80}(?:治疗|化疗|放疗|药)|"
            r"治疗后.{0,40}(?:进展|复发|失败|无效)",
        )
        if prior or any(
            item.event_status.value in {"HISTORICAL", "ADMINISTERED"}
            for item in regimens
        ):
            record = prior or next(
                (
                    {"locator": item.anchor.locator, "text": item.anchor.text}
                    for item in regimens
                    if item.anchor
                    and item.event_status.value in {"HISTORICAL", "ADMINISTERED"}
                ),
                {"locator": "", "text": ""},
            )
            return _assessment(
                node,
                CriterionState.SATISFIED,
                "找到明确既往治疗记录",
                locator=record["locator"],
                text=record["text"],
                normalized_value=True,
                service_date=service_date,
            )
        return _assessment(node, CriterionState.UNKNOWN, "未找到明确既往治疗记录")

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
    patterns = {
        "relapsed": ("复发", r"复发", r"无复发|未见复发"),
        "refractory": (
            "难治",
            r"难治|治疗后.{0,30}(?:进展|无效)|疾病进展",
            r"非难治|治疗有效|疾病缓解",
        ),
        "progressed": (
            "进展",
            r"疾病进展|治疗后.{0,30}进展|明确进展",
            r"未见进展|无进展|疾病稳定|完全缓解|部分缓解",
        ),
        "progressive_phenotype": (
            "进行性表型",
            r"进行性表型|进行性.{0,20}(?:肺疾病|纤维化)",
            r"非进行性|病情稳定",
        ),
        "newly_diagnosed": (
            "新诊断",
            r"新诊断|初诊|初治",
            r"复发|既往.{0,30}(?:治疗|化疗)",
        ),
    }
    expected_zh, positive_pattern, negative_pattern = patterns.get(
        expected,
        (expected, rf"{re.escape(expected)}", r"(?!)"),
    )
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


def _combined_text_records(
    records: list[dict[str, Any]],
    diagnoses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        *records,
        *[
            {
                "text": str(item.get("name") or ""),
                "locator": str(item.get("code") or ""),
            }
            for item in diagnoses
            if str(item.get("name") or "").strip()
        ],
    ]


def _term_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    *,
    terms: list[str],
    service_date: date,
    label: str,
    negative_pattern: str = "",
) -> CriterionAssessment:
    terms = [item.strip() for item in terms if item and item.strip()]
    if not terms:
        return _assessment(node, CriterionState.UNKNOWN, f"{label}条件缺少期望值")
    negative = _find_text(records, negative_pattern) if negative_pattern else None
    positive = next(
        (
            record
            for record in records
            if any(term.casefold() in record["text"].casefold() for term in terms)
            and (negative is None or record is not negative)
        ),
        None,
    )
    if positive and negative:
        return _conflict_assessment(
            node,
            f"{label}存在相互矛盾的证据",
            [positive, negative],
            service_date,
        )
    if negative:
        return _assessment(
            node,
            CriterionState.NOT_SATISFIED,
            f"发现明确不符合{label}的记录",
            locator=negative["locator"],
            text=negative["text"],
            normalized_value=terms,
            service_date=service_date,
        )
    if positive:
        return _assessment(
            node,
            CriterionState.SATISFIED,
            f"找到明确{label}证据",
            locator=positive["locator"],
            text=positive["text"],
            normalized_value=terms,
            service_date=service_date,
        )
    return _assessment(node, CriterionState.UNKNOWN, f"未找到明确{label}证据")


def _age_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    age = _age(records)
    if age is None:
        return _assessment(node, CriterionState.UNKNOWN, "未找到可核验年龄")
    minimum = node.expected.get("gte")
    maximum = node.expected.get("lte")
    satisfied = (
        (minimum is None or age >= int(minimum))
        and (maximum is None or age <= int(maximum))
    )
    return _assessment(
        node,
        CriterionState.SATISFIED if satisfied else CriterionState.NOT_SATISFIED,
        f"患者年龄 {age} 岁",
        text=f"{age}岁",
        normalized_value=age,
        service_date=service_date,
    )


def _resectability_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    expected = str(node.expected.get("equals") or "")
    if expected != "unresectable":
        return _assessment(node, CriterionState.UNKNOWN, "不支持的可切除性期望值")
    negative = _find_asserted_text(
        records,
        r"(?:可|适合)手术切除|(?<!不)可切除",
        r"不可切除|不能手术|不适合手术|无法手术",
    )
    positive = _find_asserted_text(
        records,
        r"不可切除|不能手术|不适合手术|无法手术",
        r"并非不可切除|排除不可切除",
    )
    if positive and negative:
        return _conflict_assessment(
            node,
            "可切除性记录相互矛盾",
            [positive, negative],
            service_date,
        )
    found = positive or negative
    if found:
        return _assessment(
            node,
            CriterionState.SATISFIED if positive else CriterionState.NOT_SATISFIED,
            "找到明确可切除性记录",
            locator=found["locator"],
            text=found["text"],
            normalized_value=expected,
            service_date=service_date,
        )
    return _assessment(node, CriterionState.UNKNOWN, "可切除性未明确")


def _therapy_count_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    regimens: list[RegimenResolution],
    service_date: date,
) -> CriterionAssessment:
    minimum = int(node.expected.get("gte") or 0)
    observed = [
        int(match.group(1))
        for record in records
        for match in re.finditer(r"(?:至少)?(?:接受过)?(\d+)种(?:系统性)?治疗", record["text"])
    ]
    observed.extend(
        item.line_of_therapy
        for item in regimens
        if item.line_of_therapy is not None
    )
    if not observed:
        return _assessment(node, CriterionState.UNKNOWN, "既往治疗数量未明确")
    value = max(observed)
    return _assessment(
        node,
        CriterionState.SATISFIED if value >= minimum else CriterionState.NOT_SATISFIED,
        f"已核验治疗数量 {value}，要求至少 {minimum}",
        normalized_value=value,
        service_date=service_date,
    )


def _line_of_therapy_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    regimens: list[RegimenResolution],
    service_date: date,
) -> CriterionAssessment:
    expected = int(node.expected.get("equals") or 0)
    labels = {1: "一线", 2: "二线", 3: "三线"}
    hits = [
        item.line_of_therapy
        for item in regimens
        if item.line_of_therapy is not None
    ]
    for value, label in labels.items():
        if _find_text(records, rf"{label}(?:治疗|方案|用药)"):
            hits.append(value)
    if not hits:
        return _assessment(node, CriterionState.UNKNOWN, "治疗线次未明确")
    value = max(hits)
    return _assessment(
        node,
        CriterionState.SATISFIED if value == expected else CriterionState.NOT_SATISFIED,
        f"已核验治疗线次 {value}，要求 {expected}",
        normalized_value=value,
        service_date=service_date,
    )


def _combination_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    regimens: list[RegimenResolution],
    service_date: date,
) -> CriterionAssessment:
    requirement = str(node.expected.get("requirement") or "REQUIRED").upper()
    if requirement in {"OPTIONAL", "WITH_OR_WITHOUT"}:
        return _assessment(
            node,
            CriterionState.SATISFIED,
            "该联合组分为可选项，不阻断资格",
            normalized_value=requirement,
            service_date=service_date,
        )
    target_kind = str(node.expected.get("target_kind") or "").upper()
    target_id = str(node.expected.get("target_id") or "")
    display_name = str(node.expected.get("display_name") or "").strip()
    matched_resolution = next(
        (
            item
            for item in regimens
            if (target_kind == "REGIMEN" and item.regimen_id == target_id)
            or any(
                component.target_kind.value == target_kind
                and component.target_id == target_id
                and component.target_matched
                for component in item.components
            )
        ),
        None,
    )
    text_hit = _find_text(records, re.escape(display_name)) if display_name else None
    if matched_resolution or text_hit:
        anchor = (
            matched_resolution.anchor
            if matched_resolution and matched_resolution.anchor
            else None
        )
        return _assessment(
            node,
            CriterionState.SATISFIED,
            "找到要求的联合方案/组分证据",
            locator=anchor.locator if anchor else text_hit["locator"],
            text=anchor.text if anchor else text_hit["text"],
            normalized_value={"target_kind": target_kind, "target_id": target_id},
            service_date=service_date,
        )
    return _assessment(node, CriterionState.UNKNOWN, "未找到要求的联合方案/组分证据")


def _intervention_status_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    service_date: date,
    *,
    intervention: str,
) -> CriterionAssessment:
    patterns = {
        "surgery": r"术后|手术切除后|接受过手术|经(?:过)?手术|已行手术",
        "radiotherapy": r"既往.{0,30}放疗|接受过放疗|放疗后|已行放疗",
    }
    hit = _find_text(records, patterns[intervention])
    if hit:
        return _assessment(
            node,
            CriterionState.SATISFIED,
            f"找到明确{'手术' if intervention == 'surgery' else '放疗'}记录",
            locator=hit["locator"],
            text=hit["text"],
            normalized_value=True,
            service_date=service_date,
        )
    return _assessment(
        node,
        CriterionState.UNKNOWN,
        f"未找到明确{'手术' if intervention == 'surgery' else '放疗'}记录",
    )


def _transplant_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    ineligible = _find_text(records, r"不适合.{0,30}移植|无法.{0,30}移植|移植不耐受")
    eligible = _find_asserted_text(
        records,
        r"适合.{0,30}移植|拟行.{0,30}移植",
        r"不适合.{0,30}移植|无法.{0,30}移植|移植不耐受",
    )
    if ineligible and eligible:
        return _conflict_assessment(
            node,
            "移植适合性记录相互矛盾",
            [ineligible, eligible],
            service_date,
        )
    expected_ineligible = "not_equals" in node.expected
    found = ineligible or eligible
    if found:
        satisfied = bool(ineligible) if expected_ineligible else bool(eligible)
        return _assessment(
            node,
            CriterionState.SATISFIED if satisfied else CriterionState.NOT_SATISFIED,
            "找到明确移植适合性记录",
            locator=found["locator"],
            text=found["text"],
            normalized_value="ineligible" if ineligible else "eligible",
            service_date=service_date,
        )
    return _assessment(node, CriterionState.UNKNOWN, "移植适合性未明确")


def _time_window_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    days = int(node.expected.get("within_days") or 0)
    if node.expected.get("window_kind") == "maximum_payment_duration":
        return _assessment(
            node,
            CriterionState.UNKNOWN,
            f"需核验该药累计支付时长是否超过 {days} 天",
            missing_items=["该药首次与末次支付日期"],
        )
    hits = [
        record
        for record in records
        if record.get("date") is not None
        and re.search(r"进展|复发|治疗失败", record["text"])
        and 0 <= (service_date - record["date"]).days <= days
    ]
    if hits:
        record = max(hits, key=lambda item: item["date"])
        return _assessment(
            node,
            CriterionState.SATISFIED,
            f"目标事件发生在服务日前 {days} 天窗口内",
            locator=record["locator"],
            text=record["text"],
            normalized_value=(service_date - record["date"]).days,
            service_date=service_date,
        )
    return _assessment(node, CriterionState.UNKNOWN, f"未找到服务日前 {days} 天内的目标事件")


def _clinician_assessment(
    node: ConditionNode,
    records: list[dict[str, Any]],
    service_date: date,
) -> CriterionAssessment:
    excluded = [str(item) for item in node.expected.get("not_in", [])]
    if excluded:
        found = next(
            (
                record
                for record in records
                if any(term.casefold() in record["text"].casefold() for term in excluded)
            ),
            None,
        )
        if found:
            return _assessment(
                node,
                CriterionState.NOT_SATISFIED,
                "发现方案明确排除的临床分级",
                locator=found["locator"],
                text=found["text"],
                normalized_value=excluded,
                service_date=service_date,
            )
        return _assessment(
            node,
            CriterionState.UNKNOWN,
            "缺少足以排除禁用临床分级的明确评估",
            missing_items=["临床分级评估"],
        )
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
    scope_label: str = "肿瘤医保限定",
) -> EligibilityEvaluation:
    assessment = CriterionAssessment(
        criterion_id=f"{concept_id or 'unknown'}-uncompiled-rule",
        criterion_type="unsupported",
        state=CriterionState.UNKNOWN,
        reason=f"该{scope_label}尚无生效且已审核的结构化条件树",
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


@dataclass(frozen=True)
class _ReleaseScopeSelection:
    policy_scope: OncologyPolicyScope
    selection: RuleSelection
    temporal: TemporalSelection | None
    source_document_ids: tuple[str, ...]
    source_fragment_ids: tuple[str, ...]
    source_versions: tuple[str, ...]


def _release_rule_selections(
    asset: EligibilityRulesAsset,
    *,
    drug_concept_id: str,
    service_date: date | None,
) -> list[_ReleaseScopeSelection]:
    """published release 按 policy scope 独立选版和计算时间窗口。"""
    approved = [
        item
        for item in asset.entries
        if item.drug_concept_id == drug_concept_id
        and item.metadata.review_status == ReviewStatus.APPROVED
    ]
    if not approved:
        return []

    by_scope: dict[OncologyPolicyScope, list[EligibilityRule]] = {}
    for rule in approved:
        scope = rule.metadata.policy_scope
        if scope in {"INSURANCE_PAYMENT", "GUIDELINE_INDICATION"}:
            by_scope.setdefault(scope, []).append(rule)

    results: list[_ReleaseScopeSelection] = []
    for scope, scoped_rules in sorted(by_scope.items()):
        source_refs = [
            source
            for rule in scoped_rules
            for source in rule.metadata.source_refs
        ]
        common = {
            "policy_scope": scope,
            "source_document_ids": tuple(
                sorted({source.source_id for source in source_refs})
            ),
            "source_fragment_ids": tuple(
                sorted(
                    {
                        source.source_fragment_id
                        for source in source_refs
                        if source.source_fragment_id
                    }
                )
            ),
            "source_versions": tuple(
                sorted(
                    {
                        f"{source.source_id}@"
                        f"{source.version or source.effective_date or source.publication_date}"
                        for source in source_refs
                    }
                )
            ),
        }
        if service_date is None:
            results.append(
                _ReleaseScopeSelection(
                    selection=RuleSelection(
                        data_quality_flags=["MISSING_CANDIDATE_SERVICE_DATE"]
                    ),
                    temporal=None,
                    **common,
                )
            )
            continue
        effective_to = [item.metadata.effective_to for item in scoped_rules]
        if any(value is None for value in effective_to):
            results.append(
                _ReleaseScopeSelection(
                    selection=RuleSelection(data_quality_flags=["FUTURE_WINDOW_MISSING"]),
                    temporal=None,
                    **common,
                )
            )
            continue

        earliest = min(item.metadata.effective_from for item in scoped_rules)
        latest = max(value for value in effective_to if value is not None)
        temporal = select_temporal_policy(
            service_date=service_date,
            effective_from=earliest,
            effective_to=latest,
        )
        if not temporal.automatic_adjudication_allowed:
            results.append(
                _ReleaseScopeSelection(
                    selection=RuleSelection(
                        data_quality_flags=[temporal.temporal_applicability.value]
                    ),
                    temporal=temporal,
                    **common,
                )
            )
            continue

        if service_date >= earliest:
            scoped_asset = asset.model_copy(update={"entries": scoped_rules})
            results.append(
                _ReleaseScopeSelection(
                    selection=select_effective_rules(
                        scoped_asset,
                        drug_concept_id=drug_concept_id,
                        service_date=service_date,
                        enforce_effective_date=True,
                    ),
                    temporal=temporal,
                    **common,
                )
            )
            continue

        # 历史回溯：在当前 scope 内按 indication branch 选最早 approved
        # revision，不让另一 scope 的更早/更晚窗口影响本 scope。
        by_branch: dict[str, list[EligibilityRule]] = {}
        for rule in scoped_rules:
            by_branch.setdefault(rule.indication_branch_id, []).append(rule)
        rules: list[EligibilityRule] = []
        flags = [temporal.temporal_applicability.value]
        for branch_id, versions in sorted(by_branch.items()):
            first_date = min(item.metadata.effective_from for item in versions)
            first = [item for item in versions if item.metadata.effective_from == first_date]
            if len(first) != 1:
                flags.append(f"AMBIGUOUS_EFFECTIVE_VERSION:{branch_id}")
            else:
                rules.append(first[0])
        if any(item.startswith("AMBIGUOUS_EFFECTIVE_VERSION") for item in flags):
            rules = []
        results.append(
            _ReleaseScopeSelection(
                selection=RuleSelection(rules=rules, data_quality_flags=flags),
                temporal=temporal,
                **common,
            )
        )
    return results


def _unknown_scope_evaluation(
    *,
    context: _ReleaseScopeSelection,
    release_id: str,
    concept_id: str,
    service_date: date | None,
) -> EligibilityEvaluation:
    """为只有某一 scope 超期/缺版的情况生成独立 fail-closed 结果。"""

    temporal = context.temporal
    display_label = (
        "医保支付限定"
        if context.policy_scope == "INSURANCE_PAYMENT"
        else "指南适应证"
    )
    return _unknown_evaluation(
        concept_id=concept_id,
        data_quality_flags=context.selection.data_quality_flags,
        scope_label=display_label,
    ).model_copy(
        update={
            "release_id": release_id,
            "drug_concept_id": concept_id,
            "policy_scope": context.policy_scope,
            "source_type": context.policy_scope,
            "policy_scope_display_label": display_label,
            "source_document_ids": list(context.source_document_ids),
            "source_fragment_ids": list(context.source_fragment_ids),
            "source_versions": list(context.source_versions),
            "rule_effective_from": temporal.effective_from if temporal else None,
            "rule_effective_to": temporal.effective_to if temporal else None,
            "evaluated_service_date": service_date,
            "temporal_applicability": (
                temporal.temporal_applicability.value if temporal else None
            ),
            "temporal_warning": temporal.warning if temporal else "",
            "effective_date_enforced": (
                temporal.effective_date_enforced if temporal else True
            ),
        }
    )


def _self_pay_scope_evaluation(
    *,
    context: _ReleaseScopeSelection,
    release_id: str,
    concept_id: str,
    record: dict[str, Any],
    service_date: date | None,
) -> EligibilityEvaluation:
    """published release 中自费只短路医保支付 scope。"""

    temporal = context.temporal
    evaluation = _self_pay_evaluation(concept_id=concept_id, record=record)
    return evaluation.model_copy(
        update={
            "release_id": release_id,
            "drug_concept_id": concept_id,
            "policy_scope": "INSURANCE_PAYMENT",
            "source_type": "INSURANCE_PAYMENT",
            "policy_scope_display_label": "医保支付限定",
            "source_document_ids": list(context.source_document_ids),
            "source_fragment_ids": list(context.source_fragment_ids),
            "source_versions": list(context.source_versions),
            "rule_effective_from": temporal.effective_from if temporal else None,
            "rule_effective_to": temporal.effective_to if temporal else None,
            "evaluated_service_date": service_date,
            "temporal_applicability": (
                temporal.temporal_applicability.value if temporal else None
            ),
            "temporal_warning": temporal.warning if temporal else "",
            "effective_date_enforced": (
                temporal.effective_date_enforced if temporal else True
            ),
            "data_quality_flags": sorted(
                set(
                    evaluation.data_quality_flags
                    + context.selection.data_quality_flags
                )
            ),
        }
    )


def _deduplicate_oncology_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """RD04 双 scope 的 bulk 命中只生成一个 patient/drug 求值候选。"""
    grouped: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]] = {}
    for match in matches:
        key = (
            str(match.get("generic_name") or ""),
            tuple(sorted(str(item) for item in match.get("fee_codes", []) if item)),
            tuple(sorted(str(item) for item in match.get("fee_names", []) if item)),
        )
        existing = grouped.get(key)
        scope = str(match.get("source_type") or "")
        if existing is None:
            existing = dict(match)
            existing["policy_scope_candidates"] = [scope] if scope else []
            grouped[key] = existing
        elif scope and scope not in existing["policy_scope_candidates"]:
            existing["policy_scope_candidates"].append(scope)
    for item in grouped.values():
        item["policy_scope_candidates"] = sorted(item["policy_scope_candidates"])
    return [grouped[key] for key in sorted(grouped)]


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
    ]
    assessments: dict[str, CriterionAssessment] = {}
    for node in _leaf_nodes(rule.condition_tree):
        if node.criterion_type == "diagnosis":
            item = _diagnosis_assessment(
                node, diagnoses, applicable_records, service_date
            )
        elif node.criterion_type == "histology":
            terms = [str(value) for value in node.expected.get("includes", [])]
            negative = ""
            if len(terms) == 1 and not terms[0].startswith("非"):
                negative = rf"非{re.escape(terms[0])}"
            item = _term_assessment(
                node,
                _combined_text_records(applicable_records, diagnoses),
                terms=terms,
                service_date=service_date,
                label="组织学",
                negative_pattern=negative,
            )
        elif node.criterion_type == "stage":
            item = _stage_assessment(
                node, applicable_records, diagnoses, service_date
            )
        elif node.criterion_type == "disease_status":
            item = _treatment_status_assessment(
                node, applicable_records, service_date
            )
        elif node.criterion_type == "resectability":
            item = _resectability_assessment(
                node, applicable_records, service_date
            )
        elif node.criterion_type == "age":
            item = _age_assessment(node, applicable_records, service_date)
        elif node.criterion_type == "sex":
            expected = str(node.expected.get("equals") or "")
            positive = (
                ["女性", "性别：女", "性别:女"]
                if expected == "female"
                else ["男性", "性别：男", "性别:男"]
            )
            negative = (
                r"男性|性别\s*[:：]\s*男"
                if expected == "female"
                else r"女性|性别\s*[:：]\s*女"
            )
            item = _term_assessment(
                node,
                applicable_records,
                terms=positive,
                service_date=service_date,
                label="性别",
                negative_pattern=negative,
            )
        elif node.criterion_type == "menopausal_status":
            item = _term_assessment(
                node,
                applicable_records,
                terms=["绝经后"],
                service_date=service_date,
                label="绝经状态",
                negative_pattern=r"未绝经|绝经前",
            )
        elif node.criterion_type == "prior_therapy":
            item = _prior_therapy_assessment(
                node, applicable_records, regimens, service_date
            )
        elif node.criterion_type == "therapy_count":
            item = _therapy_count_assessment(
                node, applicable_records, regimens, service_date
            )
        elif node.criterion_type == "line_of_therapy":
            item = _line_of_therapy_assessment(
                node, applicable_records, regimens, service_date
            )
        elif node.criterion_type == "treatment_status":
            item = _treatment_status_assessment(
                node, applicable_records, service_date
            )
        elif node.criterion_type == "combination_requirement":
            item = _combination_assessment(
                node, applicable_records, regimens, service_date
            )
        elif node.criterion_type == "surgery_status":
            item = _intervention_status_assessment(
                node,
                applicable_records,
                service_date,
                intervention="surgery",
            )
        elif node.criterion_type == "radiotherapy_status":
            item = _intervention_status_assessment(
                node,
                applicable_records,
                service_date,
                intervention="radiotherapy",
            )
        elif node.criterion_type == "transplant_eligibility":
            item = _transplant_assessment(
                node, applicable_records, service_date
            )
        elif node.criterion_type == "time_window":
            item = _time_window_assessment(
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
                policy_context=str(
                    node.expected.get("policy_context")
                    or "disitamab-urothelial-insurance"
                ),
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


def _evaluate_runtime_rule(
    rule: EligibilityRule,
    *,
    diagnoses: list[dict[str, Any]],
    records: list[dict[str, Any]],
    regimens: list[RegimenResolution],
    service_date: date,
    pathology_path: Path,
    cancer_context: str,
    enforce_effective_date: bool,
    published_release: bool,
) -> EligibilityEvaluation:
    rule_temporal = (
        select_temporal_policy(
            service_date=service_date,
            effective_from=rule.metadata.effective_from,
            effective_to=rule.metadata.effective_to,
        )
        if published_release and rule.metadata.effective_to is not None
        else None
    )
    assessments = _evaluate_rule_leaves(
        rule,
        diagnoses=diagnoses,
        records=records,
        regimens=regimens,
        service_date=service_date,
        pathology_path=pathology_path,
        cancer_context=cancer_context,
        enforce_effective_date=(
            rule_temporal.effective_date_enforced
            if rule_temporal is not None
            else enforce_effective_date
        ),
    )
    evaluation = evaluate_rule(rule, assessments)
    flags = list(evaluation.data_quality_flags)
    for regimen in regimens:
        flags.extend(regimen.conflicts)
        if regimen.temporal_conflict:
            flags.append("治疗叙述年份与就诊/收费年份冲突")
    # legacy configs 才受 enforce_effective_date 开关控制；published release
    # 始终使用自身的非对称时间策略。
    if (
        not published_release
        and not enforce_effective_date
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
    return evaluation.model_copy(
        update={
            "data_quality_flags": sorted(set(flags)),
            "temporal_applicability": (
                rule_temporal.temporal_applicability.value
                if rule_temporal is not None
                else evaluation.temporal_applicability
            ),
            "temporal_warning": (
                rule_temporal.warning
                if rule_temporal is not None
                else evaluation.temporal_warning
            ),
            "effective_date_enforced": (
                rule_temporal.effective_date_enforced
                if rule_temporal is not None
                else evaluation.effective_date_enforced
            ),
        }
    )


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
    for match in _deduplicate_oncology_matches(matches):
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
        if self_pay_record and not rules_asset.metadata.release_id:
            # legacy singular 资产保持旧语义：明确自费后整条医保资格直接 CLEAN。
            evaluations = [
                _self_pay_evaluation(
                    concept_id=concept_id,
                    record=self_pay_record,
                )
            ]
        elif rules_asset.metadata.release_id:
            evaluations = []
            scope_contexts = _release_rule_selections(
                rules_asset,
                drug_concept_id=concept_id,
                service_date=service_date,
            )
            for context in scope_contexts:
                if (
                    self_pay_record
                    and context.policy_scope == "INSURANCE_PAYMENT"
                ):
                    scoped_evaluations = [
                        _self_pay_scope_evaluation(
                            context=context,
                            release_id=rules_asset.metadata.release_id,
                            concept_id=concept_id,
                            record=self_pay_record,
                            service_date=service_date,
                        )
                    ]
                elif service_date is None:
                    scoped_evaluations = [
                        _unknown_scope_evaluation(
                            context=context,
                            release_id=rules_asset.metadata.release_id,
                            concept_id=concept_id,
                            service_date=None,
                        )
                    ]
                else:
                    scoped_evaluations = [
                        _evaluate_runtime_rule(
                            rule,
                            diagnoses=diagnoses,
                            records=records,
                            regimens=regimens,
                            service_date=service_date,
                            pathology_path=pathology_path,
                            cancer_context=cancer_context,
                            enforce_effective_date=enforce_effective_date,
                            published_release=True,
                        )
                        for rule in context.selection.rules
                    ]
                    if not scoped_evaluations:
                        scoped_evaluations = [
                            _unknown_scope_evaluation(
                                context=context,
                                release_id=rules_asset.metadata.release_id,
                                concept_id=concept_id,
                                service_date=service_date,
                            )
                        ]
                evaluations.extend(scoped_evaluations)
            if not scope_contexts:
                evaluations = [
                    _unknown_evaluation(
                        concept_id=concept_id,
                        data_quality_flags=["NO_APPROVED_POLICY_SCOPE"],
                    ).model_copy(
                        update={
                            "release_id": rules_asset.metadata.release_id,
                            "drug_concept_id": concept_id,
                            "source_document_ids": sorted(
                                {
                                    item.source_id
                                    for item in rules_asset.metadata.source_refs
                                }
                            ),
                            "source_fragment_ids": sorted(
                                {
                                    item.source_fragment_id
                                    for item in rules_asset.metadata.source_refs
                                    if item.source_fragment_id
                                }
                            ),
                            "evaluated_service_date": service_date,
                        }
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
                evaluations = [
                    _evaluate_runtime_rule(
                        rule,
                        diagnoses=diagnoses,
                        records=records,
                        regimens=regimens,
                        service_date=service_date,
                        pathology_path=pathology_path,
                        cancer_context=cancer_context,
                        enforce_effective_date=enforce_effective_date,
                        published_release=False,
                    )
                    for rule in selection.rules
                ]
                if not evaluations:
                    evaluations = [
                        _unknown_evaluation(
                            concept_id=concept_id,
                            data_quality_flags=selection.data_quality_flags,
                        )
                    ]
        branch_evaluations = list(evaluations)
        patient_context = {
            "age": _age(records),
            "multi_line_treatment": bool(
                _find_text(records, r"多线治疗|多次治疗|第[二三四五六七八九十\d]+线")
            ),
        }
        if rules_asset.metadata.release_id:
            by_scope: dict[str, list[EligibilityEvaluation]] = {}
            for item in evaluations:
                by_scope.setdefault(str(item.policy_scope or "UNSCOPED"), []).append(item)
            evaluations = [
                _best_evaluation(items)
                for _, items in sorted(by_scope.items())
            ]
            evaluations = [
                item.model_copy(
                    update={
                        "documentation_suggestions": generate_documentation_suggestions(
                            item,
                            patient_context=patient_context,
                            templates=guidance_templates,
                        ),
                        "evaluated_service_date": service_date,
                    }
                )
                for item in evaluations
            ]
            selected = _worst_evaluation(evaluations)
            scoped_snapshots = [
                item.as_scope_evaluation()
                for item in evaluations
                if item.release_id is not None and item.policy_scope is not None
            ]
            if scoped_snapshots:
                selected = EligibilityEvaluation.model_validate(
                    {
                        **selected.model_dump(mode="json", exclude={"scope_evaluations"}),
                        "scope_evaluations": [
                            item.model_dump(mode="json") for item in scoped_snapshots
                        ],
                    }
                )
        else:
            selected = _best_evaluation(evaluations)
            selected = selected.model_copy(
                update={
                    "documentation_suggestions": generate_documentation_suggestions(
                        selected,
                        patient_context=patient_context,
                        templates=guidance_templates,
                    ),
                    "evaluated_service_date": service_date,
                    "effective_date_enforced": enforce_effective_date,
                }
            )
            evaluations = [
                selected
                if item.indication_branch_id == selected.indication_branch_id
                else item
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
                "policy_scope_candidates": match.get("policy_scope_candidates", []),
                "regimen_evidence": [item.model_dump(mode="json") for item in regimens],
                "eligibility_evaluations": [
                    item.model_dump(mode="json") for item in evaluations
                ],
                "branch_evaluations": [
                    item.model_dump(mode="json") for item in branch_evaluations
                ],
                "selected_eligibility_evaluation": selected.model_dump(mode="json"),
            }
        )

    selected_overall = _worst_evaluation(all_evaluations) if all_evaluations else None
    if selected_overall is not None:
        scope_by_key = {}
        for evaluation in all_evaluations:
            for scope in evaluation.scope_evaluations:
                key = (scope.drug_concept_id, scope.policy_scope)
                previous = scope_by_key.get(key)
                if previous is None:
                    scope_by_key[key] = scope
                    continue
                rank = {
                    AuditDisposition.NO_VIOLATION_FOUND: 0,
                    AuditDisposition.REVIEW_REQUIRED: 1,
                    AuditDisposition.VIOLATION_FOUND: 2,
                }
                if rank[scope.audit_disposition] > rank[previous.audit_disposition]:
                    scope_by_key[key] = scope
        if scope_by_key:
            selected_overall = EligibilityEvaluation.model_validate(
                {
                    **selected_overall.model_dump(
                        mode="json", exclude={"scope_evaluations"}
                    ),
                    "scope_evaluations": [
                        scope_by_key[key].model_dump(mode="json")
                        for key in sorted(scope_by_key)
                    ],
                }
            )
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
