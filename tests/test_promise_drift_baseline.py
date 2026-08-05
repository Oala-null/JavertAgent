# -*- coding: utf-8 -*-
"""add-evolving-promise-harness: 四个已确认漂移的去标识回归基线。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from javert.audit.rule_loader import load_rule
from javert.audit.runner import Runner
from javert.config import get_config
from javert.store.models import RunWithReviews, User
from javert.web.api.routes_workbench import _group_runs_by_violation_type
from javert.web.hit_resolver import resolve_hits_from_json
from javert.web.templating import render


class _NoLlmProvider:
    model_name = "must-not-run"

    def chat_with_retry(self, _messages, **_kwargs):
        raise AssertionError("退费净数量 Promise 命中时不得调用 LLM")


class _Executor:
    def reset_cache(self):
        return None

    def set_patient_context(self, _patient_id):
        return None

    def clear_patient_context(self):
        return None

    def get_tools_prompt(self):
        return ""


class _FeeLoader:
    def get_fees(self, _patient_id):
        return pd.DataFrame(
            {
                "medins_list_name": ["乙肝表面抗原测定", "乙肝表面抗原测定"],
                "med_list_codg": ["SYNTH-AB", "SYNTH-AB"],
                "cnt": [2, -1],
                "fee_ocur_time": ["2026-01-02", "2026-01-03"],
            }
        )


def test_refund_net_single_is_locked_clean_before_llm():
    """修复前：正收费日期数为 2，即使 +2/-1 净量为 1 仍会进入 LLM/疑似。"""
    cfg = get_config()
    rule = load_rule(cfg.rules_path / "R151.yaml")
    result = Runner(
        executor=_Executor(),
        provider=_NoLlmProvider(),
        config=cfg,
        loader=_FeeLoader(),
    ).audit(rule, "CASE-REFUND-NET-SINGLE")

    assert result.verdict == "CLEAN"
    assert result.tool_calls == []
    assert result.promise_trace is not None
    assert result.promise_trace.finality == "LOCKED"


def test_abstract_fee_locator_without_charge_rows_is_not_a_public_hit():
    """修复前：无费用行时仍把“定位/检索词”投影成费用命中。"""
    hits = resolve_hits_from_json(
        json.dumps(
            [
                {
                    "source": "search_fees",
                    "locator": "费用明细检索",
                    "text": "定位：未找到乙肝表面抗原测定收费行",
                }
            ],
            ensure_ascii=False,
        ),
        json.dumps(
            [
                {
                    "tool_name": "search_fees",
                    "arguments": {"keyword": "乙肝表面抗原测定"},
                }
            ],
            ensure_ascii=False,
        ),
        None,
        pd.DataFrame(),
        {},
    )

    assert [hit for hit in hits if hit.source in {"fee", "drug"}] == []


def _run(rule_id: str) -> RunWithReviews:
    return RunWithReviews(
        run_id=f"aud_{rule_id}",
        rule_id=rule_id,
        patient_id="CASE-PUBLIC-CATEGORY",
        verdict="VIOLATION",
        confidence=0.9,
        reasoning="去标识测试",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_workbench_groups_by_public_behavior_pair_not_internal_type():
    """修复前：同一 H/I pair 因内部 violation_type 不同被拆成两个组。"""
    runs = [_run("R-A"), _run("R-B")]
    meta = {
        "R-A": {
            "violation_type": "虚构医药服务项目",
            "behavior_code": "T380206",
            "behavior_name": "提供不必要的医药服务",
        },
        "R-B": {
            "violation_type": "虚构医药服务",
            "behavior_code": "T380206",
            "behavior_name": "提供不必要的医药服务",
        },
    }

    groups = _group_runs_by_violation_type(runs, meta)

    assert len(groups) == 1
    assert groups[0]["behavior_code"] == "T380206"
    assert [run.rule_id for run in groups[0]["runs"]] == ["R-A", "R-B"]


def test_workbench_default_card_hides_internal_rule_id_and_raw_evidence():
    """修复前：医生默认卡片 hover 暴露内部 rule ID，并直接渲染 evidence JSON。"""
    run = RunWithReviews(
        run_id="aud_publicsafe01",
        rule_id="R191",
        patient_id="CASE-PUBLIC-OUTPUT",
        verdict="INCONCLUSIVE",
        confidence=0.5,
        reasoning="根据规则 R191，search_fees 未找到，gate 转人工复核。",
        evidence_json='[{"source":"search_fees","locator":"定位"}]',
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    user = User(
        id=1,
        username="reviewer",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    public_explanation = {
        "conclusion": {"label": "待人工复核", "summary": "现有资料不足。"},
        "audit_items": [],
        "charge_facts": [],
        "basis": [],
        "clinical_evidence": [],
        "review_needs": ["请人工核对相关资料。"],
    }

    html = render(
        "patient_detail.html",
        title="测试",
        current_user=user,
        patients=[],
        active_patient=run.patient_id,
        filter="v_and_i",
        filter_label="违规 + 不明",
        runs=[run],
        rule_meta={
            "R191": {
                "violation_type": "重复收费",
                "behavior_name": "重复收费",
                "behavior_code": "T380301",
                "question": "去标识核查问题",
                "subtitle": "内部副标题",
                "drug_rule_type": None,
            }
        },
        hits_by_run={run.run_id: []},
        public_explanations={run.run_id: public_explanation},
    )

    assert 'title="R191' not in html
    assert "search_fees" not in html
    assert "规则 R191" not in html
    assert run.evidence_json not in html
    assert "现有资料不足" in html
