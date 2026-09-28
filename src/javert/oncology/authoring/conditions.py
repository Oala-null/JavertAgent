"""确定性来源分支切分、条件候选生成与树结构 QA。"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable

from .ids import branch_id, node_id, stable_id
from .models import (
    CandidateDisposition,
    CombinationRequirement,
    ConditionNodeCandidate,
    CriterionOperator,
    CriterionType,
    IndicationBranchCandidate,
    NodeKind,
    SourceFragment,
    SourceRule,
    TargetKind,
)


_NUMBERED_RE = re.compile(
    r"(?:(?<=^)|(?<=[;；。\n:：】]))\s*"
    r"(?:[（(]\d{1,2}[）)](?:[.．、])?|\d{1,2}[）)][.．、]?|"
    r"\d{1,2}[.．、]|[一二三四五六七八九十]+[、.．])"
)
_SECTION_RE = re.compile(r"【[^】]{2,20}】")
_DISEASE_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9()+-]{2,40}"
    r"(?:癌|瘤|白血病|淋巴瘤|肉瘤|间皮瘤|间质性肺疾病|巨球蛋白血症|淀粉样变|癌性胸腹水)"
)
_SPECIAL_DISEASE_TERMS = (
    "癌性胸腹水",
    "特发性肺纤维化",
    "子宫内膜异位症",
    "获得性免疫缺陷综合征",
    "厌食症",
)
_MARKER_RE = re.compile(r"\b(?:HER2|PD-?L1|EGFR|ALK|ROS1|BRAF|BRCA[12]?|PIK3CA|AKT1|PTEN|EZH2|MSI-H|dMMR)\b", re.I)
_COMBINATION_RE = re.compile(r"(?<!可)(?<!时)联合(?!或不联合)\s*([^,，;；。]{1,30})")
_WITH_OR_WITHOUT_RE = re.compile(r"联合或不联合\s*([^,，;；。]{1,30})")
_OPTIONAL_COMBINATION_RE = re.compile(r"(?:可|必要时)联合\s*([^,，;；。]{1,30})")
_WITH_COMBINATION_RE = re.compile(
    r"与\s*([^,，;；。:：]{1,50}?)\s*联合(?:使用|用药|治疗|给药|用于|联用)"
)


def _substantive_prefix(text: str) -> bool:
    cleaned = _SECTION_RE.sub("", text)
    cleaned = re.sub(r"[\s:：;；。,.，限适应证本品]+", "", cleaned)
    return len(cleaned) >= 4


def _standalone_heading(text: str) -> bool:
    cleaned = _SECTION_RE.sub("", text).strip()
    return bool(re.fullmatch(r"[^。；;]{2,40}(?:适应证|适用范围)\s*[:：]?", cleaned))


def _global_qualifier_prefix(text: str) -> tuple[int, int, int] | None:
    first = next(iter(_NUMBERED_RE.finditer(text)), None)
    if first is None:
        return None
    prefix = text[: first.start()]
    months = re.search(r"支付不超过\s*(\d{1,3})\s*个月", prefix)
    if months is None:
        return None
    return 0, first.start(), int(months.group(1))


def _numbered_spans(text: str, *, offset: int = 0) -> list[tuple[int, int]]:
    matches = list(_NUMBERED_RE.finditer(text))
    if not matches:
        return [(offset, offset + len(text))] if text.strip() else []
    spans: list[tuple[int, int]] = []
    prefix = text[: matches[0].start()]
    if (
        _substantive_prefix(prefix)
        and not _standalone_heading(prefix)
        and _global_qualifier_prefix(text) is None
    ):
        spans.append((offset, offset + matches[0].start()))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if text[start:end].strip() and not _standalone_heading(text[start:end]):
            spans.append((offset + start, offset + end))
    return spans


def _branch_spans(text: str) -> list[tuple[int, int]]:
    sections = list(_SECTION_RE.finditer(text))
    if len(sections) < 2:
        return _numbered_spans(text)
    spans: list[tuple[int, int]] = []
    if _substantive_prefix(text[: sections[0].start()]):
        spans.extend(_numbered_spans(text[: sections[0].start()]))
    for index, section in enumerate(sections):
        start = section.start()
        end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
        spans.extend(_numbered_spans(text[start:end], offset=start))
    return spans


def split_source_rule(rule: SourceRule, fragment: SourceFragment) -> list[IndicationBranchCandidate]:
    text = fragment.original_text.strip()
    spans = _branch_spans(text) or [(0, len(text))]
    return [
        IndicationBranchCandidate(
            branch_id=branch_id(rule.revision_id, f"{start}:{end}", ordinal),
            rule_revision_id=rule.revision_id,
            source_fragment_id=fragment.source_fragment_id,
            ordinal=ordinal,
            source_text=text[start:end].strip(),
            source_span_start=start,
            source_span_end=end,
            disposition=CandidateDisposition.IN_REVIEW,
        )
        for ordinal, (start, end) in enumerate(spans, start=1)
    ]


def _clean_combination_target(value: str) -> str:
    text = re.sub(r"\s+", "", value).strip(" ,，;；。:：")
    text = re.sub(r"^以(?=[\u4e00-\u9fffA-Za-z0-9])", "", text)
    text = re.split(r"适用于|用于|作为", text, maxsplit=1)[0]
    text = re.split(
        r"治疗(?=达到|既往|新诊断|局部|晚期|转移|患者|后|期间|失败|复发)",
        text,
        maxsplit=1,
    )[0]
    text = re.split(r"新辅助治疗|围手术期治疗|并在手术后|后达", text, maxsplit=1)[0]
    text = re.split(r"为基础", text, maxsplit=1)[0]
    text = re.sub(r"(?:一|二|三)线$", "", text)
    return text.strip(" ,，;；。:：")


def _leaf_specs(text: str) -> list[tuple[CriterionType, CriterionOperator, TargetKind, str, object, CombinationRequirement | None]]:
    specs: list[tuple[CriterionType, CriterionOperator, TargetKind, str, object, CombinationRequirement | None]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: CriterionType, operator: CriterionOperator, target_kind: TargetKind, target: str, value: object, requirement: CombinationRequirement | None = None) -> None:
        key = (kind, str(value))
        if key not in seen:
            seen.add(key)
            specs.append((kind, operator, target_kind, target, value, requirement))

    for disease in _SPECIAL_DISEASE_TERMS:
        if disease in text:
            add(CriterionType.DIAGNOSIS, CriterionOperator.CONTAINS, TargetKind.VALUE, "", disease)
    for disease in _DISEASE_RE.findall(text):
        add(CriterionType.DIAGNOSIS, CriterionOperator.CONTAINS, TargetKind.VALUE, "", disease)
    for marker in _MARKER_RE.findall(text):
        around = text[max(0, text.upper().find(marker.upper()) - 8):text.upper().find(marker.upper()) + len(marker) + 8]
        negative = bool(re.search(r"阴性|无突变|野生型", around))
        add(
            CriterionType.BIOMARKER,
            CriterionOperator.EQUALS,
            TargetKind.CONCEPT,
            stable_id("marker", marker.upper()),
            {
                "marker_id": marker.upper(),
                "status": "negative" if negative else "positive",
            },
        )
    if "局部晚期" in text:
        add(CriterionType.STAGE, CriterionOperator.EQUALS, TargetKind.VALUE, "", "locally_advanced")
    if "转移" in text:
        add(CriterionType.STAGE, CriterionOperator.EQUALS, TargetKind.VALUE, "", "metastatic")
    if "不可切除" in text or "不能手术" in text:
        add(CriterionType.RESECTABILITY, CriterionOperator.EQUALS, TargetKind.VALUE, "", "unresectable")
    if "成人" in text or "成年" in text:
        add(CriterionType.AGE, CriterionOperator.GTE, TargetKind.VALUE, "", 18)
    if "女性" in text:
        add(CriterionType.SEX, CriterionOperator.EQUALS, TargetKind.VALUE, "", "female")
    for histology in ("鳞状", "非鳞状", "腺癌", "上皮样", "非上皮样"):
        if histology in text:
            add(CriterionType.HISTOLOGY, CriterionOperator.CONTAINS, TargetKind.VALUE, "", histology)
    if "绝经后" in text:
        add(CriterionType.MENOPAUSAL_STATUS, CriterionOperator.EQUALS, TargetKind.VALUE, "", "postmenopausal")
    if "既往" in text or "治疗后" in text:
        add(CriterionType.PRIOR_THERAPY, CriterionOperator.EXISTS, TargetKind.VALUE, "", True)
    line = re.search(r"(?:至少)?(?:接受过)?([一二三四五六七八九十\d]+)种(?:系统性)?治疗", text)
    if line:
        chinese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
        value = int(line.group(1)) if line.group(1).isdigit() else chinese.get(line.group(1), 1)
        add(CriterionType.THERAPY_COUNT, CriterionOperator.GTE, TargetKind.VALUE, "", value)
    for ordinal, value in (("一线", 1), ("二线", 2), ("三线", 3)):
        if ordinal in text:
            add(CriterionType.LINE_OF_THERAPY, CriterionOperator.EQUALS, TargetKind.VALUE, "", value)
    if "复发" in text:
        add(CriterionType.DISEASE_STATUS, CriterionOperator.EQUALS, TargetKind.VALUE, "", "relapsed")
    if "难治" in text:
        add(CriterionType.DISEASE_STATUS, CriterionOperator.EQUALS, TargetKind.VALUE, "", "refractory")
    if "新诊断" in text or "初诊" in text:
        add(CriterionType.DISEASE_STATUS, CriterionOperator.EQUALS, TargetKind.VALUE, "", "newly_diagnosed")
    if "进行性表型" in text:
        add(CriterionType.DISEASE_STATUS, CriterionOperator.EQUALS, TargetKind.VALUE, "", "progressive_phenotype")
    if "进展" in text:
        add(CriterionType.TREATMENT_STATUS, CriterionOperator.EQUALS, TargetKind.VALUE, "", "progressed")
    combination_patterns = (
        (_WITH_OR_WITHOUT_RE, CombinationRequirement.WITH_OR_WITHOUT),
        (_OPTIONAL_COMBINATION_RE, CombinationRequirement.OPTIONAL),
        (_WITH_COMBINATION_RE, CombinationRequirement.REQUIRED),
        (_COMBINATION_RE, CombinationRequirement.REQUIRED),
    )
    for pattern, requirement in combination_patterns:
        for match in pattern.finditer(text):
            raw_target = match.group(1)
            if re.search(r"(?:化疗|治疗)后(?:达|进展|复发|失败|完成)", raw_target):
                continue
            target_text = _clean_combination_target(raw_target)
            if target_text in {
                "", "使", "使用", "应用", "治疗", "治疗时", "用药", "给药", "联用",
                "辅助治疗", "的辅助治疗", "新辅助治疗", "方案治疗",
            } or target_text.startswith(("使用", "应用", "治疗", "用药", "给药")):
                continue
            if any(
                token in target_text
                for token in ("方案", "CHOP", "FOLFOX", "FOLFIRI", "CRT", "OFS")
            ) or any(separator in target_text for separator in ("和", "、", "及", "或")):
                target_kind = TargetKind.REGIMEN
            elif any(
                token in target_text
                for token in ("类", "抑制剂", "激动剂", "化疗", "放疗", "内分泌")
            ):
                target_kind = TargetKind.CLASS
            else:
                target_kind = TargetKind.CONCEPT
            add(
                CriterionType.COMBINATION_REQUIREMENT,
                CriterionOperator.CONTAINS,
                target_kind,
                stable_id("target", target_text),
                target_text,
                requirement,
            )
    months = re.search(r"(\d{1,3})\s*个月内", text)
    if months:
        add(CriterionType.TIME_WINDOW, CriterionOperator.WITHIN_DAYS, TargetKind.VALUE, "", int(months.group(1)) * 30)
    if "移植" in text:
        operator = CriterionOperator.NOT_EQUALS if "不适合" in text or "无法" in text else CriterionOperator.EQUALS
        add(CriterionType.TRANSPLANT_ELIGIBILITY, operator, TargetKind.VALUE, "", "eligible")
    if re.search(r"术后|手术切除后|接受过手术|经(?:过)?手术|已行手术", text):
        add(CriterionType.SURGERY_STATUS, CriterionOperator.EXISTS, TargetKind.VALUE, "", True)
    if "放疗" in text:
        add(CriterionType.RADIOTHERAPY_STATUS, CriterionOperator.EXISTS, TargetKind.VALUE, "", True)
    if "医生评估" in text or "临床评估" in text or "经评估" in text:
        add(CriterionType.CLINICIAN_ASSESSMENT, CriterionOperator.EXISTS, TargetKind.VALUE, "", True)
    if "NYHA IIIB" in text and "Mayo IIIB" in text:
        add(
            CriterionType.CLINICIAN_ASSESSMENT,
            CriterionOperator.NOT_IN,
            TargetKind.VALUE,
            "",
            ["NYHA IIIB", "NYHA IV", "Mayo IIIB"],
        )
    return specs


def _expected_payload(
    kind: CriterionType,
    operator: CriterionOperator,
    target_kind: TargetKind,
    target_id: str,
    value: object,
    requirement: CombinationRequirement | None,
) -> dict[str, object]:
    """把作者端标量归一为 release/runtime 可读取的稳定 JSON object。"""
    if kind in {CriterionType.DIAGNOSIS, CriterionType.HISTOLOGY}:
        return {"includes": [value]}
    if kind == CriterionType.BIOMARKER:
        if isinstance(value, dict):
            return value
        return {"marker_id": target_id, "status": value}
    if kind == CriterionType.COMBINATION_REQUIREMENT:
        return {
            "target_kind": target_kind.value,
            "target_id": target_id,
            "display_name": value,
            "requirement": (requirement or CombinationRequirement.REQUIRED).value,
        }
    if operator == CriterionOperator.GTE:
        return {"gte": value}
    if operator == CriterionOperator.LTE:
        return {"lte": value}
    if operator == CriterionOperator.WITHIN_DAYS:
        return {"within_days": value}
    if operator == CriterionOperator.EXISTS:
        return {"exists": value}
    if operator == CriterionOperator.NOT_EXISTS:
        return {"not_exists": value}
    if operator == CriterionOperator.NOT_EQUALS:
        return {"not_equals": value}
    if operator == CriterionOperator.IN:
        return {"in": value}
    if operator == CriterionOperator.NOT_IN:
        return {"not_in": value}
    if operator == CriterionOperator.CONTAINS:
        return {"contains": value}
    if operator == CriterionOperator.NOT_CONTAINS:
        return {"not_contains": value}
    return {"equals": value}


def build_shared_qualifier_candidates(
    branch: IndicationBranchCandidate,
    fragment: SourceFragment,
    existing_nodes: Iterable[ConditionNodeCandidate],
) -> list[ConditionNodeCandidate]:
    """把编号列表前的全局支付限定复制到各分支的 ALL 根，避免丢失或错误 OR。"""
    qualifier = _global_qualifier_prefix(fragment.original_text)
    if qualifier is None:
        return []
    start, end, months = qualifier
    nodes = list(existing_nodes)
    root = next(
        item
        for item in nodes
        if item.parent_node_id is None and item.node_kind == NodeKind.ALL
    )
    sibling_order = 1 + max(
        (
            item.sibling_order
            for item in nodes
            if item.parent_node_id == root.node_id
        ),
        default=0,
    )
    expected = {
        "within_days": months * 30,
        "window_kind": "maximum_payment_duration",
    }
    return [
        ConditionNodeCandidate(
            node_id=node_id(
                branch.branch_id,
                "root/shared-payment-window",
                {
                    "criterion_type": CriterionType.TIME_WINDOW,
                    "operator": CriterionOperator.WITHIN_DAYS,
                    "value": expected,
                },
            ),
            branch_id=branch.branch_id,
            parent_node_id=root.node_id,
            sibling_order=sibling_order,
            node_kind=NodeKind.LEAF,
            criterion_type=CriterionType.TIME_WINDOW,
            operator=CriterionOperator.WITHIN_DAYS,
            target_kind=TargetKind.VALUE,
            expected_value=expected,
            source_fragment_id=fragment.source_fragment_id,
            source_span_start=start,
            source_span_end=end,
            disposition=branch.disposition,
        )
    ]


def build_condition_candidates(branch: IndicationBranchCandidate) -> list[ConditionNodeCandidate]:
    specs = _leaf_specs(branch.source_text)
    if not specs:
        specs = [(CriterionType.UNSUPPORTED, CriterionOperator.EXISTS, TargetKind.VALUE, "", branch.source_text, None)]
        branch.disposition = CandidateDisposition.UNSUPPORTED
    root_id = node_id(branch.branch_id, "root", {"kind": "ALL"})
    nodes = [
        ConditionNodeCandidate(
            node_id=root_id,
            branch_id=branch.branch_id,
            sibling_order=0,
            node_kind=NodeKind.ALL,
            source_fragment_id=branch.source_fragment_id,
            source_span_start=branch.source_span_start,
            source_span_end=branch.source_span_end,
            disposition=branch.disposition,
        )
    ]
    alternative_types = {
        CriterionType.DIAGNOSIS,
        CriterionType.HISTOLOGY,
        CriterionType.STAGE,
        CriterionType.DISEASE_STATUS,
    }
    indexed_specs = list(enumerate(specs, start=1))
    grouped_indexes: dict[CriterionType, list[tuple[int, tuple]]] = defaultdict(list)
    for index, spec in indexed_specs:
        if spec[0] in alternative_types:
            grouped_indexes[spec[0]].append((index, spec))

    root_order = 1
    emitted_groups: set[CriterionType] = set()

    def append_leaf(
        index: int,
        spec: tuple,
        *,
        parent_id: str,
        sibling_order: int,
    ) -> None:
        kind, operator, target_kind, target_id, value, requirement = spec
        nodes.append(
            ConditionNodeCandidate(
                node_id=node_id(branch.branch_id, f"root/{index}", {"criterion_type": kind, "operator": operator, "target_id": target_id, "value": value}),
                branch_id=branch.branch_id,
                parent_node_id=parent_id,
                sibling_order=sibling_order,
                node_kind=NodeKind.LEAF,
                criterion_type=kind,
                operator=operator,
                target_kind=target_kind,
                target_id=target_id,
                expected_value=_expected_payload(
                    kind,
                    operator,
                    target_kind,
                    target_id,
                    value,
                    requirement,
                ),
                combination_requirement=requirement,
                source_fragment_id=branch.source_fragment_id,
                source_span_start=branch.source_span_start,
                source_span_end=branch.source_span_end,
                disposition=branch.disposition,
            )
        )

    for index, spec in indexed_specs:
        kind = spec[0]
        grouped = grouped_indexes.get(kind, [])
        if len(grouped) > 1:
            if kind in emitted_groups:
                continue
            emitted_groups.add(kind)
            group_id = node_id(
                branch.branch_id,
                f"root/{kind.value}-any",
                {"kind": "ANY", "criterion_type": kind},
            )
            nodes.append(
                ConditionNodeCandidate(
                    node_id=group_id,
                    branch_id=branch.branch_id,
                    parent_node_id=root_id,
                    sibling_order=root_order,
                    node_kind=NodeKind.ANY,
                    source_fragment_id=branch.source_fragment_id,
                    source_span_start=branch.source_span_start,
                    source_span_end=branch.source_span_end,
                    disposition=branch.disposition,
                )
            )
            for child_order, (child_index, child_spec) in enumerate(grouped, start=1):
                append_leaf(
                    child_index,
                    child_spec,
                    parent_id=group_id,
                    sibling_order=child_order,
                )
        else:
            append_leaf(index, spec, parent_id=root_id, sibling_order=root_order)
        root_order += 1
    return nodes


def validate_condition_tree(nodes: Iterable[ConditionNodeCandidate], known_source_fragments: set[str], known_target_ids: set[str] | None = None) -> list[str]:
    nodes = list(nodes)
    errors: list[str] = []
    if not nodes:
        return ["TREE_EMPTY"]
    by_id = {item.node_id: item for item in nodes}
    children: dict[str, list[ConditionNodeCandidate]] = defaultdict(list)
    roots = [item for item in nodes if item.parent_node_id is None]
    if len(roots) != 1:
        errors.append("ROOT_COUNT")
    for item in nodes:
        if item.source_fragment_id not in known_source_fragments:
            errors.append(f"SOURCE_FRAGMENT_MISSING:{item.node_id}")
        if item.parent_node_id:
            if item.parent_node_id not in by_id:
                errors.append(f"PARENT_MISSING:{item.node_id}")
            else:
                children[item.parent_node_id].append(item)
        if known_target_ids is not None and item.target_id and item.target_id not in known_target_ids:
            errors.append(f"TARGET_MISSING:{item.node_id}")
    for parent, items in children.items():
        orders = [item.sibling_order for item in items]
        if len(orders) != len(set(orders)):
            errors.append(f"SIBLING_ORDER_DUPLICATE:{parent}")
    for item in nodes:
        has_children = bool(children.get(item.node_id))
        if item.node_kind == NodeKind.LEAF and has_children:
            errors.append(f"LEAF_HAS_CHILDREN:{item.node_id}")
        if item.node_kind != NodeKind.LEAF and not has_children:
            errors.append(f"AGGREGATE_NO_CHILDREN:{item.node_id}")
        seen: set[str] = set()
        current = item
        while current.parent_node_id and current.parent_node_id in by_id:
            if current.node_id in seen:
                errors.append(f"CYCLE:{item.node_id}")
                break
            seen.add(current.node_id)
            current = by_id[current.parent_node_id]
    return sorted(set(errors))


def coverage_partition(branches: Iterable[IndicationBranchCandidate]) -> dict[str, int]:
    counts = Counter(item.disposition.value for item in branches)
    return {key: counts.get(key, 0) for key in ("approved", "in_review", "rejected", "unsupported")}


def source_rule_coverage(branches: Iterable[IndicationBranchCandidate]) -> dict[str, object]:
    """将每个来源规则恰好投影到一个覆盖分区。"""
    grouped: dict[str, list[IndicationBranchCandidate]] = defaultdict(list)
    for branch in branches:
        grouped[branch.rule_revision_id].append(branch)
    rows = []
    for revision_id_, items in sorted(grouped.items()):
        values = {item.disposition for item in items}
        if values == {CandidateDisposition.APPROVED}:
            disposition = CandidateDisposition.APPROVED
        elif values == {CandidateDisposition.REJECTED}:
            disposition = CandidateDisposition.REJECTED
        elif values == {CandidateDisposition.UNSUPPORTED}:
            disposition = CandidateDisposition.UNSUPPORTED
        else:
            disposition = CandidateDisposition.IN_REVIEW
        rows.append(
            {
                "rule_revision_id": revision_id_,
                "disposition": disposition.value,
                "branch_count": len(items),
            }
        )
    counts = Counter(row["disposition"] for row in rows)
    return {
        "rows": rows,
        "counts": {
            key: counts.get(key, 0)
            for key in ("approved", "in_review", "rejected", "unsupported")
        },
        "total": len(rows),
    }
