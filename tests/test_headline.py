# -*- coding: utf-8 -*-
"""公开 headline 的 golden 门控、确定性路径和无内容可观测性。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from javert.audit.headline import (
    HEADLINE_MAX_LENGTH,
    HEADLINE_MIN_LENGTH,
    deterministic_headline,
    finalize_audit_headline,
    headline_metrics_snapshot,
    reset_headline_metrics,
    validate_headline,
)
from javert.audit.result import AuditResult, Evidence


def _result(**updates) -> AuditResult:
    values = {
        "run_id": "aud_HEADLINE0001",
        "rule_id": "R191",
        "patient_id": "PATIENT-DEID-001",
        "verdict": "VIOLATION",
        "confidence": 0.91,
        "headline": "脑功能成像项目存在重复收费，现有证据支持违规结论",
        "reasoning": "完整推理保持不变",
        "evidence": [Evidence(source="fee", locator="脑功能成像", text="语义化费用事实")],
        "started_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
    }
    values.update(updates)
    return AuditResult(**values)


def test_deidentified_golden_headlines_are_100_percent_gated_as_expected():
    cases = json.loads(
        (Path(__file__).parent / "fixtures" / "headline_golden.json").read_text(
            encoding="utf-8"
        )
    )
    assert 20 <= len(cases) <= 30
    for case in cases:
        accepted, reason = validate_headline(
            case["headline"],
            case["verdict"],
            patient_id="PATIENT-DEID-001",
        )
        assert reason == case["reason"], case["id"]
        assert bool(accepted) == (case["reason"] is None), case["id"]


def test_valid_model_headline_is_kept_without_touching_main_verdict_fields():
    original = _result()
    final = finalize_audit_headline(
        original,
        {"violation_type": "重复收费"},
        patient_id=original.patient_id,
    )
    assert final.headline == original.headline
    assert final.verdict == original.verdict
    assert final.confidence == original.confidence
    assert final.reasoning == original.reasoning
    assert final.evidence == original.evidence


def test_verdict_change_forces_inconclusive_fallback_without_reusing_violation_title():
    original = _result(
        verdict="INCONCLUSIVE",
        gate_tag="低置信降级",
        headline="脑功能成像项目存在重复收费，现有证据支持违规结论",
    )
    final = finalize_audit_headline(original, {"violation_type": "重复收费"})
    assert "待人工复核" in final.headline
    assert "支持违规结论" not in final.headline
    assert final.verdict == "INCONCLUSIVE"
    assert final.reasoning == original.reasoning


def test_deterministic_paths_do_not_parse_reasoning():
    meta = {"violation_type": "重复收费"}
    clean = _result(
        verdict="CLEAN",
        headline="",
        precheck_tag="无A项",
        reasoning="这里故意写入虚构药品名，标题不得读取本段散文。",
    )
    final = finalize_audit_headline(clean, meta)
    assert "虚构药品名" not in final.headline
    assert "未支持违规" in final.headline

    technical = clean.model_copy(update={"gate_tag": "技术故障隔离"})
    assert finalize_audit_headline(technical, meta).headline == (
        "未形成可复核异常证据，本规则不输出风险判定"
    )


def test_structured_oncology_headline_uses_at_most_two_names_and_aggregate_count():
    result = {
        "eligibility_evaluation": {"audit_disposition": "REVIEW_REQUIRED"},
        "evidence": [
            {"source": "drug_audit_lookup", "locator": name}
            for name in ("维立西呱", "奥希替尼", "帕博利珠单抗")
        ],
    }
    headline = deterministic_headline("INCONCLUSIVE", {}, result)
    assert "维立西呱、奥希替尼等3项" in headline
    assert "帕博利珠单抗" not in headline
    assert "待人工复核" in headline
    assert HEADLINE_MIN_LENGTH <= len(headline) <= HEADLINE_MAX_LENGTH

    long_result = {
        "eligibility_evaluation": {"audit_disposition": "REVIEW_REQUIRED"},
        "evidence": [
            {"source": "drug_audit_lookup", "locator": "注射用超长语义化抗肿瘤药品名称甲"},
            {"source": "drug_audit_lookup", "locator": "注射用超长语义化抗肿瘤药品名称乙"},
            {"source": "drug_audit_lookup", "locator": "注射用超长语义化抗肿瘤药品名称丙"},
        ],
    }
    long_headline = deterministic_headline("INCONCLUSIVE", {}, long_result)
    assert len(long_headline) <= HEADLINE_MAX_LENGTH
    assert long_headline.endswith("待人工复核")


def test_promise_path_uses_structured_trace_without_reasoning():
    headline = deterministic_headline(
        "CLEAN",
        {"violation_type": "抗体筛查多次检查"},
        {
            "promise_trace": {"facts": {"net_qty": 1, "refund_count": 1}},
            "reasoning": "散文中的虚构项目不得进入标题",
        },
    )
    assert headline == "抗体筛查多次检查：退费后净数量未超过多次检查边界"
    assert "虚构项目" not in headline


def test_headline_metrics_and_logs_never_contain_business_text_or_identifiers(caplog):
    reset_headline_metrics()
    raw = "PATIENT-DEID-001 脑功能成像存在重复收费违规风险"
    with caplog.at_level(logging.INFO, logger="javert.audit.headline"):
        final = finalize_audit_headline(
            _result(headline=raw),
            {"violation_type": "重复收费"},
            patient_id="PATIENT-DEID-001",
        )
    metrics = headline_metrics_snapshot()
    assert metrics == {
        "generated_total": 1,
        "fallback_total": 1,
        "fallback_reason.patient_identifier": 1,
    }
    assert final.headline != raw
    assert raw not in caplog.text
    assert "PATIENT-DEID-001" not in caplog.text
    assert "aud_HEADLINE0001" not in caplog.text
    assert "完整推理保持不变" not in caplog.text
