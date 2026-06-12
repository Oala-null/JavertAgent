# -*- coding: utf-8 -*-
"""search_pathology — 病理视图工具 (add-visual-schema-onboarding, status=view).

病理无独立物理源表 (设计 D2): 散在 lab specimen=病理 (419 行) + notes 病理子阶段.
本工具聚合二者, 输出 MUST 标注"视图来源·暂不参与判定" (不冒充 live 已覆盖).
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from javert.data.lab_loader import LabLoader
from javert.data.loader import DataLoader

REQUIRES_PATIENT_ID = True

DESCRIPTION = (
    "【视图工具·弱信号】聚合患者病理信息: 检验表 specimen=病理 的记录 "
    "+ case_notes 病理子阶段 (病理诊断/病理结果/病理报告). "
    "病理无独立源表, 本工具为视图聚合, 结果标注'暂不参与判定', 仅供参考不单独支撑裁决."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号"},
    },
    "required": ["patient_id"],
}

_PATH_KEYWORDS = ("病理",)
_VIEW_TAG = "【视图来源·暂不参与判定】"


def _patient_notes(df: pd.DataFrame, patient_id: str) -> pd.DataFrame:
    if "住院号" in df.columns:
        return df[df["住院号"].astype(str).str.strip() == patient_id]
    if "bah" in df.columns:
        return df[df["bah"].astype(str).str.contains(patient_id, na=False)]
    return df[df.iloc[:, 0].astype(str).str.contains(patient_id, na=False)]


def create_executor(loader: DataLoader, lab_loader: LabLoader) -> Callable[..., str]:
    """绑定 DataLoader (文书) + LabLoader (检验), 返回 search_pathology(patient_id)."""

    def execute(patient_id: str, **_kwargs) -> str:
        lines: list[str] = []

        # 1. 检验表 specimen=病理
        try:
            lab_rows = lab_loader.get_lab_results(patient_id)
        except Exception:  # noqa: BLE001
            lab_rows = []
        path_labs = [
            r for r in lab_rows
            if any(k in (r.get("specimen") or "") for k in _PATH_KEYWORDS)
            or any(k in (r.get("rpt_itemname") or "") for k in _PATH_KEYWORDS)
            or any(k in (r.get("inspectionName") or "") for k in _PATH_KEYWORDS)
        ]
        if path_labs:
            lines.append(f"检验表病理记录 (specimen=病理, 共 {len(path_labs)} 条):")
            for i, r in enumerate(path_labs[:5], 1):
                date = r.get("report_dt") or "未知时间"
                item = r.get("rpt_itemname") or ""
                result = r.get("result") or ""
                opinion = r.get("diagnosisOpinion") or ""
                lines.append(f"  [{i}] {date} | {item}")
                detail = f"结果: {result}" + (f" | 诊断意见: {opinion}" if opinion else "")
                lines.append(f"      {detail}")
            if len(path_labs) > 5:
                lines.append(f"  ... 共 {len(path_labs)} 条, 已显示前 5 条")

        # 2. case_notes 病理子阶段
        notes_df = loader.all_notes()
        pn = _patient_notes(notes_df, patient_id)
        if not pn.empty and "内容" in pn.columns:
            sub_col = "子阶段" if "子阶段" in pn.columns else None
            hit_rows: list[tuple[str, str]] = []
            for _, row in pn.iterrows():
                sub = str(row[sub_col]).strip() if sub_col else ""
                if sub.lower() == "nan":
                    sub = ""
                content = str(row["内容"])
                if any(k in sub for k in _PATH_KEYWORDS) or any(k in content for k in _PATH_KEYWORDS):
                    hit_rows.append((sub or "未分类", content))
            if hit_rows:
                lines.append("")
                lines.append(f"文书病理相关子阶段 (共 {len(hit_rows)} 条):")
                for i, (sub, content) in enumerate(hit_rows[:5], 1):
                    excerpt = content[:160] + ("..." if len(content) > 160 else "")
                    lines.append(f"  [{i}] 子阶段: {sub}")
                    lines.append(f"      {excerpt}")
                if len(hit_rows) > 5:
                    lines.append(f"  ... 共 {len(hit_rows)} 条, 已显示前 5 条")

        if not lines:
            return f"{_VIEW_TAG} 该患者无病理相关记录 (检验无 specimen=病理 + 文书无病理子阶段)."
        return f"{_VIEW_TAG}\n" + "\n".join(lines) + (
            "\n\n注: 病理为视图聚合 (无独立源表), 信号弱, 暂不参与判定, 仅供核实参考."
        )

    return execute
