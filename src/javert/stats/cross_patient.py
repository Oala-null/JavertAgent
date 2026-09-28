# -*- coding: utf-8 -*-
"""规则维度跨患者聚合 + 系统性违规判定 (add-cross-patient-stats).

只读: 把 audit_runs 的最新裁决按 rule_id 聚合成 V 率 / 系统性标记. 两个纯函数
(`aggregate` / `compute_rule_stats`) 是「系统性违规」判定的唯一实现, CLI (sqlite) 与
工作台 (142) 都喂同一函数, 不各自算阈值.

latest 语义 (每 (rule_id, patient_id) 取最新一条) 由 store 的 `latest_verdict_rows` 负责,
本模块只对已去重的 (rule_id, patient_id, verdict) 三元组计数. 见 design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from javert.config import PROJECT_ROOT

_THRESHOLDS_REL = "configs/systemic_thresholds.yaml"


class Thresholds(BaseModel):
    """系统性违规双阈值 + 按规则覆盖."""

    v_rate: float = 0.5
    min_patients: int = 10
    overrides: dict[str, dict] = Field(default_factory=dict)

    def for_rule(self, rule_id: str) -> tuple[float, int]:
        """该规则生效的 (v_rate, min_patients), overrides 只覆盖填了的字段."""
        ov = self.overrides.get(rule_id) or {}
        return (
            float(ov.get("v_rate", self.v_rate)),
            int(ov.get("min_patients", self.min_patients)),
        )


def load_thresholds(path: Path | None = None) -> Thresholds:
    """读 configs/systemic_thresholds.yaml; 缺文件 → 默认阈值."""
    p = path or (PROJECT_ROOT / _THRESHOLDS_REL)
    if not p.exists():
        return Thresholds()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Thresholds(**data)


@dataclass
class RuleAgg:
    """一条规则的跨患者计数 (latest 去重后)."""

    rule_id: str
    n_patients: int  # 被审计患者数 (分母, 含 CLEAN)
    v: int
    i: int
    c: int
    v_patients: list[str] = field(default_factory=list)  # V 患者号 (drilldown 用)


@dataclass
class RuleStat:
    """RuleAgg + 阈值判定结果."""

    rule_id: str
    n_patients: int
    v: int
    i: int
    c: int
    v_rate: float
    i_rate: float
    systemic: bool
    reason: str
    v_patients: list[str] = field(default_factory=list)
    amount: float | None = None  # v1 恒 None → 渲染「不可计」(见 design.md)


def aggregate(rows: list[tuple[str, str, str]]) -> list[RuleAgg]:
    """把已去重的 (rule_id, patient_id, verdict) 三元组按 rule_id 聚合成 RuleAgg[].

    传入 rows MUST 已是 latest-per-(rule,patient) (store 负责去重); 本函数只计数.
    """
    buckets: dict[str, RuleAgg] = {}
    for rule_id, patient_id, verdict in rows:
        agg = buckets.get(rule_id)
        if agg is None:
            agg = RuleAgg(rule_id=rule_id, n_patients=0, v=0, i=0, c=0)
            buckets[rule_id] = agg
        agg.n_patients += 1
        if verdict == "VIOLATION":
            agg.v += 1
            agg.v_patients.append(patient_id)
        elif verdict == "INCONCLUSIVE":
            agg.i += 1
        elif verdict == "CLEAN":
            agg.c += 1
    return list(buckets.values())


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def compute_rule_stats(aggs: list[RuleAgg], thresholds: Thresholds) -> list[RuleStat]:
    """对每条 RuleAgg 判系统性违规, 返回 V 率倒序的 RuleStat[].

    systemic = n_patients >= min_patients AND v_rate >= v_rate_threshold (按规则阈值).
    """
    out: list[RuleStat] = []
    for agg in aggs:
        n = agg.n_patients
        v_rate = (agg.v / n) if n else 0.0
        i_rate = (agg.i / n) if n else 0.0
        thr_rate, thr_min = thresholds.for_rule(agg.rule_id)
        systemic = n >= thr_min and v_rate >= thr_rate
        if n < thr_min:
            reason = f"样本不足 ({n} < {thr_min} 患者), 不判定"
        elif systemic:
            reason = f"{n} 患者中 {agg.v} 例违规, V 率 {_pct(v_rate)} (≥ 阈值 {_pct(thr_rate)})"
        else:
            reason = f"{n} 患者中 {agg.v} 例违规, V 率 {_pct(v_rate)} (< 阈值 {_pct(thr_rate)})"
        out.append(RuleStat(
            rule_id=agg.rule_id,
            n_patients=n,
            v=agg.v,
            i=agg.i,
            c=agg.c,
            v_rate=v_rate,
            i_rate=i_rate,
            systemic=systemic,
            reason=reason,
            v_patients=agg.v_patients,
        ))
    out.sort(key=lambda s: (-s.v_rate, -s.n_patients, s.rule_id))
    return out
