# -*- coding: utf-8 -*-
"""规则路由 — GET /api/rules, GET /api/rules/{rule_id}."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from javert.audit.rule_loader import load_all, load_rule
from javert.config import get_config
from javert.store.audit_store import SqliteStore

from .schemas import AuditRunSummary, RuleDetail, RuleHistory, RuleSummary

logger = logging.getLogger("javert.web.routes_rules")

router = APIRouter(prefix="/api/rules", tags=["rules"])


def _coerce_history(d: dict | None) -> RuleHistory | None:
    if d is None:
        return None
    return RuleHistory(
        V=int(d.get("V", 0)),
        C=int(d.get("C", 0)),
        I=int(d.get("I", 0)),
        total=int(d.get("total", 0)),
        mean_confidence=float(d.get("mean_confidence", 0.0)),
        mean_duration_ms=float(d.get("mean_duration_ms", 0.0)),
    )


@router.get("", response_model=list[RuleSummary])
def list_rules() -> list[RuleSummary]:
    """所有规则列表 (附本地 SQLite 历史 verdict 分布)."""
    cfg = get_config()
    rules = load_all(cfg.rules_path)

    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        history_map = store.summary_by_rule()
    finally:
        store.close()

    out: list[RuleSummary] = []
    for rid in sorted(rules.keys()):
        r = rules[rid]
        out.append(
            RuleSummary(
                rule_id=r.rule_id,
                domain=r.domain,
                violation_type=r.violation_type,
                question=r.question,
                status=r.status,
                has_prompt_addon=bool(r.prompt_addon.strip()),
                trigger_keywords_count=len(r.trigger_keywords),
                suggested_tools=list(r.suggested_tools),
                history=_coerce_history(history_map.get(rid)),
            )
        )
    return out


@router.get("/{rule_id}", response_model=RuleDetail)
def get_rule(rule_id: str) -> RuleDetail:
    """规则详情 + 最近 20 条 audit."""
    cfg = get_config()
    yaml_path = cfg.rules_path / f"{rule_id}.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"rule {rule_id} not found")
    rule = load_rule(yaml_path)

    store = SqliteStore(cfg.audit_db_path)
    try:
        store.init_schema()
        history = store.summary_by_rule(rule_id=rule_id).get(rule_id)
        runs = store.find_by_rule(rule_id)[:20]
    finally:
        store.close()

    return RuleDetail(
        rule_id=rule.rule_id,
        domain=rule.domain,
        violation_type=rule.violation_type,
        question=rule.question,
        example=rule.example,
        status=rule.status,
        prompt_addon=rule.prompt_addon,
        trigger_keywords=list(rule.trigger_keywords),
        suggested_tools=list(rule.suggested_tools),
        expected_signal=rule.expected_signal,
        notes=rule.notes,
        history=_coerce_history(history),
        recent_runs=[
            AuditRunSummary(
                run_id=r.run_id,
                rule_id=r.rule_id,
                patient_id=r.patient_id,
                verdict=r.verdict,
                confidence=r.confidence,
                duration_ms=r.duration_ms,
                model=r.model,
                started_at=r.started_at,
                audit_disposition=(
                    r.eligibility_evaluation.audit_disposition.value
                    if r.eligibility_evaluation is not None
                    else None
                ),
                eligibility_status=(
                    r.eligibility_evaluation.eligibility_status.value
                    if r.eligibility_evaluation is not None
                    else None
                ),
            )
            for r in runs
        ],
    )


@router.get("/{rule_id}/yaml")
def get_rule_yaml(rule_id: str) -> dict:
    """返回原始 yaml 文件文本 (含注释/字段顺序)."""
    cfg = get_config()
    yaml_path = cfg.rules_path / f"{rule_id}.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail=f"rule {rule_id} not found")
    return {
        "rule_id": rule_id,
        "yaml_text": yaml_path.read_text(encoding="utf-8"),
        "path": str(yaml_path),
    }
