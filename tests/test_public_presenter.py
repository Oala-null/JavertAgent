# -*- coding: utf-8 -*-
"""医生公开解释的字段来源与内部术语清洗。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from javert.audit.result import AuditResult
from javert.promises.models import PromiseTrace
from javert.web.api.routes_audit import _result_payload
from javert.web.hit_resolver import Anchor, HitItem
from javert.web.public_presenter import (
    present_public_explanation,
    public_promise_summary,
    sanitize_public_text,
)


def _locked_result() -> AuditResult:
    return AuditResult(
        run_id="aud_publicsafe01",
        rule_id="R151",
        patient_id="CASE-PUBLIC-PRESENTER",
        verdict="CLEAN",
        confidence=1.0,
        reasoning="根据规则 R151，search_fees 后由 gate 返回 CLEAN。",
        evidence=[],
        tool_calls=[],
        duration_ms=1,
        model="offline",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        promise_trace=PromiseTrace(
            promise_id="PR-D001",
            version=1,
            kind="refund-net-single-clean",
            finality="LOCKED",
            reason_code="REFUND_NET_SINGLE_CLEAN",
            facts={"net_qty": 1, "refund_count": 1},
        ),
    )


def test_locked_clean_explanation_only_projects_proven_facts():
    result = _locked_result()
    public = present_public_explanation(
        result,
        {"behavior_name": "过度检查", "question": "核对抗体筛查次数"},
        [],
    )
    assert public["conclusion"]["summary"] == "退费抵消后目标项目净数量为 1 次，未超过多次检查边界。"
    assert public["charge_facts"] == [{
        "fact": "目标收费项目退费后净数量",
        "value": 1,
        "unit": "次",
        "refund_count": 1,
    }]
    assert public["clinical_evidence"] == []
    assert public["review_needs"] == []


def test_presenter_uses_actual_charge_hit_and_does_not_guess_from_reasoning():
    result = _locked_result().model_copy(update={"promise_trace": None, "verdict": "VIOLATION"})
    actual = HitItem(
        source="fee",
        name="语义化收费项目",
        matched_fee_name="语义化收费项目（规格）",
        code_nat="SYNTHETIC-CODE",
        anchor=Anchor(tab="fees", query="语义化收费项目"),
    )
    public = present_public_explanation(result, {}, [actual])
    assert public["charge_facts"] == [{
        "name": "语义化收费项目",
        "matched_charge_name": "语义化收费项目（规格）",
        "code": "SYNTHETIC-CODE",
    }]
    assert "search_fees" not in json.dumps(public, ensure_ascii=False)
    assert "R151" not in json.dumps(public, ensure_ascii=False)


def test_presenter_keeps_humanized_narrative_without_guessing_structured_facts():
    result = _locked_result().model_copy(update={
        "promise_trace": None,
        "verdict": "INCONCLUSIVE",
        "reasoning": (
            "根据规则 R040，search_fees 发现合成收费项目；诊断只支持合成疾病甲，"
            "关键手术记录缺失（ETL_GAP），无法核实实际术式，故判 INCONCLUSIVE。"
        ),
    })

    public = present_public_explanation(result, {}, [])

    assert "合成收费项目" in public["narrative"]
    assert "诊断只支持合成疾病甲" in public["narrative"]
    assert "关键手术记录缺失" in public["narrative"]
    assert "无法核实实际术式" in public["narrative"]
    for forbidden in ("R040", "search_fees", "ETL_GAP", "INCONCLUSIVE"):
        assert forbidden not in public["narrative"]
    assert public["charge_facts"] == []
    assert public["basis"] == []
    assert public["clinical_evidence"] == []


def test_presenter_does_not_publish_charge_assertion_without_charge_anchor():
    result = _locked_result().model_copy(update={
        "promise_trace": None,
        "verdict": "INCONCLUSIVE",
    })

    public = present_public_explanation(
        result,
        {
            "behavior_name": "提供不必要的医药服务",
            "question": "未开展相关诊疗项目，但收取对应诊疗费用。",
        },
        [],
    )

    assert public["audit_items"] == ["提供不必要的医药服务"]
    assert "收取对应诊疗费用" not in public["audit_items"][0]
    assert any("未形成可公开的收费项目锚点" in item for item in public["review_needs"])


def test_old_row_headline_fallback_uses_rule_metadata_not_reasoning():
    result = _locked_result().model_copy(update={
        "promise_trace": None,
        "verdict": "INCONCLUSIVE",
        "headline": "",
        "reasoning": "散文中出现虚构药品名和虚构收费事实，不得反解析进标题。",
    })
    public = present_public_explanation(
        result,
        {"violation_type": "重复收费", "behavior_name": "重复收费"},
        [],
    )
    assert public["headline"] == "重复收费核查：现有依据不足，待人工复核"
    assert "虚构药品名" not in public["headline"]


def test_public_sanitizer_removes_internal_ids_tools_verdicts_and_reason_codes():
    text = sanitize_public_text(
        "根据规则 RD04 和 CD01，search_fees 与 gate 得出 VIOLATION，原因 PROMISE_CONFLICT。"
    )
    for forbidden in ("RD04", "CD01", "search_fees", "gate", "VIOLATION", "PROMISE_CONFLICT"):
        assert forbidden not in text


def test_sse_result_payload_adds_public_fields_without_removing_legacy_fields():
    payload = _result_payload(_locked_result())
    for legacy in (
        "run_id", "rule_id", "patient_id", "verdict", "confidence", "reasoning",
        "evidence", "tool_calls", "duration_ms", "model", "started_at",
    ):
        assert legacy in payload
    assert payload["public_explanation"]["charge_facts"][0]["value"] == 1
    assert payload["public_explanation"]["narrative"]
    assert payload["promise"] == {"locked": True, "historical_conflict": False}
    assert public_promise_summary(None) is None
