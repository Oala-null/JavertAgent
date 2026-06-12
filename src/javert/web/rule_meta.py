# -*- coding: utf-8 -*-
"""Rule metadata 缓存 — 进程内 lazy load, 给前端展示 domain/violation_type/priority/template."""

from __future__ import annotations

import logging
from typing import TypedDict

from javert.audit.rule_loader import load_all
from javert.config import get_config

logger = logging.getLogger("javert.web.rule_meta")


class RuleMeta(TypedDict):
    rule_id: str
    domain: str
    violation_type: str
    priority: str
    template: str | None  # M1..M7 / None
    question: str
    subtitle: str  # 临床检验.过度检查.P0.模板M2
    drug_rule_type: str | None  # M8 药品规则: 限适应症/超说明书/限二线/禁忌症; 否则 None


_CACHE: dict[str, RuleMeta] | None = None


def _build_subtitle(domain: str, violation_type: str, priority: str, template: str | None) -> str:
    parts = [domain, violation_type, priority]
    if template:
        parts.append(f"模板{template}")
    return ".".join(p for p in parts if p)


# violation_type (细类) → 短别名 (D5): 顶部 chip + 正文组标题用, 整句压成短词.
# 覆盖全部已装载细类 (22 个); 未登记的走 violation_alias 兜底截断.
_VIOLATION_ALIAS: dict[str, str] = {
    "重复收费": "重复收费",
    "分解收费": "分解收费",
    "超标准收费": "超标收费",
    "串换项目": "串换项目",
    "过度检查": "过度检查",
    "过度诊疗": "过度诊疗",
    "超医保限定支付适应症用药": "超限用药",
    "超药品说明书适应症用药": "超说明书",
    "超医保限定二线用药": "超限二线",
    "用药安全/禁忌": "用药禁忌",
    "虚构医药服务项目或以骗保为目的串换项目": "虚构/串换",
    "虚构医药服务项目": "虚构项目",
    "虚构医药服务": "虚构服务",
    "虚构诊疗": "虚构诊疗",
    "虚构病情": "虚构病情",
    "虚假诊断": "虚假诊断",
    "虚假住院": "虚假住院",
    "诱导住院骗取医保基金": "诱导住院",
    "将不属于医保支付范围的纳入医保基金结算": "超范围结算",
    "DRG/DIP高编高套": "高编高套",
    "虚构医药服务项目或过度诊疗": "虚构/过度",
    "过度诊疗或虚构医药服务项目": "过度/虚构",
}


def violation_alias(violation_type: str | None) -> str:
    """violation_type → 短别名 (chip / 组标题). 未登记的整句压成前 6 字 + …; 空 → 未分类."""
    vt = (violation_type or "").strip()
    if not vt:
        return "未分类"
    alias = _VIOLATION_ALIAS.get(vt)
    if alias:
        return alias
    return vt if len(vt) <= 6 else vt[:6] + "…"


def load_rule_meta() -> dict[str, RuleMeta]:
    """全量 yaml → dict[rule_id, RuleMeta]. 第一次调用扫盘 (~111 条 yaml, ~50ms)."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    cfg = get_config()
    out: dict[str, RuleMeta] = {}
    try:
        rules = load_all(cfg.rules_path)
        for rid, rule in rules.items():
            out[rid] = RuleMeta(
                rule_id=rid,
                domain=rule.domain,
                violation_type=rule.violation_type,
                priority=rule.priority,
                template=rule.derived_from_template,
                question=rule.question,
                subtitle=_build_subtitle(
                    rule.domain, rule.violation_type,
                    rule.priority, rule.derived_from_template,
                ),
                drug_rule_type=rule.drug_rule_type,
            )
        logger.info("rule_meta cache: %d rules loaded", len(out))
    except Exception as e:
        logger.warning("rule_meta load 失败: %s", e)
    _CACHE = out
    return out


def get_rule_meta(rule_id: str) -> RuleMeta | None:
    return load_rule_meta().get(rule_id)


def reset_cache() -> None:
    global _CACHE
    _CACHE = None
