# -*- coding: utf-8 -*-
"""公开审计短标题的确定性门控、回退与无内容指标。"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from collections.abc import Mapping
from typing import Any

from .rule import RULE_ID_BODY_PATTERN
from .result import AuditResult, Verdict

logger = logging.getLogger("javert.audit.headline")

HEADLINE_MIN_LENGTH = 15
HEADLINE_MAX_LENGTH = 60

_INTERNAL_PATTERNS = (
    re.compile(
        rf"(?<![A-Za-z0-9]){RULE_ID_BODY_PATTERN}(?![A-Za-z0-9])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:search_[a-z_]+|drug_audit_lookup|tool_calls?|"
        r"gate|precheck|run_id|ownership_id|patient_id)(?![A-Za-z0-9_])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:VIOLATION|INCONCLUSIVE|CLEAN)(?![A-Za-z0-9_])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])(?:aud|run|att|ownership)[-_][A-Za-z0-9_-]{4,}"
        r"(?![A-Za-z0-9_])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Fa-f0-9])[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[1-5][A-Fa-f0-9]{3}-"
        r"[89ABab][A-Fa-f0-9]{3}-[A-Fa-f0-9]{12}(?![A-Fa-f0-9])"
    ),
    re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+(?![A-Za-z0-9_])"),
)
_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LIST_PATTERN = re.compile(r"^\s*(?:[-*•]|\d+[.)、])\s*")
_REVIEW_MARKERS = ("疑似", "待核查", "待核", "待复核", "依据不足", "资料不足", "证据不足", "无法自动定性", "需人工复核")
_CLEAN_MARKERS = ("未发现违规", "未见违规", "未支持违规", "未形成可复核异常", "未超出", "未超过", "未触发")
_VIOLATION_MARKERS = ("违规", "不符合", "超出", "超范围", "重复收取", "重复收费", "不合理", "超过", "多收", "不应")

_METRICS: Counter[str] = Counter()
_METRICS_LOCK = threading.Lock()


def _get(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _clean_metadata(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ，,；;：:。")
    for pattern in _INTERNAL_PATTERNS:
        text = pattern.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ，,；;：:。")


def _record_metric(*, fallback_reason: str | None) -> None:
    with _METRICS_LOCK:
        _METRICS["generated_total"] += 1
        if fallback_reason is None:
            _METRICS["accepted_total"] += 1
        else:
            _METRICS["fallback_total"] += 1
            _METRICS[f"fallback_reason.{fallback_reason}"] += 1


def headline_metrics_snapshot() -> dict[str, int]:
    """返回进程内计数快照；不包含标题、患者或运行标识。"""
    with _METRICS_LOCK:
        return dict(_METRICS)


def reset_headline_metrics() -> None:
    """测试/进程重载用；不影响持久化结果。"""
    with _METRICS_LOCK:
        _METRICS.clear()


def validate_headline(
    value: str | None,
    verdict: Verdict | str,
    *,
    patient_id: str = "",
) -> tuple[str, str | None]:
    """校验模型标题，返回 ``(清理后标题, 失败原因)``。"""
    raw = str(value or "")
    text = raw.strip()
    if not text:
        return "", "missing"
    if "\n" in raw or "\r" in raw:
        return "", "multiline"
    if _CONTROL_PATTERN.search(raw):
        return "", "control_character"
    if _LIST_PATTERN.match(text):
        return "", "list_format"
    if not HEADLINE_MIN_LENGTH <= len(text) <= HEADLINE_MAX_LENGTH:
        return "", "length"
    if patient_id and patient_id in text:
        return "", "patient_identifier"
    if any(pattern.search(text) for pattern in _INTERNAL_PATTERNS):
        return "", "internal_term"

    if verdict == "INCONCLUSIVE" and not any(marker in text for marker in _REVIEW_MARKERS):
        return "", "verdict_conflict"
    if verdict == "CLEAN" and not any(marker in text for marker in _CLEAN_MARKERS):
        return "", "verdict_conflict"
    if verdict == "VIOLATION" and (
        any(marker in text for marker in _REVIEW_MARKERS + _CLEAN_MARKERS)
        or not any(marker in text for marker in _VIOLATION_MARKERS)
    ):
        return "", "verdict_conflict"
    return text, None


def _structured_subject(result: Any) -> str:
    names: list[str] = []
    for item in _get(result, "evidence", []) or []:
        if _get(item, "source", "") != "drug_audit_lookup":
            continue
        name = _clean_metadata(_get(item, "locator", ""))
        if name and name not in names:
            names.append(name)
    if not names:
        return "肿瘤医保限定用药"
    subject = "、".join(name[:12].rstrip(" ，,；;：:。") for name in names[:2])
    if len(names) > 2:
        subject += f"等{len(names)}项"
    return subject


def deterministic_headline(
    verdict: Verdict | str,
    rule_or_meta: Any = None,
    result: Any = None,
) -> str:
    """仅用规则元数据和结构化结果生成安全标题，不解析 reasoning。"""
    if _get(result, "gate_tag", "") == "技术故障隔离":
        return "未形成可复核异常证据，本规则不输出风险判定"

    if _get(result, "eligibility_evaluation") is not None:
        subject = _structured_subject(result)
        if verdict == "VIOLATION":
            return _fit_fallback(f"{subject}不符合医保限定支付条件，存在超范围支付风险")
        if verdict == "CLEAN":
            return _fit_fallback(f"{subject}医保限定支付条件核对未发现违规")
        return _fit_fallback(f"{subject}医保限定支付依据不足，待人工复核")

    if _get(result, "promise_trace") is not None and verdict == "CLEAN":
        subject = _rule_subject(rule_or_meta)
        return _fit_fallback(f"{subject}：退费后净数量未超过多次检查边界")

    subject = _rule_subject(rule_or_meta)
    if verdict == "VIOLATION":
        return _fit_fallback(f"{subject}核查：现有证据支持违规结论")
    if verdict == "CLEAN":
        return _fit_fallback(f"{subject}核查：现有结构化事实未支持违规结论")
    return _fit_fallback(f"{subject}核查：现有依据不足，待人工复核")


def _rule_subject(rule_or_meta: Any) -> str:
    for field in ("behavior_name", "violation_type", "question"):
        value = _clean_metadata(_get(rule_or_meta, field, ""))
        if value:
            return value[:24].rstrip(" ，,；;：:。")
    return "医保合规项目"


def _fit_fallback(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > HEADLINE_MAX_LENGTH:
        text = text[:HEADLINE_MAX_LENGTH].rstrip(" ，,；;：:。")
    if len(text) < HEADLINE_MIN_LENGTH:
        text = f"{text}，请结合现有资料核对"
    return text[:HEADLINE_MAX_LENGTH].rstrip(" ，,；;：:。")


def finalize_audit_headline(
    result: AuditResult,
    rule_or_meta: Any = None,
    *,
    patient_id: str = "",
) -> AuditResult:
    """在全部主裁决门控之后确定 headline；绝不改动主裁决字段。"""
    reason = "verdict_changed" if result.gate_tag else None
    accepted = ""
    if reason is None:
        accepted, reason = validate_headline(
            result.headline,
            result.verdict,
            patient_id=patient_id,
        )
    headline = accepted or deterministic_headline(result.verdict, rule_or_meta, result)
    _record_metric(fallback_reason=reason)
    logger.info(
        "headline_finalize outcome=%s reason=%s verdict=%s",
        "fallback" if reason else "accepted",
        reason or "none",
        result.verdict,
    )
    return result.model_copy(update={"headline": headline})
