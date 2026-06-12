# -*- coding: utf-8 -*-
"""join_preflight — 连接预检 (设计 D4/D6, 纯计算不调 LLM).

判据 = "两个 getter 都能解析得出同一 patient_id" 而非键集合相等 (设计 D4):
loader.get_notes 精确相等 (住院号==pid), get_fees 包含匹配 (bah.contains(pid)),
复合键 vs 裸号形态差会骗过集合相等, 故逐 id 实际跑 getter 语义验证.

结论三态:
- 🟢 green   覆盖率高 → 可靠
- 🟡 yellow  部分匹配 / 时间窗口错位 → 可继续 (弹提示)
- 🔴 red     键几乎不交 (<10%) → 挡住, 大概率列映射错

GUI (/onboarding) 与 CLI (etl_import 校验阶段) 共用本模块.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from javert.onboarding.etl_engine import SpokeResult
from javert.onboarding.manifest_loader import Spoke
from javert.onboarding.profiler import detect_date_format, normalize_date_series

GREEN_THRESHOLD = 0.9   # ≥ → 🟢
RED_THRESHOLD = 0.1     # < → 🔴 (其间为 🟡)

_VERDICT_META = {
    "green": ("🟢", "可靠"),
    "yellow": ("🟡", "部分匹配可继续"),
    "red": ("🔴", "键几乎不交"),
}


def _verdict_of(coverage: float) -> str:
    if coverage >= GREEN_THRESHOLD:
        return "green"
    if coverage < RED_THRESHOLD:
        return "red"
    return "yellow"


def extract_bare_ids(df: pd.DataFrame, spoke: Spoke) -> list[str]:
    """从 spoke 输出表抽裸号 (compound 取末段, bare 直接取)."""
    col = spoke.id_column
    if not col or col not in df.columns:
        return []
    vals = [str(v).strip() for v in df[col].tolist() if str(v).strip()]
    if spoke.id_form == "compound":
        out = [v.split("-")[-1].strip() for v in vals]
    else:
        out = vals
    # 去重保序
    seen: set[str] = set()
    return [x for x in out if x and not (x in seen or seen.add(x))]


def id_hits(df: pd.DataFrame, spoke: Spoke, pid: str) -> bool:
    """按目标 getter 语义判 pid 在该表是否解析得出非空 (设计 D4)."""
    col = spoke.id_column
    if not col or col not in df.columns:
        return False
    series = df[col].astype(str)
    if spoke.id_form == "compound":
        return bool(series.str.contains(pid, na=False, regex=False).any())
    return bool((series.str.strip() == pid).any())


@dataclass
class KeyPreflight:
    primary: str                  # 参照 spoke key
    coverage: float               # 候选 id 在所有其他 live tabular spoke 都命中的比例
    total: int
    hit: int
    misses: list[str]             # 未全命中的裸号
    per_spoke: dict[str, float]   # 每个其他 spoke 的命中率
    verdict: str

    @property
    def emoji(self) -> str:
        return _VERDICT_META[self.verdict][0]

    @property
    def label(self) -> str:
        return _VERDICT_META[self.verdict][1]


def preflight_keys(spokes: list[SpokeResult], spoke_meta: dict[str, Spoke],
                   primary: str = "fees") -> KeyPreflight | None:
    """以 primary spoke 的裸号为候选, 验证它们在其余 tabular spoke 都解析得出.

    返回 None 表示不足两张表无法预检.
    """
    present = {s.key: s for s in spokes}
    if primary not in present:
        # primary 不在 → 取第一张表
        if not spokes:
            return None
        primary = spokes[0].key
    others = [s for s in spokes if s.key != primary]
    if not others:
        return None

    cand = extract_bare_ids(present[primary].df, spoke_meta[primary])
    if not cand:
        return KeyPreflight(primary, 0.0, 0, 0, [], {}, "red")

    per_spoke: dict[str, float] = {}
    for s in others:
        meta = spoke_meta[s.key]
        hits = sum(1 for pid in cand if id_hits(s.df, meta, pid))
        per_spoke[s.key] = hits / len(cand)

    misses: list[str] = []
    full_hit = 0
    for pid in cand:
        if all(id_hits(s.df, spoke_meta[s.key], pid) for s in others):
            full_hit += 1
        else:
            misses.append(pid)
    coverage = full_hit / len(cand)
    return KeyPreflight(
        primary=primary, coverage=coverage, total=len(cand), hit=full_hit,
        misses=misses[:50], per_spoke=per_spoke, verdict=_verdict_of(coverage),
    )


@dataclass
class TimeWindowPreflight:
    checked: int
    in_window: int
    out_window: int
    out_rate: float
    verdict: str
    note: str = ""

    @property
    def emoji(self) -> str:
        return _VERDICT_META[self.verdict][0]


def preflight_time_window(window_series: pd.Series, event_series: pd.Series,
                          window_label: str = "住院期", event_label: str = "记录") -> TimeWindowPreflight:
    """时间窗口重叠: event 日期落在 window [min,max] 内的比例 (揭露化验时间落住院期外).

    window/event 任一为纯时间无日期列 → 跳过 (note 说明).
    """
    wprof = detect_date_format(window_series.tolist())
    eprof = detect_date_format(event_series.tolist())
    if not wprof.can_time_window or not eprof.can_time_window:
        return TimeWindowPreflight(0, 0, 0, 0.0, "yellow",
                                   note=f"{event_label}或{window_label}列无日期分量, 跳过时间窗口分析")
    w = pd.to_datetime(normalize_date_series(window_series, wprof), errors="coerce").dropna()
    e = pd.to_datetime(normalize_date_series(event_series, eprof), errors="coerce").dropna()
    if w.empty or e.empty:
        return TimeWindowPreflight(0, 0, 0, 0.0, "yellow", note="窗口或事件日期为空, 无法判定")
    lo, hi = w.min(), w.max()
    inside = int(((e >= lo) & (e <= hi)).sum())
    total = len(e)
    out = total - inside
    out_rate = out / total
    # 时间错位只给 🟢/🟡 (按 spec, 不给 🔴 挡住 — 时间窗口是提示而非硬闸)
    verdict = "green" if out_rate <= RED_THRESHOLD else "yellow"
    note = ""
    if out_rate > RED_THRESHOLD:
        note = f"{out_rate*100:.0f}% {event_label}落在{window_label}外, 可加日期窗口过滤"
    return TimeWindowPreflight(total, inside, out, out_rate, verdict, note)


def _primary_date_col(spoke: Spoke) -> str | None:
    """spoke 的主日期输出列 (第一个 is_date 字段的目标列)."""
    for f in spoke.fields:
        if f.is_date and f.targets:
            return f.targets[0]
    return None


def preflight_time_windows(spokes: list[SpokeResult], spoke_meta: dict[str, Spoke]) -> list[dict]:
    """对时间敏感 spoke (化验/检查/手术) 相对住院期 (费用日期区间) 算时间窗口重叠.

    无费用窗口或无时间敏感事件表 → 返回 []. (接入级粗检, 揭露化验日期整体落住院期外的错位.)
    """
    present = {s.key: s for s in spokes}
    if "fees" not in present:
        return []
    wcol = _primary_date_col(spoke_meta["fees"])
    fees_df = present["fees"].df
    if not wcol or wcol not in fees_df.columns:
        return []
    window = fees_df[wcol]
    out: list[dict] = []
    for ev_key in ("labs", "examinations", "surgeries"):
        if ev_key not in present:
            continue
        meta = spoke_meta[ev_key]
        ecol = _primary_date_col(meta)
        if not ecol or ecol not in present[ev_key].df.columns:
            continue
        tw = preflight_time_window(
            window, present[ev_key].df[ecol],
            window_label="住院期(费用区间)", event_label=meta.name)
        out.append({
            "spoke": ev_key, "name": meta.name, "checked": tw.checked,
            "out_window": tw.out_window, "out_rate": tw.out_rate,
            "verdict": tw.verdict, "emoji": tw.emoji, "note": tw.note,
        })
    return out
