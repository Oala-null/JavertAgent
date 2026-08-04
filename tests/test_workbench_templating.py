# -*- coding: utf-8 -*-
"""tests for web/templating.py: Jinja2 env + 自定义 filters + 工作台模板渲染."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from javert.store.models import (
    DashboardStats,
    PatientSidebarItem,
    ReviewerLeaderRow,
    ReviewRecord,
    RuleAgreementRow,
    RunWithReviews,
    SinceLastLoginStats,
    User,
)
from javert.web.templating import get_env, render


@pytest.fixture
def alice():
    return User(
        id=1, username="alice", display_name="Dr. Alice",
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        last_login=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )


def test_filters_registered():
    env = get_env()
    for k in ("humanize_delta", "humanize_since", "verdict_color",
              "verdict_label", "quality_flag_zh", "format_dt"):
        assert k in env.filters


def test_verdict_color_filter():
    env = get_env()
    assert env.filters["verdict_color"]("VIOLATION") == "v"
    assert env.filters["verdict_color"]("INCONCLUSIVE") == "i"
    assert env.filters["verdict_color"]("CLEAN") == "c"
    assert env.filters["verdict_color"]("V") == "v"
    assert env.filters["verdict_color"]("X") == "unknown"
    assert env.filters["verdict_color"](None) == "unknown"


def test_verdict_label_filter():
    env = get_env()
    assert env.filters["verdict_label"]("V") == "认同"
    assert env.filters["verdict_label"]("I") == "改判不明"
    assert env.filters["verdict_label"]("C") == "驳回"
    assert env.filters["verdict_label"]("VIOLATION") == "违规"


def test_oncology_draft_quality_flags_are_human_readable():
    quality_flag_zh = get_env().filters["quality_flag_zh"]
    assert "草稿规则预览" in quality_flag_zh("DRAFT_RULE_PREVIEW_ONLY")
    assert "必须人工复核" in quality_flag_zh("AUTHORING_REVIEW_REQUIRED")
    assert "尚无生效且已审核" in quality_flag_zh(
        "NO_APPROVED_ELIGIBILITY_RULE"
    )


def test_login_renders(alice):
    out = render("login.html", next="/workbench", error=None,
                 title="登录", current_user=None)
    assert "登录" in out
    assert "/workbench" in out
    assert 'name="next"' in out


def test_login_error_shown():
    out = render("login.html", next="/workbench", error="用户名或密码错误",
                 title="登录", current_user=None)
    assert "用户名或密码错误" in out
    assert "banner-error" in out


def test_register_renders():
    out = render("register.html", error=None, title="注册", current_user=None)
    assert "创建账号" in out
    assert 'name="username"' in out


def test_workbench_first_login_banner(alice):
    stats = SinceLastLoginStats(new_patients=50, new_violations=275, new_inconclusive=120)
    out = render(
        "workbench.html",
        title="工作台",
        current_user=alice,
        patients=[],
        active_patient=None,
        filter="v_and_i",
        filter_label="违规 + 不明",
        runs=[],
        show_banner=True,
        banner_stats=stats,
        banner_first_login=True,
        prev_last_login=None,
    )
    assert "首次登录" in out
    assert "50" in out  # new_patients
    assert "275" in out  # new_violations


def test_workbench_returning_user_banner(alice):
    stats = SinceLastLoginStats(new_patients=3, new_violations=15, new_inconclusive=7)
    out = render(
        "workbench.html",
        title="工作台",
        current_user=alice,
        patients=[],
        active_patient=None,
        filter="v_and_i",
        filter_label="违规 + 不明",
        runs=[],
        show_banner=True,
        banner_stats=stats,
        banner_first_login=False,
        prev_last_login=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )
    assert "欢迎回来" in out
    assert "alice" in out or "Dr. Alice" in out
    assert "+3" in out
    assert "+15" in out


def test_workbench_dismissed_banner(alice):
    out = render(
        "workbench.html",
        title="工作台",
        current_user=alice,
        patients=[],
        active_patient=None,
        filter="v_and_i",
        filter_label="违规 + 不明",
        runs=[],
        show_banner=False,
        banner_stats=SinceLastLoginStats(),
        banner_first_login=False,
        prev_last_login=None,
    )
    assert "welcome-banner" not in out


def test_workbench_sidebar_progress(alice):
    patients = [
        PatientSidebarItem(
            patient_id="J66252", v_count=18, i_count=0, c_count=80,
            reviewed_count=5, relevant_count=18,
        ),
        PatientSidebarItem(
            patient_id="J18906", v_count=7, i_count=2, c_count=20,
            reviewed_count=9, relevant_count=9, fully_reviewed=True,
        ),
    ]
    out = render(
        "workbench.html",
        title="工作台",
        current_user=alice,
        patients=patients,
        active_patient=None,
        filter="v_and_i",
        filter_label="违规 + 不明",
        runs=[],
        show_banner=False,
        banner_stats=SinceLastLoginStats(),
        banner_first_login=False,
        prev_last_login=None,
    )
    assert "J66252" in out
    assert "18V" in out
    assert "已审 5/18" in out
    assert "J18906" in out
    assert "fully-reviewed" in out  # CSS class on the ✓ patient
    assert "已审 9/9" in out


def test_workbench_sidebar_facets_and_summary(alice):
    """G4: 富卡片摘要 (主诊/¥金额/更新时间) + facet 栏 + tag chip + data-* 属性."""
    patients = [
        PatientSidebarItem(
            patient_id="J66252", v_count=3, i_count=1, c_count=10,
            reviewed_count=0, relevant_count=4,
            fees_sum=26871.39, primary_dx="甲状腺恶性肿瘤 (C73.x00)",
            updated_at=datetime(2026, 5, 22, 3, 14, tzinfo=timezone.utc),
        ),
        PatientSidebarItem(
            patient_id="211486870", v_count=5, i_count=3, c_count=32,
            reviewed_count=2, relevant_count=8, batch_tag="szx",
            fees_sum=120000.0, primary_dx="肺恶性肿瘤",
            updated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        ),
    ]
    out = render(
        "workbench.html", title="工作台", current_user=alice,
        patients=patients, active_patient=None, filter="v_and_i",
        filter_label="违规 + 不明", runs=[], show_banner=False,
        banner_stats=SinceLastLoginStats(), banner_first_login=False,
        prev_last_login=None,
    )
    # facet 栏
    assert 'id="facet-bar"' in out
    assert 'id="facet-dx"' in out
    assert 'id="facet-fee"' in out
    assert 'id="facet-time"' in out
    # tag chip (从 batch_tag szx 派生)
    assert 'class="facet-chip" data-tag="szx"' in out
    # 富卡片摘要 + data-* facet 驱动属性
    assert 'data-fees="26871"' in out
    assert 'data-dx="甲状腺恶性肿瘤 (C73.x00)"' in out
    assert 'data-tag="szx"' in out
    assert 'data-updated="2026-05-22T03:14:00+00:00"' in out
    assert "¥26,871" in out
    assert "甲状腺恶性肿瘤" in out


def test_patient_detail_v_card_with_my_review(alice):
    my = ReviewRecord(
        id=1, run_id="aud_abcdefghijkl", user_id=alice.id,
        review_verdict="V", comment="符合规则",
        created_at=datetime.now(timezone.utc), is_latest=True,
        reviewer_username="alice", reviewer_display_name="Dr. Alice",
    )
    other = ReviewRecord(
        id=2, run_id="aud_abcdefghijkl", user_id=99,
        review_verdict="C", comment="不算违规",
        created_at=datetime.now(timezone.utc), is_latest=True,
        reviewer_username="bob", reviewer_display_name="Dr. Bob",
    )
    runs = [
        RunWithReviews(
            run_id="aud_abcdefghijkl", rule_id="R191", patient_id="J66252",
            verdict="VIOLATION", confidence=0.92,
            reasoning="测试 reasoning",
            evidence_json='[{"source":"fee"}]',
            created_at=datetime.now(timezone.utc),
            reviews=[my, other],
        )
    ]
    out = render(
        "patient_detail.html",
        title="J66252",
        current_user=alice,
        patients=[],
        active_patient="J66252",
        filter="v_and_i",
        filter_label="违规 + 不明",
        runs=runs,
    )
    assert "R191" in out
    assert "verdict-v" in out  # Javert verdict CSS class
    assert "您的审核" in out  # my_review block
    assert "符合规则" in out
    assert "Dr. Bob" in out  # other reviewer
    assert "其他专家审核" in out


def test_patient_detail_v_card_no_review_yet(alice):
    runs = [
        RunWithReviews(
            run_id="aud_xxxxxxxxxxxx", rule_id="R220", patient_id="J18906",
            verdict="VIOLATION", confidence=0.85,
            reasoning="", created_at=datetime.now(timezone.utc),
            reviews=[],
        )
    ]
    out = render(
        "patient_detail.html",
        title="J18906",
        current_user=alice,
        patients=[],
        active_patient="J18906",
        filter="v_and_i",
        filter_label="违规 + 不明",
        runs=runs,
    )
    assert "您的审核" not in out  # 未审
    assert 'name="verdict"' in out  # form 存在
    assert "其他专家审核" not in out


def test_patient_detail_hit_items_block(alice):
    """命中项目块 — 仅渲 fee/drug (出码+限定+复核标注+data-anchor); note 类不单列, 收敛成查文书按钮."""
    from javert.web.hit_resolver import Anchor, HitItem

    hits = [
        HitItem(
            source="drug", name="注射用福沙匹坦双葡甲胺",
            code_nat="XA04AD012", code_local="210999111",
            restriction="限放化疗所致恶心呕吐。",
            matched_fee_name="(集)注射用福沙匹坦双葡甲胺",
            review_note="按通用名匹配, 剂型/复方需复核",
            anchor=Anchor(tab="fees", query="(集)注射用福沙匹坦双葡甲胺", match_level="keyword"),
        ),
        HitItem(
            source="note", name="出院诊断",
            anchor=Anchor(tab="notes", subsection="出院诊断",
                          query="甲状腺乳头状癌", match_level="locator"),
        ),
    ]
    run = RunWithReviews(
        run_id="aud_hititem0001", rule_id="R007", patient_id="J26355",
        verdict="VIOLATION", confidence=0.9, reasoning="r",
        evidence_json="[]", created_at=datetime.now(timezone.utc), reviews=[],
    )
    out = render(
        "patient_detail.html", title="J26355", current_user=alice,
        patients=[], active_patient="J26355", filter="v_and_i",
        filter_label="违规 + 不明", runs=[run],
        hits_by_run={run.run_id: hits},
    )
    # 仅 fee/drug 进命中项目 (note 被过滤) → 计数 1
    assert "命中项目 (1)" in out
    assert "XA04AD012" in out  # 国家码
    assert 'title="院内码 210999111"' in out  # 院内码 hover
    assert "限定: 限放化疗所致恶心呕吐。" in out
    assert "按通用名匹配, 剂型/复方需复核" in out
    assert "data-anchor=" in out
    assert "hit-src-drug" in out
    # note 命中不单列, 收敛成「查阅文书原文」按钮
    assert "hit-src-note" not in out
    assert "查阅文书原文" in out
    # data-anchor 只含 drug 的 fees 锚点, 不含 note 的 (note 已过滤)
    import json
    import re
    anchors = [json.loads(m) for m in re.findall(r"data-anchor='([^']*)'", out)]
    assert any(a.get("tab") == "fees" and a.get("query") for a in anchors)
    assert not any(a.get("tab") == "notes" for a in anchors)


def test_patient_detail_uses_leaflet_label_for_off_label_rule(alice):
    """超说明书规则显示说明书适应证；医保限定规则继续沿用原有“限定”口径。"""
    from javert.web.hit_resolver import Anchor, HitItem

    off_label = RunWithReviews(
        run_id="aud_leaflet_basis", rule_id="RD_SYNTH_OFF", patient_id="P-SYNTH",
        verdict="VIOLATION", confidence=0.9, reasoning="r",
        created_at=datetime.now(timezone.utc), reviews=[],
    )
    insurance = RunWithReviews(
        run_id="aud_insurance_basis", rule_id="RD_SYNTH_INS", patient_id="P-SYNTH",
        verdict="VIOLATION", confidence=0.9, reasoning="r",
        created_at=datetime.now(timezone.utc), reviews=[],
    )
    common_meta = {
        "violation_type": "超范围支付",
        "behavior_name": "超范围支付",
        "subtitle": "",
        "question": "合成测试问题",
    }
    out = render(
        "patient_detail.html", title="t", current_user=alice, patients=[],
        active_patient="P-SYNTH", filter="v_and_i", filter_label="x",
        runs=[off_label, insurance],
        rule_meta={
            off_label.rule_id: {**common_meta, "drug_rule_type": "超说明书"},
            insurance.rule_id: {**common_meta, "drug_rule_type": "限适应症"},
        },
        hits_by_run={
            off_label.run_id: [
                HitItem(
                    source="drug", name="合成药甲",
                    restriction="说明书适应证原文。",
                    anchor=Anchor(tab="fees", query="合成药甲"),
                ),
            ],
            insurance.run_id: [
                HitItem(
                    source="drug", name="合成药乙",
                    restriction="限特定诊断患者。",
                    anchor=Anchor(tab="fees", query="合成药乙"),
                ),
            ],
        },
    )

    assert "说明书适应证: 说明书适应证原文。" in out
    assert "限定: 限特定诊断患者。" in out


def test_patient_detail_run_groups_chips_and_ordering(alice):
    """细类分组渲染: 顶部 chip (别名+计数) + 可折叠组 (按锚点 id) + 组内 V 前 I 后 + 只看不明 toggle."""
    def mkrun(rid, verdict):
        return RunWithReviews(
            run_id="aud_" + rid, rule_id=rid, patient_id="J66252",
            verdict=verdict, confidence=0.8, reasoning="r",
            created_at=datetime.now(timezone.utc), reviews=[],
        )
    # 模拟 route._group_runs_by_violation_type 的产出 (组内已 V 前 I 后)
    run_groups = [
        {"vt": "过度检查", "alias": "过度检查", "anchor": "vt-0", "n_v": 1, "n_i": 1,
         "runs": [mkrun("R151", "VIOLATION"), mkrun("R152", "INCONCLUSIVE")]},
        {"vt": "重复收费", "alias": "重复收费", "anchor": "vt-1", "n_v": 1, "n_i": 0,
         "runs": [mkrun("R191", "VIOLATION")]},
    ]
    all_runs = run_groups[0]["runs"] + run_groups[1]["runs"]
    out = render(
        "patient_detail.html", title="t", current_user=alice, patients=[],
        active_patient="J66252", filter="v_and_i", filter_label="x",
        runs=all_runs, run_groups=run_groups,
    )
    # chip 行 + 每组一 chip (data-target 指组锚点)
    assert 'class="vt-chip-row"' in out
    assert 'data-target="vt-0"' in out
    assert 'data-target="vt-1"' in out
    assert "过度检查" in out and "重复收费" in out
    # 组标题 (锚点 id + 计数)
    assert 'id="vt-0"' in out
    assert 'id="vt-1"' in out
    assert "1 违 · 1 不明" in out
    # 组内 V 前 I 后: R151(V) 在 R152(I) 之前
    assert out.index("aud_R151") < out.index("aud_R152")
    # 只看不明 toggle + data-has-i (重复收费组 n_i=0)
    assert "只看不明" in out
    assert "toggleInconclusiveOnly" in out
    assert 'data-has-i="0"' in out
    # behavior-naming: 卡片头显示行为认定名称 (R 代号进 hover title), alias chip 已合并
    assert "rule-subtitle-alias" not in out
    assert 'class="rule-id" title="' in out


def test_patient_detail_long_comment_hover_full_text(alice):
    """G5/D9: 其他专家长评语截断显 … , 但 title 带全文 (零接口往返)."""
    long_comment = "这是一段超过八十个字符的很长很长的专家评语" * 3
    other = ReviewRecord(
        id=2, run_id="aud_longcmt0001", user_id=99, review_verdict="C",
        comment=long_comment, created_at=datetime.now(timezone.utc),
        is_latest=True, reviewer_username="bob", reviewer_display_name="Dr. Bob",
    )
    run = RunWithReviews(
        run_id="aud_longcmt0001", rule_id="R191", patient_id="J66252",
        verdict="VIOLATION", confidence=0.9, reasoning="r",
        created_at=datetime.now(timezone.utc), reviews=[other],
    )
    out = render(
        "patient_detail.html", title="t", current_user=alice, patients=[],
        active_patient="J66252", filter="v_and_i", filter_label="x", runs=[run],
    )
    assert "…" in out  # 视觉截断
    assert f'title="{long_comment}"' in out  # 全文在 title
    assert "other-comment" in out


def test_dashboard_renders(alice):
    stats = DashboardStats(
        total_v_or_i=395,
        total_reviewed_runs=187,
        progress_pct=47.3,
        agreement_v_pct=80.0,
        reviewers=[
            ReviewerLeaderRow(
                user_id=1, username="alice", display_name="Dr. Alice",
                latest_review_count=92,
            ),
            ReviewerLeaderRow(
                user_id=2, username="bob", display_name=None,
                latest_review_count=65,
            ),
        ],
        rules=[
            RuleAgreementRow(
                rule_id="R191", v_count=18, i_count=0,
                expert_agreed=12, expert_overturned=3, expert_pending=3,
            ),
        ],
    )
    out = render(
        "dashboard.html",
        title="仪表盘",
        current_user=alice,
        stats=stats,
    )
    assert "47.3" in out
    assert "187" in out
    assert "alice" in out
    assert "R191" in out
    assert "12" in out  # expert_agreed
    assert "highlight" in out  # current user 行加 highlight 类
