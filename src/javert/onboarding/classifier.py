# -*- coding: utf-8 -*-
"""classifier — 文件→目标表自动归类 (设计 D2, 最小启发式).

列名对各 tabular spoke 必填字段别名的覆盖率 → Top-1 归属 + 患者键硬门槛.
刻意保持最小: 只用「必填覆盖率 + 患者键硬门槛」, 不引需真实数据标定的复杂打分阈值
(对抗审查: 无数据先调阈值是过早抽象). Top-1 不明确 (覆盖相当 / 无患者键) → 标歧义, 不猜.

归类 MUST 只作用于 tabular spoke (费用/文书/诊断/手术/化验/检查 6 张);
view spoke (麻醉/病理) 无 output_file → 不在 manifest.tabular_spokes(), 天然不参与 (设计 D9).

键模式默认取 manifest 声明 (确定性, 无需探测): via_bridge 非空 → bridge, 否则 synth
(synth 据 id_form 落 compound/bare). 选错由预检红灯兜住, 操作者在驾驶舱一键改 (设计 D2).

GUI (/api/onboarding/classify) 与 CLI 共用. 别名匹配逻辑与前端 onboarding.js aliasMatch 同语义.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from javert.onboarding.manifest_loader import Manifest, Spoke


def _norm(s: str) -> str:
    return str(s or "").strip().lower()


def alias_match(aliases: list[str], columns: list[str]) -> str | None:
    """单字段别名→列名匹配 (精确 → 包含). 与前端 onboarding.js aliasMatch 同语义."""
    al = [_norm(a) for a in aliases if _norm(a)]
    if not al:
        return None
    norm_cols = [(c, _norm(c)) for c in columns]
    # 1. 精确
    for c, nc in norm_cols:
        if nc in al:
            return c
    # 2. 包含 (列名含别名 或 别名含列名)
    for c, nc in norm_cols:
        for a in al:
            if a and (a in nc or nc in a):
                return c
    return None


def match_fields(spoke: Spoke, columns: list[str], alias: dict) -> dict[str, str]:
    """spoke 的各语义字段 → 命中的源列名 (按 field_alias). 用于自动预填映射."""
    spoke_alias = alias.get(spoke.key, {}) or {}
    out: dict[str, str] = {}
    for f in spoke.fields:
        m = alias_match(spoke_alias.get(f.key, []) or [], columns)
        if m:
            out[f.key] = m
    return out


def default_key_mode(spoke: Spoke) -> str:
    """键模式默认 (设计 D2/5.2): via_bridge → bridge, 否则 synth (据 id_form 落 compound/bare)."""
    return "bridge" if spoke.via_bridge else "synth"


@dataclass
class SpokeScore:
    spoke: str
    matched: dict[str, str]    # field_key -> col
    has_patient_key: bool
    required_hit: int
    required_total: int
    required_non_pid_hit: int = 0   # 命中的必填字段中, 患者键以外的数量

    @property
    def coverage(self) -> float:
        return self.required_hit / self.required_total if self.required_total else 0.0


@dataclass
class ClassifyResult:
    spoke: str | None          # Top-1 归属 (歧义/无命中 → None)
    ambiguous: bool
    matched: dict              # 选中 spoke 命中的字段 (歧义时空)
    candidates: list           # [{spoke,name,coverage,required_hit,required_total,has_patient_key}]
    reason: str = ""           # 歧义/无命中文案

    def to_dict(self) -> dict:
        return {
            "spoke": self.spoke, "ambiguous": self.ambiguous,
            "matched": self.matched, "candidates": self.candidates, "reason": self.reason,
        }


def classify_columns(columns: list[str], manifest: Manifest, alias: dict) -> ClassifyResult:
    """对一组列名给 Top-1 tabular spoke 归属猜测 (最小启发式, 设计 D2)."""
    scores: list[SpokeScore] = []
    for key, spoke in manifest.tabular_spokes().items():
        matched = match_fields(spoke, columns, alias)
        req = spoke.required_keys
        hit = sum(1 for r in req if r in matched)
        non_pid = sum(1 for r in req if r in matched and r != "patient_id")
        scores.append(SpokeScore(
            spoke=key, matched=matched,
            has_patient_key="patient_id" in matched,
            required_hit=hit, required_total=len(req), required_non_pid_hit=non_pid,
        ))

    candidates_view = sorted(
        ({"spoke": s.spoke, "name": manifest.spoke(s.spoke).name,
          "coverage": round(s.coverage, 3), "required_hit": s.required_hit,
          "required_total": s.required_total, "has_patient_key": s.has_patient_key}
         for s in scores if s.required_hit > 0 or s.has_patient_key),
        key=lambda d: (d["coverage"], d["required_hit"]), reverse=True,
    )

    # 候选硬门槛: 有患者键 + 至少命中 1 个「患者键以外」的必填字段.
    # (只命中患者键的文件 — 如桥表/对照表 — 无法据此定表, 标歧义不猜)
    cands = [s for s in scores if s.has_patient_key and s.required_non_pid_hit > 0]
    if not cands:
        return ClassifyResult(None, True, {}, candidates_view,
                              "只看到患者键、缺别的必填字段 — 请确认这是哪张表 (桥表/对照表可忽略)")
    cands.sort(key=lambda s: (s.coverage, s.required_hit), reverse=True)
    top = cands[0]
    # 歧义: 次优覆盖率与最优相当 (并列) → 不猜
    if len(cands) > 1 and abs(cands[1].coverage - top.coverage) < 1e-9 \
            and cands[1].required_hit == top.required_hit:
        return ClassifyResult(None, True, {}, candidates_view,
                              "多张表覆盖相当, 请确认这是哪张表")
    return ClassifyResult(top.spoke, False, top.matched, candidates_view, "")
