# -*- coding: utf-8 -*-
"""Jinja2 环境单例 — 注入 humanize 过滤器 + verdict 颜色 helper."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .utils.humanize_zh import humanize_delta_zh, humanize_since_zh


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    """静态资源版本号 = app.js/style.css 最新 mtime — 每次部署 (scp 改 mtime) 自动变,
    模板据此给资源 URL 加 ?v=, 浏览器换 URL 拉新, 杜绝缓存看到旧前端 (无需手动 bump)."""
    try:
        ts = [
            (STATIC_DIR / f).stat().st_mtime
            for f in ("app.js", "style.css", "onboarding.js")
            if (STATIC_DIR / f).exists()
        ]
        return str(int(max(ts))) if ts else "0"
    except Exception:  # noqa: BLE001
        return "0"


def _verdict_color(verdict: str | None) -> str:
    """Javert / 专家 verdict 字符串 → CSS class 后缀."""
    v = (verdict or "").upper()
    if v in ("V", "VIOLATION"):
        return "v"
    if v in ("I", "INCONCLUSIVE"):
        return "i"
    if v in ("C", "CLEAN"):
        return "c"
    return "unknown"


def _verdict_label_zh(verdict: str | None) -> str:
    v = (verdict or "").upper()
    return {
        "V": "认同",
        "I": "改判不明",
        "C": "驳回",
        "VIOLATION": "违规",
        "INCONCLUSIVE": "不明",
        "CLEAN": "干净",
    }.get(v, verdict or "")


# 肿瘤资格 follow-up 面板: criterion_type / state 英文 → 中文 (前端友好清单).
_CRITERION_TYPE_ZH = {
    "diagnosis": "诊断",
    "stage": "分期 / 转移",
    "biomarker": "病理标志物（免疫组化）",
    "prior_therapy": "既往治疗",
    "line_of_therapy": "治疗线数",
    "treatment_status": "复发 / 难治状态",
    "clinician_assessment": "移植适合性评估",
    "payer_scope": "支付性质",
    "unsupported": "其他条件",
}
_STATE_ZH = {
    "SATISFIED": "满足",
    "NOT_SATISFIED": "不满足",
    "UNKNOWN": "文书未见",
    "CONFLICT": "证据冲突",
}
_STATE_MARK = {"SATISFIED": "✓", "NOT_SATISFIED": "✗", "UNKNOWN": "？", "CONFLICT": "⚠"}


def _criterion_label(criterion_type: str | None) -> str:
    return _CRITERION_TYPE_ZH.get((criterion_type or "").strip(), criterion_type or "条件")


def _state_zh(state: str | None) -> str:
    return _STATE_ZH.get((state or "").strip(), state or "")


def _state_mark(state: str | None) -> str:
    return _STATE_MARK.get((state or "").strip(), "·")


# criterion 证据锚点 → 可跳原文的 anchor (复用命中项目的 openSourcePanel 高亮机制).
# 免疫组化等 criterion 命中文书时, follow-up 面板给一个「定位原文」双链.
_NOTE_ANCHOR_SOURCES = ("note", "path", "shi_zd", "diag")


def _onco_evidence_anchor(assessment) -> dict | None:
    """把 criterion 的证据锚点转成 {tab,subsection,query,label}；无文书锚点返回 None.

    biomarker(免疫组化) 优先用归一到的观察原文片段(如 ``CerbB2(1+)``, 是文书 verbatim
    子串)做精确高亮；其余 criterion 退回原文引文/最长 n-gram。
    """
    from .hit_resolver import _quoted_or_ngram  # 延迟导入避免加载期耦合

    anchors = getattr(assessment, "evidence_anchors", None) or []
    note_anchor = next(
        (
            a
            for a in anchors
            if any(s in (getattr(a, "source", "") or "").lower() for s in _NOTE_ANCHOR_SOURCES)
            and (getattr(a, "text", "") or "").strip()
        ),
        None,
    )
    if note_anchor is None:
        return None
    query = ""
    for fact in getattr(assessment, "normalized_facts", None) or []:
        value = str(getattr(fact, "value", "") or "").strip()
        if value and 1 < len(value) <= 40:
            query = value
            break
    if not query:
        query = _quoted_or_ngram(note_anchor.text or "")
    if not query:
        return None
    return {
        "tab": "notes",
        "subsection": getattr(note_anchor, "locator", "") or "",
        "query": query,
        "label": query,
    }


def _onco_needs_date_check(evaluation) -> bool:
    """就诊日在声明生效窗口之外 (即结果未按生效期过滤) → 前端需提示核查生效时间."""
    sd = getattr(evaluation, "evaluated_service_date", None)
    ef = getattr(evaluation, "rule_effective_from", None)
    et = getattr(evaluation, "rule_effective_to", None)
    if sd is None or ef is None:
        return False
    return sd < ef or (et is not None and sd > et)


def _format_dt(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime(fmt)


def build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["humanize_delta"] = humanize_delta_zh
    env.filters["humanize_since"] = humanize_since_zh
    env.filters["verdict_color"] = _verdict_color
    env.filters["verdict_label"] = _verdict_label_zh
    env.filters["criterion_label"] = _criterion_label
    env.filters["state_zh"] = _state_zh
    env.filters["state_mark"] = _state_mark
    env.filters["onco_evidence_anchor"] = _onco_evidence_anchor
    env.filters["onco_needs_date_check"] = _onco_needs_date_check
    env.filters["format_dt"] = _format_dt
    env.globals["now"] = lambda: datetime.now(timezone.utc)
    env.globals["asset_v"] = _asset_version()
    return env


_ENV: Environment | None = None


def get_env() -> Environment:
    global _ENV
    if _ENV is None:
        _ENV = build_env()
    return _ENV


def render(name: str, /, **ctx) -> str:
    return get_env().get_template(name).render(**ctx)
