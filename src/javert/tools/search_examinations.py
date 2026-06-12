# -*- coding: utf-8 -*-
"""search_examinations — 检查报告检索工具 (v0.7+).

包 `ExaminationLoader` 暴露给 LLM. 解决跨文书证据 / 检查指征核实问题:
- R131 心彩超指征 → check_type="心超"
- R155 胸部 CT 指征 → keyword="胸部" or check_type="放射"
- R141/R146 病情评估检查 → 无参数,看全表
- R203 麻醉方法可能记在术后病程而非麻醉记录 → keyword="麻醉"
"""

from __future__ import annotations

from typing import Callable

from javert.data.examination_loader import ExaminationLoader

REQUIRES_PATIENT_ID = True

DESCRIPTION = (
    "检索患者检查报告 (CT/MRI/超声/心电图/内镜/肺功能/电生理/放射等). "
    "无 check_type/keyword 时返回该患者检查清单 (摘要); "
    "传 check_type 按类型筛 (例: '心超'/'放射'/'电生理'/'磁共振'); "
    "传 keyword 在 checkItemName+checkConclusion+checkDescribe+checkPosition 全文模糊搜. "
    "适用于核实检查指征 (R131 心彩超 / R155 胸部 CT / R141 病情评估) 或跨文书证据."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号 (J/Kxxxxx)"},
        "check_type": {
            "type": "string",
            "description": "可选,checkType 子串匹配 (放射/心超/电生理/磁共振/超声/...)",
        },
        "keyword": {
            "type": "string",
            "description": "可选,在检查项名/结论/描述/部位 任一命中即返回",
        },
    },
    "required": ["patient_id"],
}


# 单条记录最多 350 字符 detail (含结论 + 描述前 N 字),控制 LLM context
_MAX_DETAIL_CHARS = 350
# 最多返回 12 条
_MAX_ROWS = 12


def _format_row(idx: int, row: dict) -> str:
    date = row.get("reportDate") or row.get("checkDate") or "未知时间"
    ctype = row.get("checkType") or ""
    item = row.get("checkItemName") or ""
    position = row.get("checkPosition") or ""
    conclusion = (row.get("checkConclusion") or "").strip()
    describe = (row.get("checkDescribe") or "").strip()
    reporter = row.get("reporter") or ""
    is_pos = row.get("isPos") or ""

    head_parts = [date, ctype]
    if item:
        head_parts.append(item)
    if position:
        head_parts.append(f"({position})")
    head = " · ".join([p for p in head_parts if p])

    detail = ""
    if conclusion:
        detail = conclusion
    if describe:
        glue = " | 描述: " if detail else "描述: "
        detail = detail + glue + describe
    if len(detail) > _MAX_DETAIL_CHARS:
        detail = detail[:_MAX_DETAIL_CHARS] + "..."

    foot = []
    if reporter:
        foot.append(f"报告: {reporter}")
    if is_pos in ("1", "True", "true"):
        foot.append("阳性")
    foot_line = " · ".join(foot)

    lines = [f"[{idx}] {head}"]
    if detail:
        lines.append(f"    {detail}")
    if foot_line:
        lines.append(f"    {foot_line}")
    return "\n".join(lines)


def create_executor(loader: ExaminationLoader) -> Callable[..., str]:
    """绑定 ExaminationLoader, 返回 search_examinations(patient_id, ...) 函数."""

    def execute(
        patient_id: str,
        check_type: str | None = None,
        keyword: str | None = None,
        **_kwargs,
    ) -> str:
        all_rows = loader.get_examinations(patient_id)
        if not all_rows:
            return f"该患者无检查报告记录 (sy_patient_examination 索引中无 zyh={patient_id})"

        filtered = loader.get_examinations(
            patient_id, check_type=check_type, keyword=keyword,
        )
        filter_desc_parts = []
        if check_type:
            filter_desc_parts.append(f"check_type={check_type!r}")
        if keyword:
            filter_desc_parts.append(f"keyword={keyword!r}")
        filter_desc = ", ".join(filter_desc_parts) if filter_desc_parts else "无筛选"

        if not filtered:
            # 列出该患者的 checkType 分布,方便 LLM 调整参数
            type_counts: dict[str, int] = {}
            for r in all_rows:
                t = r.get("checkType") or "未分类"
                type_counts[t] = type_counts.get(t, 0) + 1
            ts = ", ".join(f"{k}({v}条)" for k, v in sorted(type_counts.items(), key=lambda x: -x[1]))
            return (
                f"该患者共 {len(all_rows)} 条检查报告,但 {filter_desc} 无匹配.\n"
                f"该患者的 checkType 分布: {ts}\n"
                "提示: 可调整 check_type / keyword 后重试,或不传参数看全表."
            )

        header = (
            f"检查报告 (共 {len(all_rows)} 条; {filter_desc} 后保留 {len(filtered)} 条; "
            f"按 reportDate 升序):"
        )
        body_lines = []
        for i, row in enumerate(filtered[:_MAX_ROWS], 1):
            body_lines.append(_format_row(i, row))
            body_lines.append("")
        if len(filtered) > _MAX_ROWS:
            body_lines.append(
                f"... 共 {len(filtered)} 条匹配,已显示前 {_MAX_ROWS} 条. 可缩小筛选."
            )
        return header + "\n\n" + "\n".join(body_lines).rstrip()

    return execute
