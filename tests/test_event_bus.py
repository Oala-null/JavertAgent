# -*- coding: utf-8 -*-
"""tests for routes_sse.EventBus — pub/sub fan-out."""

from __future__ import annotations

import asyncio
import json

import pytest

from javert.web.api.routes_sse import EventBus, _eligibility_sse_fields


@pytest.mark.asyncio
async def test_subscribe_and_publish_single_subscriber():
    bus = EventBus()
    q = await bus.subscribe()
    await bus.publish("review_submitted", {"run_id": "aud_abc", "verdict": "V"})
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    assert msg["event"] == "review_submitted"
    data = json.loads(msg["data"])
    assert data == {"run_id": "aud_abc", "verdict": "V"}


@pytest.mark.asyncio
async def test_publish_fans_out_to_all_subscribers():
    bus = EventBus()
    q1 = await bus.subscribe()
    q2 = await bus.subscribe()
    q3 = await bus.subscribe()
    await bus.publish("new_audit_run", {"run_id": "x", "patient_id": "J66252"})

    for q in (q1, q2, q3):
        msg = await asyncio.wait_for(q.get(), timeout=1.0)
        assert msg["event"] == "new_audit_run"


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = await bus.subscribe()
    await bus.unsubscribe(q)
    await bus.publish("evt", {"x": 1})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.2)


@pytest.mark.asyncio
async def test_subscriber_count_tracks_subs():
    bus = EventBus()
    assert bus.subscriber_count == 0
    q1 = await bus.subscribe()
    q2 = await bus.subscribe()
    assert bus.subscriber_count == 2
    await bus.unsubscribe(q1)
    assert bus.subscriber_count == 1
    await bus.unsubscribe(q2)
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_publish_handles_datetime_payload():
    from datetime import datetime, timezone
    bus = EventBus()
    q = await bus.subscribe()
    ts = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    await bus.publish("evt", {"ts": ts})
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    data = json.loads(msg["data"])
    # ISO-8601 string
    assert "2026-05-20" in data["ts"]


@pytest.mark.asyncio
async def test_slow_subscriber_dropped_on_queue_full():
    bus = EventBus()
    q = await bus.subscribe()
    # 200 是 EventBus._subs queue maxsize
    for i in range(200):
        await bus.publish("evt", {"i": i})
    # 第 201 条触发 QueueFull → 该订阅被踢出
    await bus.publish("evt", {"i": 200})
    assert bus.subscriber_count == 0


def test_eligibility_sse_summary_is_additive_and_old_rows_remain_nullable():
    old = _eligibility_sse_fields(None)
    assert old["release_id"] is None
    assert old["policy_scope"] is None
    assert old["temporal_warning"] is None
    assert old["scope_evaluations"] is None

    summary = _eligibility_sse_fields(
        {
            "audit_disposition": "NO_VIOLATION_FOUND",
            "eligibility_status": "SATISFIED",
            "release_id": "release-synthetic",
            "rule_revision_id": "revision-synthetic",
            "policy_scope": "INSURANCE_PAYMENT",
            "rule_effective_from": "2026-01-01",
            "rule_effective_to": "2027-12-31",
            "evaluated_service_date": "2025-12-31",
            "effective_date_enforced": False,
            "temporal_applicability": "BEFORE_EFFECTIVE_WINDOW",
            "temporal_warning": "核查当期指南/医保限定是否适用",
            "scope_evaluations": [
                {
                    "drug_concept_id": "drug-synthetic",
                    "policy_scope": "INSURANCE_PAYMENT",
                    "legacy_verdict": "CLEAN",
                },
                {
                    "drug_concept_id": "drug-synthetic",
                    "policy_scope": "GUIDELINE_INDICATION",
                    "legacy_verdict": "INCONCLUSIVE",
                },
            ],
            "future_consumer_field": "ignored-at-summary",
        }
    )
    assert summary["audit_disposition"] == "NO_VIOLATION_FOUND"
    assert summary["release_id"] == "release-synthetic"
    assert summary["policy_scope"] == "INSURANCE_PAYMENT"
    assert summary["temporal_applicability"] == "BEFORE_EFFECTIVE_WINDOW"
    assert summary["effective_date_enforced"] is False
    assert [item["policy_scope"] for item in summary["scope_evaluations"]] == [
        "INSURANCE_PAYMENT",
        "GUIDELINE_INDICATION",
    ]
    assert "future_consumer_field" not in summary
