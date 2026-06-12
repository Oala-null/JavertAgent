# -*- coding: utf-8 -*-
"""drug_indication — 药品适应症 + ICD 候选 (本地映射表查找).

Source: zadig_agent/src/tools/drug_indication.py + skills/drug_indication.py
        (snapshot @ 2026-05-08)
改动: 移除联网搜索回退 (Javert 不依赖 detective); 映射表路径改为 javert configs.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("javert.tools.drug_indication")

REQUIRES_PATIENT_ID = False

DESCRIPTION = "查询药品的适应症和 ICD 候选 (本地映射表 51 种药品 + 排除项)."

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "drug_name": {"type": "string", "description": "药品名称"},
    },
    "required": ["drug_name"],
}

_drug_map_cache: dict | None = None


def _load_drug_map(map_path: Path) -> dict:
    global _drug_map_cache
    if _drug_map_cache is not None:
        return _drug_map_cache
    if not map_path.exists():
        logger.warning("drug_indication_map.json 不存在: %s", map_path)
        _drug_map_cache = {"drugs": [], "exclusions": []}
        return _drug_map_cache
    with open(map_path, encoding="utf-8") as f:
        _drug_map_cache = json.load(f)
    logger.info(
        "药品映射表 v%s: %d 种药品",
        _drug_map_cache.get("version", "?"),
        len(_drug_map_cache.get("drugs", [])),
    )
    return _drug_map_cache


def lookup_drug(drug_name: str, map_path: Path) -> dict[str, Any]:
    drug_map = _load_drug_map(map_path)
    for excl in drug_map.get("exclusions", []):
        if re.search(excl["pattern"], drug_name, re.IGNORECASE):
            return {
                "found": True,
                "source": "exclusion",
                "drug_name": drug_name,
                "tier": None,
                "indication": "",
                "icd_candidates": [],
                "generic_name": "",
                "is_exclusion": True,
                "exclusion_reason": excl.get("reason", ""),
            }
    for entry in drug_map.get("drugs", []):
        if re.search(entry["pattern"], drug_name, re.IGNORECASE):
            return {
                "found": True,
                "source": "local_map",
                "drug_name": drug_name,
                "tier": entry.get("tier"),
                "indication": entry.get("indication", ""),
                "icd_candidates": entry.get("icd_candidates", []),
                "generic_name": entry.get("generic_name", ""),
                "is_exclusion": False,
                "exclusion_reason": "",
            }
    return {
        "found": False,
        "source": "not_found",
        "drug_name": drug_name,
        "tier": None,
        "indication": "",
        "icd_candidates": [],
        "generic_name": "",
        "is_exclusion": False,
        "exclusion_reason": "",
        "note": "本地映射未命中, Javert pilot 阶段无联网回退",
    }


def format_for_agent(result: dict[str, Any]) -> str:
    if result.get("is_exclusion"):
        return f"药品「{result['drug_name']}」为非诊断性药品 ({result['exclusion_reason']}), 不触发适应症调查."
    if not result["found"]:
        return f"药品「{result['drug_name']}」未在本地映射表找到. {result.get('note', '')}"
    lines = [f"药品「{result['drug_name']}」适应症查询结果:"]
    lines.append(f"  来源: {result['source']}")
    if result["generic_name"]:
        lines.append(f"  通用名: {result['generic_name']}")
    if result["tier"]:
        lines.append(f"  信号强度: Tier {result['tier']}")
    if result["indication"]:
        lines.append(f"  适应症: {result['indication']}")
    if result["icd_candidates"]:
        lines.append(f"  ICD 候选: {', '.join(result['icd_candidates'])}")
    return "\n".join(lines)


def create_executor(map_path: Path) -> Callable[..., str]:
    """绑定 drug_indication_map.json 路径, 返回 drug_indication(drug_name) 函数."""

    def execute(drug_name: str, **_kwargs) -> str:
        result = lookup_drug(drug_name, map_path)
        return format_for_agent(result)

    return execute
