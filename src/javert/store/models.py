# -*- coding: utf-8 -*-
"""Workbench 数据模型 (pydantic v2).

仅 142 工作台需要的 User / Review / Log + 派生 stats 类型. 审计裁决本体复用
``javert.audit.result.AuditResult``, 不在这里重复.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from javert.oncology.contracts import EligibilityEvaluation
from javert.promises.models import PromiseTrace


ReviewVerdict = Literal["V", "I", "C"]


class User(BaseModel):
    """javert_users 一行 (不含 pw_hash)."""

    model_config = ConfigDict(frozen=False)

    id: int
    username: str
    display_name: str | None = None
    created_at: datetime
    last_login: datetime | None = None


class ReviewRecord(BaseModel):
    """javert_vio_review 一行."""

    id: int
    run_id: str
    user_id: int
    review_verdict: ReviewVerdict
    comment: str | None = None
    created_at: datetime
    is_latest: bool
    # JOIN javert_users 时回填; 非持久化字段
    reviewer_username: str | None = None
    reviewer_display_name: str | None = None
    # JOIN javert_audit_runs 时回填 (submit_review 顺手取);
    # SSE 广播需要靠 patient_id 找 sidebar 卡片
    patient_id: str | None = None
    rule_id: str | None = None
    # submit_review 区分首次提交 vs 改判 — None 表示首次, 否则是上一版 verdict
    previous_verdict: str | None = None
    # submit_review 顺手回填 (SSE 全局计数增量用, 非持久化):
    #   run_first_review = 该 run 是否本次才被「任意专家」首评 (避免改判/二次审重复 +1)
    #   run_audit_verdict = 该 run 的 AI 裁决 (VIOLATION/INCONCLUSIVE/CLEAN), 判定是否落当前 filter 命中集
    run_first_review: bool = False
    run_audit_verdict: str | None = None


class AuditLogRecord(BaseModel):
    """javert_audit_logs 一行."""

    id: int
    user_id: int | None = None
    action: str
    target_id: str | None = None
    payload_json: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    ts: datetime


class SinceLastLoginStats(BaseModel):
    """欢迎 banner 用的 4 元组."""

    new_patients: int = Field(default=0, ge=0)
    new_violations: int = Field(default=0, ge=0)
    new_inconclusive: int = Field(default=0, ge=0)
    last_review_at: datetime | None = None


class PatientSidebarItem(BaseModel):
    """workbench 左侧 patient sidebar 单行."""

    patient_id: str
    v_count: int = 0
    i_count: int = 0
    c_count: int = 0
    reviewed_count: int = 0  # 该 patient 上「团队」已审条数 (任意专家, 全局口径; filter 下分子)
    relevant_count: int = 0  # 当前 filter 下命中的总条数 (sidebar 进度分母)
    fully_reviewed: bool = False
    # v0.7: 该 patient 的 latest batch_tag (跨 rule 取 max created_at 的). None = baseline.
    batch_tag: str | None = None
    # v0.9 (enhance-workbench-usability): 富卡片摘要 — 渲染前由路由填充, 默认值不破坏现有构造.
    fees_sum: float = 0.0          # 该 patient 费用合计 (进程级 groupby 缓存, 近似值)
    primary_dx: str = ""           # 主诊断 (病案首页 maindiag_flag=1 优先, note 兜底)
    updated_at: datetime | None = None  # 该 patient 最近一次审计 MAX(created_at)


class HistoricalRun(BaseModel):
    """同 (rule, patient) 的老 audit_run + 当时的专家批注 — latest 之外的历史.

    v0.7 引入: v1.2 重跑后, 工作台默认显示 latest (v1.2);
    用户能在违规卡末尾展开"📜 历史 N 次"看到 v1.0 verdict + 当时批注.
    """

    run_id: str
    verdict: str
    confidence: float
    reasoning: str
    batch_tag: str | None = None
    created_at: datetime
    reviews: list[ReviewRecord] = Field(default_factory=list)
    eligibility_evaluation: EligibilityEvaluation | None = None
    promise_trace: PromiseTrace | None = None


class RunWithReviews(BaseModel):
    """patient detail 单条 violation 卡片所需的全部信息."""

    run_id: str
    rule_id: str
    patient_id: str
    verdict: str
    confidence: float
    reasoning: str
    evidence_json: str | None = None
    tool_calls_json: str | None = None
    duration_ms: int | None = None
    model: str | None = None
    started_at: datetime | None = None
    created_at: datetime
    triggered_by: str | None = None
    batch_tag: str | None = None  # v0.7: 这是 latest run 的 tag (v1.2 或 NULL)
    gate_tag: str = ""  # add-verdict-gate-layer: gate 降级标签 (缺文书/单次放过/低置信降级/'')
    eligibility_evaluation: EligibilityEvaluation | None = None
    promise_trace: PromiseTrace | None = None
    reviews: list[ReviewRecord] = Field(default_factory=list)
    history: list[HistoricalRun] = Field(default_factory=list)  # v0.7


class DashboardStats(BaseModel):
    """/dashboard 渲染需要的全部聚合."""

    total_v_or_i: int = 0
    total_reviewed_runs: int = 0
    progress_pct: float = 0.0
    # add-verdict-gate-layer: gate_tag=缺文书 的 latest 裁决数 (待线下核查桶)
    pending_offline_check: int = 0

    reviewers: list["ReviewerLeaderRow"] = Field(default_factory=list)
    rules: list["RuleAgreementRow"] = Field(default_factory=list)

    agreement_v_pct: float = 0.0
    agreement_i_pct: float = 0.0


class ReviewerLeaderRow(BaseModel):
    user_id: int
    username: str
    display_name: str | None = None
    latest_review_count: int


class RuleAgreementRow(BaseModel):
    rule_id: str
    v_count: int
    i_count: int
    expert_agreed: int
    expert_overturned: int
    expert_pending: int


class ReviewerDrillRow(BaseModel):
    """dashboard 专家下钻 — 单条 expert review + 关联 audit_run 简要,
    供 /dashboard/reviewer/{uid} 模板渲染 + 跳转 #run_xxx."""

    review_id: int
    run_id: str
    patient_id: str
    rule_id: str
    expert_verdict: ReviewVerdict
    agent_verdict: str  # VIOLATION / CLEAN / INCONCLUSIVE
    agent_confidence: float | None = None
    comment: str | None = None
    created_at: datetime


DashboardStats.model_rebuild()
