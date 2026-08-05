# -*- coding: utf-8 -*-
"""医院公开解释的单一确定性投影；不读取或猜测 LLM 散文。"""

from __future__ import annotations

import re
from typing import Any, Iterable
from collections.abc import Mapping

from javert.promises.models import PromiseTrace

_INTERNAL_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])RD?\d{2,3}(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"\b(?:search_[a-z_]+|drug_audit_lookup|tool_calls?|gate|run_id|ownership_id)\b", re.IGNORECASE),
    re.compile(r"\b(?:VIOLATION|INCONCLUSIVE|CLEAN)\b", re.IGNORECASE),
    re.compile(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b"),
)


def sanitize_public_text(value: str | None) -> str:
    text = str(value or "")
    for pattern in _INTERNAL_PATTERNS:
        text = pattern.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ，,；;：:")


def public_promise_summary(trace: PromiseTrace | dict[str, Any] | None) -> dict[str, Any] | None:
    if trace is None:
        return None
    if isinstance(trace, dict):
        try:
            trace = PromiseTrace.model_validate(trace)
        except (TypeError, ValueError):
            return None
    # 不公开 Promise ID / kind / 内部 reason code；详细 trace 仍保留在兼容存储字段。
    return {
        "locked": trace.finality == "LOCKED",
        "historical_conflict": trace.historical_conflict,
    }


def _get(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def present_public_explanation(
    run: Any,
    meta: dict[str, Any] | None = None,
    hits: Iterable[Any] = (),
) -> dict[str, Any]:
    """只从持久化结构字段、真实 hit 和最小 Promise trace 投影医生解释。"""

    meta = meta or {}
    hit_items = list(hits)
    raw_trace = _get(run, "promise_trace")
    trace = raw_trace if isinstance(raw_trace, PromiseTrace) else None
    if isinstance(raw_trace, dict):
        try:
            trace = PromiseTrace.model_validate(raw_trace)
        except (TypeError, ValueError):
            trace = None
    verdict = str(_get(run, "verdict", ""))

    if verdict == "VIOLATION":
        conclusion = {
            "label": "发现需核查行为",
            "summary": "现有结构化事实支持该项进入医保合规复核。",
        }
    elif verdict == "CLEAN":
        if trace is not None and trace.finality == "LOCKED":
            net_qty = trace.facts.get("net_qty")
            conclusion = {
                "label": "未发现违规",
                "summary": f"退费抵消后目标项目净数量为 {net_qty} 次，未超过多次检查边界。",
            }
        else:
            conclusion = {"label": "未发现违规", "summary": "现有结构化事实未支持违规结论。"}
    else:
        conclusion = {"label": "待人工复核", "summary": "现有资料不足以自动定性。"}

    question = sanitize_public_text(meta.get("question"))
    behavior = sanitize_public_text(meta.get("behavior_name"))
    audit_items = [question or behavior] if (question or behavior) else []

    charge_facts: list[dict[str, Any]] = []
    if trace is not None and trace.finality == "LOCKED":
        charge_facts.append(
            {
                "fact": "目标收费项目退费后净数量",
                "value": trace.facts.get("net_qty"),
                "unit": "次",
                "refund_count": trace.facts.get("refund_count"),
            }
        )
    for hit in hit_items:
        if getattr(hit, "source", "") not in {"fee", "drug"}:
            continue
        matched_name = sanitize_public_text(getattr(hit, "matched_fee_name", ""))
        if not matched_name:
            continue
        fact = {
            "name": sanitize_public_text(getattr(hit, "name", "")) or matched_name,
            "matched_charge_name": matched_name,
        }
        code = str(getattr(hit, "code_nat", "") or getattr(hit, "code_local", ""))
        if code:
            fact["code"] = code
        if fact not in charge_facts:
            charge_facts.append(fact)

    basis: list[str] = []
    clinical_evidence: list[dict[str, str]] = []
    review_needs: list[str] = []
    if trace is not None and trace.finality == "LOCKED":
        basis.append("按退费后的收费净数量核对同一项目是否达到多次检查边界。")
        if trace.historical_conflict:
            review_needs.append("本次确定性结果与历史结果不同，建议核对历史审核依据。")
    for hit in hit_items:
        restriction = sanitize_public_text(getattr(hit, "restriction", ""))
        if restriction and restriction not in basis:
            basis.append(restriction)
        if getattr(hit, "source", "") in {"lab", "exam", "note"}:
            anchor = getattr(hit, "anchor", None)
            if anchor is None or getattr(anchor, "unresolved", True):
                continue
            item = {
                "type": {"lab": "检验", "exam": "检查", "note": "病历"}.get(
                    getattr(hit, "source", ""), "资料"
                ),
                "name": sanitize_public_text(getattr(hit, "name", "")),
            }
            if item["name"] and item not in clinical_evidence:
                clinical_evidence.append(item)
    if verdict == "INCONCLUSIVE":
        review_needs.append("请人工核对相关收费与临床资料。")
    if verdict == "VIOLATION" and not charge_facts and not clinical_evidence:
        review_needs.append("当前缺少可公开投影的结构化事实，请查阅原始资料确认。")

    return {
        "conclusion": conclusion,
        "audit_items": audit_items,
        "charge_facts": charge_facts,
        "basis": basis,
        "clinical_evidence": clinical_evidence,
        "review_needs": review_needs,
    }
