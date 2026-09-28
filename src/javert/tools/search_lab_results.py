# -*- coding: utf-8 -*-
"""search_lab_results — 检验/化验报告检索工具 (v0.7+).

包 `LabLoader` 暴露给 LLM. 解决检验指征核实问题:
- R153 AFP/CEA 等肿瘤标志物指征 → item_keyword="AFP" 或 "肿瘤标志物"
- R278 肾上腺垂体激素指征 → item_keyword="皮质醇" / "ACTH"
- R155 检查指征 → 任意 item 模糊搜
- 异常聚焦 → abnormal_only=True
"""

from __future__ import annotations

from typing import Callable

from javert.data.lab_loader import LabLoader
from javert.data.loader import DataLoader
from javert.tools import search_notes

REQUIRES_PATIENT_ID = True

DESCRIPTION = (
    "检索患者检验/化验报告 (血常规/肿瘤标志物/激素/凝血/感染指标/甲功/电解质等). "
    "无 item_keyword 时返回全部检验项 (按 report_dt 升序); "
    "传 item_keyword 按检验名搜 — 同时命中 rpt_itemname (中文如'甲胎蛋白'/'甲状腺球蛋白') "
    "和 rpt_itemcode (英文如 AFP/TSH/CEA),大小写不敏感; "
    "传 abnormal_only=true 仅返回 result_flag 异常项 (↑/↓/N/阳性 等). "
    "适用于核实检验指征 (R153 AFP / R278 肾上腺激素 / R155 检查指征) 或异常聚焦."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号 (J/Kxxxxx)"},
        "item_keyword": {
            "type": "string",
            "description": (
                "可选,在 rpt_itemname (中文项名) / rpt_itemcode (英文如 AFP/TSH) "
                "/ inspectionName 三列任一命中即返回. 大小写不敏感."
            ),
        },
        "abnormal_only": {
            "type": "boolean",
            "description": "可选,仅返回 result_flag 非'正常'/'N'的行 (默认 false)",
        },
    },
    "required": ["patient_id"],
}


_MAX_ROWS = 15  # 检验项往往多,放宽一点


def _format_row(idx: int, row: dict) -> str:
    date = row.get("report_dt") or "未知时间"
    item = row.get("rpt_itemname") or ""
    inspection = row.get("inspectionName") or ""
    result = row.get("result") or ""
    unit = row.get("result_unit") or ""
    ref = row.get("result_ref") or ""
    flag = row.get("result_flag") or ""
    diag_op = row.get("diagnosisOpinion") or ""
    dept = row.get("department") or ""
    specimen = row.get("specimen") or ""

    head = f"{date} | {item}"
    if inspection and inspection not in item:
        head += f" · {inspection}"

    value_parts = [f"结果: {result}"]
    if unit:
        value_parts[-1] += f" {unit}"
    if ref:
        value_parts.append(f"参考: {ref}")
    if flag:
        value_parts.append(f"标志: {flag}")
    value_line = "  ".join(value_parts)

    extra_parts = []
    if diag_op:
        extra_parts.append(f"临床诊断: {diag_op}")
    if dept:
        extra_parts.append(f"科室: {dept}")
    if specimen:
        extra_parts.append(f"样本: {specimen}")
    extra_line = " | ".join(extra_parts)

    lines = [f"[{idx}] {head}", f"    {value_line}"]
    if extra_line:
        lines.append(f"    {extra_line}")
    return "\n".join(lines)


def create_executor(loader: LabLoader, notes_loader: DataLoader | None = None) -> Callable[..., str]:
    """绑定 LabLoader, 返回 search_lab_results(patient_id, ...) 函数."""

    def execute(
        patient_id: str,
        item_keyword: str | None = None,
        abnormal_only: bool = False,
        **_kwargs,
    ) -> str:
        all_rows = loader.get_lab_results(patient_id)
        if not all_rows:
            if notes_loader is not None:
                query = item_keyword or "检验报告"
                note_result = search_notes.create_executor(notes_loader)(patient_id, keyword=query)
                if "未找到" not in note_result and "无文书记录" not in note_result:
                    return (
                        "非结构化报告候选（病历全文，不能替代结构化检验报告表）:\n"
                        + note_result
                    )
            return f"该患者无检验/化验报告记录 (sy_检验 索引中无 zyh={patient_id})"

        filtered = loader.get_lab_results(
            patient_id, item_keyword=item_keyword, abnormal_only=abnormal_only,
        )
        filter_desc_parts = []
        if item_keyword:
            filter_desc_parts.append(f"item_keyword={item_keyword!r}")
        if abnormal_only:
            filter_desc_parts.append("abnormal_only=true")
        filter_desc = ", ".join(filter_desc_parts) if filter_desc_parts else "无筛选"

        if not filtered:
            # 列出该患者 inspectionName 分布,方便 LLM 调整
            insp_counts: dict[str, int] = {}
            for r in all_rows:
                k = r.get("inspectionName") or "未分类"
                insp_counts[k] = insp_counts.get(k, 0) + 1
            top = sorted(insp_counts.items(), key=lambda x: -x[1])[:8]
            ts = ", ".join(f"{k}({v})" for k, v in top)
            return (
                f"该患者共 {len(all_rows)} 条检验,但 {filter_desc} 无匹配.\n"
                f"该患者检验类别 top: {ts}\n"
                "提示: 可调整 item_keyword 或去掉 abnormal_only 后重试."
            )

        # v0.9 — 跨天统计 (帮 LLM 判定 "反复检测 ≥3 天" 类 V 触发器)
        from collections import defaultdict
        item_days: dict = defaultdict(set)
        for r in filtered:
            item = (r.get("rpt_itemname") or "?").strip()
            date = (r.get("report_dt") or "")[:10]
            if item and date:
                item_days[item].add(date)
        stats_lines = []
        for item, days in sorted(item_days.items(), key=lambda x: -len(x[1])):
            if len(days) >= 2:
                sd = sorted(days)
                stats_lines.append(f"  - 「{item}」: 共 {len(days)} 天 ({sd[0]} → {sd[-1]})")
        stats_block = ""
        if stats_lines:
            stats_block = (
                "\n📊 反复检测统计 (用于 R141/R143/R146/R155/R160/R161 等"
                " '连续/反复检测 ≥3 天' V 触发器判定):\n"
                + "\n".join(stats_lines) + "\n"
            )

        header = (
            f"检验/化验报告 (共 {len(all_rows)} 条; {filter_desc} 后保留 {len(filtered)} 条; "
            f"按 report_dt 升序):"
            + stats_block
        )
        body_lines = []
        for i, row in enumerate(filtered[:_MAX_ROWS], 1):
            body_lines.append(_format_row(i, row))
            body_lines.append("")
        if len(filtered) > _MAX_ROWS:
            body_lines.append(
                f"... 共 {len(filtered)} 条匹配,已显示前 {_MAX_ROWS} 条. 可加 item_keyword 缩小."
            )
        return header + "\n\n" + "\n".join(body_lines).rstrip()

    return execute
