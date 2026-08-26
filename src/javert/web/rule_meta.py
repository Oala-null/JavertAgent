# -*- coding: utf-8 -*-
"""Rule metadata 缓存 — 进程内 lazy load, 给前端展示规则静态元数据.

行为认定名称 (configs/behavior_names.yaml, 源自 260611医保基金监管规则框架总表.xlsx):
对外展示 (2C 出参 + 工作台 chip/组标题) 统一用 behavior_name, 不再露 M 模板名/R 代号.
"""

from __future__ import annotations

import logging
from typing import TypedDict

import yaml

from javert.audit.rule_loader import load_all
from javert.config import get_config

logger = logging.getLogger("javert.web.rule_meta")


class RuleMeta(TypedDict):
    rule_id: str
    domain: str
    violation_type: str
    priority: str
    handling_level: str
    template: str | None  # M1..M7 / None
    question: str
    subtitle: str  # 临床检验.过度检查.P0.模板M2
    drug_rule_type: str | None  # M8 药品规则: 限适应症/超说明书/限二线/禁忌症; 否则 None
    behavior_name: str  # 行为认定名称 (对外展示口径; 未登记统一为“未分类”)
    behavior_code: str  # 行为认定编码 (如 T380301; 未登记为 "")
    behavior_exception_key: str


_CACHE: dict[str, RuleMeta] | None = None
_BEHAVIOR_CACHE: dict[str, dict] | None = None
_BEHAVIOR_REL = "configs/behavior_names.yaml"


def load_behavior_map() -> dict[str, dict]:
    """behavior_names.yaml → {violation_type: {code, name}}；缺文件/损坏时 fail closed。"""
    global _BEHAVIOR_CACHE
    if _BEHAVIOR_CACHE is not None:
        return _BEHAVIOR_CACHE
    out: dict[str, dict] = {}
    try:
        path = get_config().resolve(_BEHAVIOR_REL)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = data.get("mappings") or {}
        for vt, entry in raw.items():
            if isinstance(entry, dict) and entry.get("name"):
                out[str(vt).strip()] = {
                    "name": str(entry["name"]).strip(),
                    "code": str(entry.get("code") or "").strip(),
                    "exception_key": str(entry.get("exception_key") or "").strip(),
                }
    except Exception as e:  # noqa: BLE001
        logger.warning("behavior_names load 失败 (公开类别回退未分类): %s", e)
    _BEHAVIOR_CACHE = out
    return out


def behavior_name(violation_type: str | None) -> str:
    """violation_type → 行为认定名称；未登记或为空均 fail closed 为“未分类”。"""
    vt = (violation_type or "").strip()
    if not vt:
        return "未分类"
    entry = load_behavior_map().get(vt)
    return entry["name"] if entry else "未分类"


def behavior_code(violation_type: str | None) -> str:
    vt = (violation_type or "").strip()
    entry = load_behavior_map().get(vt)
    return entry["code"] if entry else ""


def _build_subtitle(domain: str, violation_type: str, priority: str, template: str | None) -> str:
    parts = [domain, violation_type, priority]
    if template:
        parts.append(f"模板{template}")
    return ".".join(p for p in parts if p)


def violation_alias(violation_type: str | None) -> str:
    """violation_type → 展示名 (chip / 组标题) = 行为认定名称; 超长压 8 字 + ….

    (behavior-naming) 原 22 词短别名表退役 — 展示口径统一到 behavior_names.yaml,
    改 yaml 即全站生效, 不再维护两套词.
    """
    name = behavior_name(violation_type)
    return name if len(name) <= 10 else name[:8] + "…"


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
                handling_level=rule.handling_level,
                template=rule.derived_from_template,
                question=rule.question,
                subtitle=_build_subtitle(
                    rule.domain, rule.violation_type,
                    rule.priority, rule.derived_from_template,
                ),
                drug_rule_type=rule.drug_rule_type,
                behavior_name=behavior_name(rule.violation_type),
                behavior_code=behavior_code(rule.violation_type),
                behavior_exception_key=(
                    load_behavior_map().get(rule.violation_type, {}).get("exception_key", "")
                ),
            )
        logger.info("rule_meta cache: %d rules loaded", len(out))
    except Exception as e:
        logger.warning("rule_meta load 失败: %s", e)
    _CACHE = out
    return out


def get_rule_meta(rule_id: str) -> RuleMeta | None:
    return load_rule_meta().get(rule_id)


def reset_cache() -> None:
    global _CACHE, _BEHAVIOR_CACHE
    _CACHE = None
    _BEHAVIOR_CACHE = None
