# -*- coding: utf-8 -*-
"""完全离线、可重复的 Promise 案例执行器。"""

from __future__ import annotations

import json
import socket
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import patch

from javert.data.fee_netting import NetItem, fee_group_key

from .loader import PromiseRepository
from .models import PromiseCase, PromiseMatch, PromiseTrace
from .registry import evaluate_definition, evaluate_terminal_promises


def _offline_denied(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("PROMISE_OFFLINE_DEPENDENCY_DENIED")


@contextmanager
def offline_guard() -> Iterator[None]:
    """禁止最常见网络入口；harness 本身不加载 LLM/Hub/SQL 组件。"""

    with ExitStack() as stack:
        stack.enter_context(patch.object(socket, "create_connection", _offline_denied))
        stack.enter_context(patch.object(socket.socket, "connect", _offline_denied))
        yield


def _net_items(case: PromiseCase) -> dict[str, NetItem]:
    raw_items = case.facts.get("fee_items", [])
    if not isinstance(raw_items, list):
        return {}
    result: dict[str, NetItem] = {}
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        item = NetItem(
            name=str(raw.get("name") or ""),
            code=str(raw.get("code") or ""),
            net_qty=float(raw.get("net_qty") or 0),
            distinct_billing_dates=int(raw.get("distinct_billing_dates") or 0),
            has_refund=bool(raw.get("has_refund")),
            refund_count=int(raw.get("refund_count") or 0),
            quantity_parseable=bool(raw.get("quantity_parseable", True)),
        )
        result[fee_group_key(item.code, item.name) or f"item-{index}"] = item
    return result


def _normalized(match: PromiseMatch | None) -> dict[str, Any]:
    if match is None:
        return {"outcome": "NOT_APPLICABLE"}
    return {
        "outcome": "MATCH",
        "verdict": match.guarantee,
        "finality": match.trace.finality,
        "reason_code": match.trace.reason_code,
        "facts": match.trace.facts,
    }


def _expected(case: PromiseCase) -> dict[str, Any]:
    value: dict[str, Any] = {"outcome": case.expected.outcome}
    if case.expected.outcome == "MATCH":
        value.update(
            verdict=case.expected.verdict,
            finality=case.expected.finality,
            reason_code=case.expected.reason_code,
            facts=case.expected.facts,
        )
    return value


def _duration_bucket(seconds: float) -> str:
    if seconds < 0.01:
        return "lt_10ms"
    if seconds < 0.1:
        return "10_99ms"
    return "gte_100ms"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    promise_id: str
    status: str
    reason_code: str
    duration_bucket: str
    historical: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "promise_id": self.promise_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "duration_bucket": self.duration_bucket,
            "historical": self.historical,
        }


def run_case(repository: PromiseRepository, case: PromiseCase) -> CaseResult:
    started = time.monotonic()
    definition = repository.definition(case.promise_id, case.promise_version)
    items = _net_items(case)
    with offline_guard():
        first = _normalized(evaluate_definition(definition, case.rule_id, items))
        second = _normalized(evaluate_definition(definition, case.rule_id, items))
        terminal = evaluate_terminal_promises(
            repository.active_definitions, case.rule_id, items
        )
    if first != second:
        status = "failed"
        reason_code = "NON_DETERMINISTIC_OUTPUT"
    elif terminal.conflict_ids:
        status = "failed"
        reason_code = "PROMISE_CONFLICT"
    elif first != _expected(case):
        status = "failed"
        reason_code = "UNEXPECTED_MATCH"
    else:
        status = "passed"
        reason_code = str(first.get("reason_code") or "NOT_APPLICABLE")
    historical = bool(
        case.source_case_id
        and any(
            drift.case_id == case.source_case_id and drift.status == "promoted"
            for drift in repository.drift_cases
        )
    ) or case.historical_boundary
    return CaseResult(
        case_id=case.case_id,
        promise_id=case.promise_id,
        status=status,
        reason_code=reason_code,
        duration_bucket=_duration_bucket(time.monotonic() - started),
        historical=historical,
    )


def run_harness(repository: PromiseRepository) -> dict[str, Any]:
    results = [run_case(repository, case) for case in repository.cases]
    results.extend(_run_public_fixtures())
    failures = sum(item.status == "failed" for item in results)
    return {
        "status": "ok" if failures == 0 else "failed",
        "counts": {
            "cases": len(results),
            "passed": len(results) - failures,
            "failed": failures,
            "historical": sum(item.historical for item in results),
        },
        "results": [item.as_dict() for item in results],
    }


def _fixture_result(case_id: str, reason_code: str, ok: bool) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        promise_id="PR-PUBLIC",
        status="passed" if ok else "failed",
        reason_code=reason_code if ok else reason_code.replace("_OK", "_FAILED"),
        duration_bucket="lt_10ms",
        historical=False,
    )


def _run_public_fixtures() -> list[CaseResult]:
    """把 presenter、真实费用命中约束和公开类别合组纳入同一离线门禁。"""

    import pandas as pd

    from javert.web.api.routes_workbench import _group_runs_by_violation_type
    from javert.web.hit_resolver import resolve_hits_from_json
    from javert.web.public_presenter import present_public_explanation

    trace = PromiseTrace(
        promise_id="PR-D001",
        version=1,
        kind="refund-net-single-clean",
        finality="LOCKED",
        reason_code="REFUND_NET_SINGLE_CLEAN",
        facts={"net_qty": 1, "refund_count": 1},
    )
    run = SimpleNamespace(verdict="CLEAN", promise_trace=trace)
    meta = {"behavior_name": "过度检查", "question": "核对是否存在多次抗体筛查"}
    presenter_a = present_public_explanation(run, meta, [])
    presenter_b = present_public_explanation(run, meta, [])
    presenter_ok = presenter_a == presenter_b and presenter_a["charge_facts"] == [
        {
            "fact": "目标收费项目退费后净数量",
            "value": 1,
            "unit": "次",
            "refund_count": 1,
        }
    ]

    evidence = json.dumps(
        [{"source": "search_fees", "locator": "费用明细检索", "text": "未找到收费行"}],
        ensure_ascii=False,
    )
    hits_a = resolve_hits_from_json(evidence, "[]", None, pd.DataFrame(), {})
    hits_b = resolve_hits_from_json(evidence, "[]", None, pd.DataFrame(), {})
    hits_ok = hits_a == hits_b == []

    runs = [
        SimpleNamespace(rule_id="R-A", verdict="VIOLATION"),
        SimpleNamespace(rule_id="R-B", verdict="INCONCLUSIVE"),
    ]
    category_meta = {
        "R-A": {"behavior_code": "T380206", "behavior_name": "提供不必要的医药服务"},
        "R-B": {"behavior_code": "T380206", "behavior_name": "提供不必要的医药服务"},
    }
    groups_a = _group_runs_by_violation_type(list(runs), category_meta)
    groups_b = _group_runs_by_violation_type(list(runs), category_meta)
    category_ok = (
        len(groups_a) == len(groups_b) == 1
        and groups_a[0]["behavior_code"] == "T380206"
        and [item.rule_id for item in groups_a[0]["runs"]] == ["R-A", "R-B"]
    )
    return [
        _fixture_result("CASE-PUBLIC-PRESENTER", "PUBLIC_PRESENTER_OK", presenter_ok),
        _fixture_result("CASE-PUBLIC-HIT", "PUBLIC_HIT_OK", hits_ok),
        _fixture_result("CASE-PUBLIC-CATEGORY", "PUBLIC_CATEGORY_OK", category_ok),
    ]


def harness_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
