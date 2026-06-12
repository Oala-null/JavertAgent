# -*- coding: utf-8 -*-
"""scan_progress_indications — 病程/查房 section 族症状指征扫描 (确定性, 无 LLM).

M2 过度检查流程历来只读 `note_diagnosis`, 而真正的指征症状 (胸闷/气短/喘…) 散在
`病程记录内容`/`病情及处理`/`诊疗经过` 等病程类 section (ETL 把文书劈碎, 无干净
「日常查房记录」section). 本工具扫遍 **病程/查房 section 族** (由 verdict_gate.yaml
`progress_sections` 配置) 找症状关键词, 返回每个命中的 `{子阶段, char_start, 摘录,
否认段?, 选项框?}`, 复用 search_notes 的否认段/选项框标注逻辑.

命中即「有指征」→ M2 判 CLEAN; 命中带 [否认段]/[选项框] 标注时提示非阳性, 需谨慎.

Source: 本项目原创 (add-verdict-gate-layer).
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from javert.data.loader import DataLoader
from javert.tools.search_notes import (
    _annotate_keyword_hit,
    _filter_patient,
    _find_col,
)

REQUIRES_PATIENT_ID = True

DESCRIPTION = (
    "扫描患者病程/查房 section 族 (病程记录内容/病情及处理/诊疗经过/目前情况/入院情况/"
    "简要病情/首次病程 等) 内的症状关键词, 返回每个命中的子阶段 + char 偏移 + ±80 字摘录, "
    "并标注 [否认段] (前导有 否认/未见/排除) / [选项框] (邻近有 □). "
    "用于 M2 过度检查判「无指征」前强制核对病程是否真有症状指征 (命中具体症状 → 有指征 → CLEAN). "
    "确定性工具, 不依赖『日常查房记录』section 存在."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号"},
        "symptom_kw_list": {
            "type": "array",
            "items": {"type": "string"},
            "description": "症状关键词列表 (例: ['胸闷','气短','喘','憋','胸痛','下肢水肿'])",
        },
    },
    "required": ["patient_id", "symptom_kw_list"],
}


def _load_progress_sections() -> list[str]:
    """从 verdict_gate.yaml 取病程/查房 section 族 (配置缺失 → 实测 6 段兜底)."""
    try:
        from javert.audit.verdict_gate import get_gate_config

        secs = get_gate_config().progress_sections
        if secs:
            return list(secs)
    except Exception:  # noqa: BLE001
        pass
    return [
        "病程记录内容", "病情及处理", "诊疗经过", "目前情况",
        "入院情况", "简要病情", "首次病程",
    ]


def _section_in_family(section_name: str, family: list[str]) -> bool:
    """子阶段名是否属病程/查房族 (substring 包含, 容纳 ETL 命名变体)."""
    for fam in family:
        if fam in section_name or section_name in fam:
            return True
    return False


def create_executor(loader: DataLoader) -> Callable[..., str]:
    """绑定 DataLoader, 返回 scan_progress_indications(patient_id, symptom_kw_list) 函数."""

    def execute(
        patient_id: str,
        symptom_kw_list: list[str] | None = None,
        **_kwargs,
    ) -> str:
        kws = [str(k).strip() for k in (symptom_kw_list or []) if str(k).strip()]
        if not kws:
            return "未提供 symptom_kw_list, 无法扫描 (请传入该检查对应的症状关键词列表)"

        notes_df = loader.all_notes()
        patient_notes = _filter_patient(notes_df, patient_id)
        if patient_notes.empty:
            return "该患者无文书记录"

        section_col = _find_col(patient_notes, ["子阶段", "sub_stage", "section"])
        content_col = _find_col(patient_notes, ["内容", "content", "text"])
        if content_col is None:
            return "文书数据缺少内容列"

        family = _load_progress_sections()

        hits: list[dict] = []  # {section, keyword, char_start, annotation, excerpt}
        scanned_sections: set[str] = set()
        for _, row in patient_notes.iterrows():
            sec_name = str(row[section_col]).strip() if section_col else "未分类"
            if not _section_in_family(sec_name, family):
                continue
            scanned_sections.add(sec_name)
            content = str(row[content_col])
            for kw in kws:
                pos = content.find(kw)
                if pos < 0:
                    continue
                annotation, excerpt = _annotate_keyword_hit(content, kw)
                hits.append({
                    "section": sec_name,
                    "keyword": kw,
                    "char_start": pos,
                    "annotation": annotation,
                    "excerpt": excerpt,
                })

        if not scanned_sections:
            return (
                f"该患者无病程/查房类子阶段 (扫描族: {', '.join(family)}); "
                "病程文书可能未数字化 → 不能据此直接判定无指征, 宜谨慎 (INCONCLUSIVE)"
            )

        if not hits:
            return (
                f"病程/查房 section 族 ({', '.join(sorted(scanned_sections))}) 内"
                f"未命中任何症状关键词 ({'/'.join(kws)}) → 0 指征命中."
            )

        positive = [h for h in hits if not h["annotation"]]
        lines = [
            f"病程/查房症状指征扫描结果 (共 {len(hits)} 处命中, 其中阳性 {len(positive)} 处):",
            "",
        ]
        for i, h in enumerate(hits, 1):
            ann = f" {h['annotation']}" if h["annotation"] else ""
            loc = f" ⟨子阶段={h['section']} char={h['char_start']}⟩"
            lines.append(f"[{i}] 子阶段: {h['section']} | 症状: {h['keyword']}{ann}{loc}")
            lines.append(f"    {h['excerpt']}")
            lines.append("")
        if any(h["annotation"] for h in hits):
            lines.append(
                "⚠️ [否认段] 表示症状前导有 '否认/未见/排除' (反向语义); "
                "[选项框] 表示邻近有 □ 未必激活. 这些命中不构成阳性指征."
            )
        if positive:
            lines.append(
                f"✅ 有 {len(positive)} 处阳性症状指征 (无否认/选项框标注) → 该检查有指征, 应判 CLEAN."
            )
        else:
            lines.append(
                "⚠️ 所有命中均带 [否认段]/[选项框] 标注 → 无真实阳性指征, 仍需结合诊断判断."
            )
        return "\n".join(lines)

    return execute
