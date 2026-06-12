"""RuleRouter — Javert 主体 (做不了的规则) 的 prefilter, 输出 Javert yaml 子集.

设计原则 (v2 single-gate):
  - Java engine 14372 字典是"做得了"的违规 — 走单独 Track A (java_engine 模块, Phase 2)
  - Javert yaml 是"做不了"的违规 — 走本 Router B, 单闸用 yaml 自身 metadata 决定

单闸三步:
  1) status / priority 高层 prune
  2) applicable_* metadata prune (灵感来自 Java engine ImsRuleCatch + RuleItemBase.checkRuleValid):
     - applicable_visit_type    (ipt / opt)
     - applicable_gender         (M / F)
     - applicable_age_min/max
     - applicable_diag_codes     (ICD 前缀, 支持 *)
     - applicable_departments
     全 optional; 缺省 = 不限制
  3) yaml.trigger_keywords 弹性命中 patient fee + diagnoses
     - kw 长度 ≥3: 用 60% prefix (e.g. "病理检查" → "病理") 弹性命中
     - kw 长度 ≤2: 精确包含 (防止 "钾" / "钠" 这种单字泛滥)

旧 Case A overlapping (用 Java 字典 AND 闸 prune Javert yaml) 已废弃 — 因为
两者本质不 overlap, AND 闸导致 fee-notes 模式 (M5/M6) 漏检.

数据依赖:
  data/router/javert_rules_index.json   ← 单闸所需
  data/router/violation_dict.json       ← Phase 2 Track A 用, route() 不调
  data/router/active_java_rules.json    ← Phase 2 用
  data/router/pruning_rules.json        ← Phase 2 用
  configs/rule_mapping.json             ← 仅做 Java rule 元信息参考
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .types import FeeItem, PatientRecord, RouterDecision, TriggerEvidence

logger = logging.getLogger("javert.routing")

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATHS = {
    "violation_dict":   _REPO_ROOT / "data" / "router" / "violation_dict.json",
    "active_rules":     _REPO_ROOT / "data" / "router" / "active_java_rules.json",
    "pruning_rules":    _REPO_ROOT / "data" / "router" / "pruning_rules.json",
    "javert_index":     _REPO_ROOT / "data" / "router" / "javert_rules_index.json",
    "rule_mapping":     _REPO_ROOT / "configs" / "rule_mapping.json",
}


@dataclass(frozen=True)
class _JavertMeta:
    """单条 yaml 在 router 视角的精简元数据 (v2 含 applicable_* 结构化字段)."""

    rule_id: str
    domain: Optional[str]
    violation_type: Optional[str]
    status: Optional[str]
    priority: Optional[str]
    template: Optional[str]
    trigger_keywords: tuple[str, ...] = ()
    # applicable_* — 全 optional, yaml 缺省即不限制 (false-negative 安全)
    applicable_visit_type:   tuple[str, ...] = ()    # e.g. ("ipt",)
    applicable_gender:       Optional[str] = None    # "M" / "F"
    applicable_age_min:      Optional[int] = None
    applicable_age_max:      Optional[int] = None
    applicable_diag_codes:   tuple[str, ...] = ()    # ICD 前缀, e.g. ("C73", "D34")
    applicable_departments:  tuple[str, ...] = ()


class RuleRouter:
    """Router B: Javert 主体的 prefilter (单闸版).

    构造时加载 4 个 JSON, 但运行时 route() 只用 javert_rules_index.json.
    其余 (violation_dict / active_rules / pruning_rules / rule_mapping) 保留为
    Phase 2 Java engine track 的入口数据.
    """

    def __init__(
        self,
        violation_dict: dict,
        active_rules: dict,
        pruning_rules: dict,
        javert_index: dict,
        rule_mapping: dict,
        *,
        enabled_statuses: tuple[str, ...] = ("ready",),
        enabled_priorities: tuple[str, ...] = ("P0", "P1", "P2", "P3"),
        keyword_prefix_ratio: float = 0.6,
        keyword_short_threshold: int = 2,
    ) -> None:
        self.violation_dict = violation_dict
        self.active_rules = active_rules
        self.pruning_rules = pruning_rules
        self.javert_index = javert_index
        self.rule_mapping = rule_mapping
        self.enabled_statuses = set(enabled_statuses)
        self.enabled_priorities = set(enabled_priorities)
        self.keyword_prefix_ratio = keyword_prefix_ratio
        self.keyword_short_threshold = keyword_short_threshold

        # yaml index → _JavertMeta (含 applicable_* 字段, optional)
        self._yaml_meta: dict[str, _JavertMeta] = {}
        for y in javert_index["rules"]:
            self._yaml_meta[y["rule_id"]] = _JavertMeta(
                rule_id=y["rule_id"],
                domain=y.get("domain"),
                violation_type=y.get("violation_type"),
                status=y.get("status"),
                priority=y.get("priority"),
                template=y.get("derived_from_template"),
                trigger_keywords=tuple(y.get("trigger_keywords") or []),
                applicable_visit_type=tuple(y.get("applicable_visit_type") or []),
                applicable_gender=y.get("applicable_gender"),
                applicable_age_min=y.get("applicable_age_min"),
                applicable_age_max=y.get("applicable_age_max"),
                applicable_diag_codes=tuple(y.get("applicable_diag_codes") or []),
                applicable_departments=tuple(y.get("applicable_departments") or []),
            )

        # Phase 2 Track A 用 — route() 不调
        self._entries: list[dict] = violation_dict["entries"]
        self._keyword_index: dict[str, list[int]] = violation_dict["keyword_index"]
        self._kw_sorted = sorted(self._keyword_index.keys(), key=lambda s: -len(s))

    # ────────────────────────── 构造器辅助 ──────────────────────────

    @classmethod
    def from_defaults(cls, **kwargs) -> "RuleRouter":
        return cls.from_paths(**DEFAULT_PATHS, **kwargs)

    @classmethod
    def from_paths(
        cls,
        violation_dict: Path,
        active_rules: Path,
        pruning_rules: Path,
        javert_index: Path,
        rule_mapping: Path,
        **kwargs,
    ) -> "RuleRouter":
        def _load(p: Path) -> dict:
            return json.loads(p.read_text(encoding="utf-8"))
        return cls(
            violation_dict=_load(violation_dict),
            active_rules=_load(active_rules),
            pruning_rules=_load(pruning_rules),
            javert_index=_load(javert_index),
            rule_mapping=_load(rule_mapping),
            **kwargs,
        )

    # ────────────────────────── 主流程 (single-gate) ──────────────────────────

    def route(self, record: PatientRecord) -> RouterDecision:
        """对单个病案返回 RouterDecision (single-gate v2).

        三步 prune, 每一步都不过即跳:
          1) status / priority
          2) applicable_* metadata (yaml 缺省即不限制)
          3) yaml.trigger_keywords 弹性命中

        Java engine Track A 走独立模块, 不在此处.
        """
        kept: list[str] = []
        pruned_status: list[str] = []
        pruned_applicable: list[str] = []
        pruned_no_keyword: list[str] = []

        # patient fee + diag 文本合一, 加速 keyword 命中
        haystack = self._build_haystack(record)

        for rid, meta in self._yaml_meta.items():
            if not self._passes_status_priority(meta):
                pruned_status.append(rid)
                continue
            if not self._yaml_applicable_to_patient(meta, record):
                pruned_applicable.append(rid)
                continue
            if not self._yaml_keyword_hit(meta, haystack):
                pruned_no_keyword.append(rid)
                continue
            kept.append(rid)

        final_rules = self._rank(kept)
        all_pruned = pruned_status + pruned_applicable + pruned_no_keyword

        stats = {
            "total_yaml_scanned":    len(self._yaml_meta),
            "passed_status_priority": len(self._yaml_meta) - len(pruned_status),
            "passed_applicable":      len(self._yaml_meta) - len(pruned_status) - len(pruned_applicable),
            "kept":                   len(kept),
            "pruned_status":          len(pruned_status),
            "pruned_applicable":      len(pruned_applicable),
            "pruned_no_keyword":      len(pruned_no_keyword),
            "final":                  len(final_rules),
            # Phase 2 Track A — 留 0 占位; Java engine track 独立填充
            "java_rules_triggered":   0,
            "java_entries_matched":   0,
        }

        return RouterDecision(
            patient_id=record.patient_id,
            final_rules=final_rules,
            # legacy 字段: single-gate 下二者意义合并, overlapping_kept 已废
            overlapping_kept=[],
            javert_only_kept=final_rules,
            pruned_out=all_pruned,
            java_triggered={},   # Phase 2 Track A 用
            stats=stats,
        )

    # ────────────────────────── prune helpers ──────────────────────────

    def _passes_status_priority(self, meta: _JavertMeta) -> bool:
        if meta.status not in self.enabled_statuses:
            return False
        if meta.priority and meta.priority not in self.enabled_priorities:
            return False
        return True

    def _yaml_applicable_to_patient(
        self, meta: _JavertMeta, record: PatientRecord,
    ) -> bool:
        """检查 yaml 的 applicable_* 字段是否与患者匹配.

        全部 optional; yaml 没写就当不限制. 这是给 yaml 撰写者一个"硬过滤"
        的能力, 比 trigger_keywords 单凭模糊匹配更精准.
        """
        # 1) visit_type — yaml 指定了 ipt/opt 就必须匹配
        if meta.applicable_visit_type and record.visit_type:
            if record.visit_type not in meta.applicable_visit_type:
                return False

        # 2) gender
        if meta.applicable_gender and record.gender:
            if meta.applicable_gender != record.gender:
                return False

        # 3) age range
        if meta.applicable_age_min is not None and record.age is not None:
            if record.age < meta.applicable_age_min:
                return False
        if meta.applicable_age_max is not None and record.age is not None:
            if record.age > meta.applicable_age_max:
                return False

        # 4) diagnosis codes (ICD 前缀匹配, 支持 'C73' 命中 'C73.x00')
        if meta.applicable_diag_codes and record.diagnosis_codes:
            ok = False
            for prefix in meta.applicable_diag_codes:
                # 支持 * 通配 (e.g. "C73*" 等价于 "C73")
                pure = prefix.rstrip("*").upper()
                for code in record.diagnosis_codes:
                    if code.upper().startswith(pure):
                        ok = True
                        break
                if ok:
                    break
            if not ok:
                return False

        # 5) departments
        if meta.applicable_departments and record.department:
            if record.department not in meta.applicable_departments:
                return False

        return True

    def _build_haystack(self, record: PatientRecord) -> str:
        """把 fee_names + diagnoses 拼成一个字符串, 加速 substring 检索."""
        return " ".join(record.fee_item_names() + record.diagnoses)

    def _yaml_keyword_hit(
        self, meta: _JavertMeta, haystack: str,
    ) -> bool:
        """yaml.trigger_keywords 至少 1 个弹性命中 patient haystack.

        弹性策略:
          - kw 长度 ≤ keyword_short_threshold (默认 2): 精确包含
          - kw 长度 ≥ 3: 先全包含; 不命中再取 60% prefix 试 (e.g. "病理检查" → "病理")
          - 空 trigger_keywords: 视为命中 (yaml 写得不全, 保守保留)
        """
        if not meta.trigger_keywords:
            return True
        for kw in meta.trigger_keywords:
            if self._kw_match(kw, haystack):
                return True
        return False

    def _kw_match(self, kw: str, text: str) -> bool:
        if not kw:
            return False
        kw = kw.strip()
        if not kw:
            return False
        if len(kw) <= self.keyword_short_threshold:
            return kw in text
        if kw in text:
            return True
        # 弹性: 前 60% prefix (>= 2 字)
        prefix_len = max(2, int(round(len(kw) * self.keyword_prefix_ratio)))
        prefix = kw[:prefix_len]
        return prefix in text

    def _rank(self, rule_ids: list[str]) -> list[str]:
        """按 priority (P0 优先) + rule_id 字典序."""
        prio_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, None: 9}
        return sorted(
            set(rule_ids),
            key=lambda rid: (
                prio_order.get(self._yaml_meta[rid].priority, 9),
                rid,
            ),
        )

    # ────────────────────────── Phase 2: Track A 入口 (route() 不调) ──────────────────────────

    def java_engine_lookup(
        self, record: PatientRecord,
    ) -> dict[str, list[TriggerEvidence]]:
        """Phase 2: 跑 14372 字典 lookup 产 java_violations.

        这是 Java engine Track A 的 Python 入口; 跟 Javert yaml prune 完全解耦.
        当前 route() 不调; 留给 javert/java_engine/ 模块在 Phase 2 中调用.
        """
        return self._deterministic_lookup(record)

    def _deterministic_lookup(
        self, record: PatientRecord,
    ) -> dict[str, list[TriggerEvidence]]:
        """对患者 fee_items 在 14372 keyword_index 里查, 通过 pruning_hints 过滤.

        Phase 2 Java engine simulator 会复用此方法做 RULE2/3/4/5/7/9/15/17/19/35/36
        的字典命中检测. 当前在 Phase 1 (router B 单闸) 中不被 route() 调用.
        """
        triggered: dict[str, list[TriggerEvidence]] = defaultdict(list)
        seen_entries: set[int] = set()

        fee_pairs: list[tuple[str, FeeItem]] = [
            (f.medins_list_name, f) for f in record.fee_items if f.medins_list_name
        ]

        for kw in self._kw_sorted:
            entry_idxs = self._keyword_index.get(kw, [])
            if not entry_idxs:
                continue
            hit_fees = [f for name, f in fee_pairs if kw in name]
            if not hit_fees:
                continue
            for idx in entry_idxs:
                if idx in seen_entries:
                    continue
                entry = self._entries[idx]
                if not self._passes_entry_hints(entry, record):
                    continue
                jr = entry["java_rule_no"]
                if jr == "RULE36":
                    hit_fees_jr = [f for f in hit_fees if f.has_insurance_coverage()]
                    if not hit_fees_jr:
                        continue
                else:
                    hit_fees_jr = hit_fees
                for f in hit_fees_jr:
                    triggered[jr].append(TriggerEvidence(
                        entry_idx=idx,
                        java_rule_no=jr,
                        category=entry["category"],
                        matched_keyword=kw,
                        matched_fee_name=f.medins_list_name,
                        matched_item_sn=f.item_sn,
                        warn_msg=entry["warn_msg"],
                    ))
                seen_entries.add(idx)

        return dict(triggered)

    def _passes_entry_hints(self, entry: dict, record: PatientRecord) -> bool:
        """14372 字典条目 pruning_hints 过滤 (Phase 2 用)."""
        hints = entry.get("pruning_hints") or {}

        rg = hints.get("require_gender")
        if rg and record.gender and rg != record.gender:
            return False

        ma = hints.get("max_age")
        if ma is not None and record.age is not None and record.age > ma:
            return False

        rv = hints.get("require_visit_type")
        if rv and record.visit_type and rv != record.visit_type:
            return False

        mh = hints.get("min_hospital_level")
        if mh is not None and record.hospital_level is not None and record.hospital_level >= mh:
            return False

        return True
