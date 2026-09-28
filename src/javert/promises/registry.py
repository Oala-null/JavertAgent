# -*- coding: utf-8 -*-
"""受控 Promise evaluator registry；不接受表达式或动态代码。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from pydantic import Field

from javert.data.fee_netting import NetItem

from .models import PromiseDefinition, PromiseMatch, PromiseTrace, StrictModel


class RefundNetSingleParams(StrictModel):
    target_keywords: list[str] = Field(min_length=1)
    excluded_keywords: list[str] = Field(default_factory=list)
    max_net_qty: float = Field(default=1.0, ge=0, le=1)


Evaluator = Callable[
    [PromiseDefinition, str, Mapping[str, NetItem]], PromiseMatch | None
]


@dataclass(frozen=True)
class EvaluatorSpec:
    params_model: type[StrictModel]
    evaluate: Evaluator


def _contains_any(value: str, keywords: Iterable[str]) -> bool:
    folded = value.casefold()
    return any(keyword.casefold() in folded for keyword in keywords if keyword)


def evaluate_refund_net_single_clean(
    definition: PromiseDefinition,
    rule_id: str,
    net_items: Mapping[str, NetItem],
) -> PromiseMatch | None:
    """唯一目标费用组在真实退费后净量不大于 1 时锁定 CLEAN。"""

    if rule_id not in definition.scope.rule_ids:
        return None
    params = RefundNetSingleParams.model_validate(definition.params)
    candidates: list[NetItem] = []
    for item in net_items.values():
        searchable = " ".join(part for part in (item.name, item.code) if part)
        if not _contains_any(searchable, params.target_keywords):
            continue
        if _contains_any(searchable, params.excluded_keywords):
            continue
        candidates.append(item)
    if len(candidates) != 1:
        return None
    item = candidates[0]
    if not item.quantity_parseable or not item.has_refund:
        return None
    if item.net_qty > params.max_net_qty:
        return None
    return PromiseMatch(
        guarantee=definition.guarantee,
        trace=PromiseTrace(
            promise_id=definition.promise_id,
            version=definition.version,
            kind=definition.kind,
            finality=definition.finality,
            reason_code=definition.reason_code,
            facts={
                "net_qty": item.net_qty,
                "refund_count": item.refund_count,
            },
        ),
    )


EVALUATORS: dict[str, EvaluatorSpec] = {
    "refund-net-single-clean": EvaluatorSpec(
        params_model=RefundNetSingleParams,
        evaluate=evaluate_refund_net_single_clean,
    )
}


def validate_definition_registry(definition: PromiseDefinition) -> None:
    spec = EVALUATORS.get(definition.kind)
    if spec is None:
        raise ValueError(f"UNKNOWN_PROMISE_KIND:{definition.kind}")
    spec.params_model.model_validate(definition.params)


def evaluate_definition(
    definition: PromiseDefinition,
    rule_id: str,
    net_items: Mapping[str, NetItem],
) -> PromiseMatch | None:
    validate_definition_registry(definition)
    return EVALUATORS[definition.kind].evaluate(definition, rule_id, net_items)


@dataclass(frozen=True)
class TerminalEvaluation:
    match: PromiseMatch | None = None
    conflict_ids: tuple[str, ...] = ()


def evaluate_terminal_promises(
    definitions: Iterable[PromiseDefinition],
    rule_id: str,
    net_items: Mapping[str, NetItem],
) -> TerminalEvaluation:
    matches = [
        match
        for definition in definitions
        if definition.status == "active" and definition.phase == "decision_pre_llm"
        if (match := evaluate_definition(definition, rule_id, net_items)) is not None
    ]
    if not matches:
        return TerminalEvaluation()
    guarantees = {match.guarantee for match in matches}
    if len(guarantees) > 1:
        return TerminalEvaluation(
            conflict_ids=tuple(sorted(match.trace.promise_id for match in matches))
        )
    # 同保证重叠仍按稳定 Promise ID 选取；治理校验负责避免多个 active head。
    return TerminalEvaluation(
        match=sorted(matches, key=lambda match: match.trace.promise_id)[0]
    )
