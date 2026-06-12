# -*- coding: utf-8 -*-
"""search_anesthesia — 麻醉视图工具 (add-visual-schema-onboarding, status=view).

麻醉无独立物理源表 (设计 D2): 散在 case_notes 麻醉子阶段 + shi_ss.anst_mtd_name.
本工具聚合二者, 输出 MUST 标注"视图来源·暂不参与判定" (不冒充 live 已覆盖).
"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from typing import Callable

import pandas as pd

from javert.data.loader import DataLoader

logger = logging.getLogger("javert.tools.search_anesthesia")

REQUIRES_PATIENT_ID = True

DESCRIPTION = (
    "聚合患者麻醉信息. 主源=病案首页手术表 (anst_way 麻醉方式码: 1=全身麻醉气管插管/9=其他局部麻醉, "
    "anst_dr_name 麻醉医师签名, oprn_oprt_name 对应手术) — 这是权威记录, 手术表有麻醉医师签名即麻醉真实开展, "
    "可直接支撑'全麻收费真实'(麻醉记录单常仅 ETL 未数字化, 不等于虚构). 辅源=case_notes 麻醉子阶段."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号"},
    },
    "required": ["patient_id"],
}

_ANES_KEYWORDS = ("麻醉",)
# anst_mtd_name 在本数据集全空, 用 anst_way 码解码兜底 (本数据集只出现 1 / 9).
_ANST_WAY_LABEL = {"1": "全身麻醉(气管插管)", "9": "其他/局部麻醉"}

# ss_path → {bare_pid: [ {anesthesia, anst_dr, surgery} ]}
_SS_CACHE: dict[str, dict[str, list[dict]]] = {}
_LOCK = Lock()


def _build_ss_index(ss_path: Path) -> dict[str, list[dict]]:
    key = str(ss_path)
    if key in _SS_CACHE:
        return _SS_CACHE[key]
    with _LOCK:
        if key in _SS_CACHE:
            return _SS_CACHE[key]
        index: dict[str, list[dict]] = {}
        if ss_path.exists():
            try:
                if ss_path.suffix.lower() == ".csv":
                    df = pd.read_csv(ss_path, dtype=str, low_memory=False)
                else:
                    df = pd.read_excel(ss_path, dtype=str)
                df = df.fillna("")
                id_col = next((c for c in ("ba_id", "bah", "住院号") if c in df.columns), None)
                if id_col is not None:
                    for _, row in df.iterrows():
                        raw = str(row[id_col]).strip()
                        if not raw or raw == "nan":
                            continue
                        pid = raw.split("-")[-1].strip()
                        anst = str(row.get("anst_mtd_name", "")).strip()
                        anst_way = str(row.get("anst_way", "")).strip()
                        anst_dr = str(row.get("anst_dr_name", "")).strip()
                        surgery = str(row.get("oprn_oprt_name", "")).strip()
                        # anst_mtd_name 本数据集全空 → 用 anst_way 码解码兜底
                        if not anst and anst_way:
                            anst = _ANST_WAY_LABEL.get(anst_way, f"麻醉方式码{anst_way}")
                        if anst or anst_dr:
                            index.setdefault(pid, []).append(
                                {"anesthesia": anst, "anst_dr": anst_dr,
                                 "surgery": surgery, "anst_way": anst_way}
                            )
            except Exception as e:  # noqa: BLE001
                logger.warning("search_anesthesia 读 shi_ss 失败: %s", e)
        _SS_CACHE[key] = index
        return index


def _patient_notes(df: pd.DataFrame, patient_id: str) -> pd.DataFrame:
    if "住院号" in df.columns:
        return df[df["住院号"].astype(str).str.strip() == patient_id]
    if "bah" in df.columns:
        return df[df["bah"].astype(str).str.contains(patient_id, na=False)]
    return df[df.iloc[:, 0].astype(str).str.contains(patient_id, na=False)]


def create_executor(loader: DataLoader, ss_path: Path) -> Callable[..., str]:
    """绑定 DataLoader + shi_ss 路径, 返回 search_anesthesia(patient_id)."""

    def execute(patient_id: str, **_kwargs) -> str:
        lines: list[str] = []

        # 1. 病案首页手术表麻醉字段 (权威主源)
        ss_index = _build_ss_index(ss_path)
        ss_rows = ss_index.get(patient_id.strip(), [])
        has_authoritative = False
        if ss_rows:
            lines.append("【病案首页手术表·权威记录】麻醉信息:")
            seen: set[tuple] = set()
            for r in ss_rows:
                k = (r["anesthesia"], r["anst_dr"], r["surgery"])
                if k in seen:
                    continue
                seen.add(k)
                if r.get("anst_dr") or r.get("anst_way") == "1":
                    has_authoritative = True
                parts = []
                if r["anesthesia"]:
                    parts.append(f"麻醉方式: {r['anesthesia']}")
                if r["anst_dr"]:
                    parts.append(f"麻醉医师: {r['anst_dr']}")
                if r["surgery"]:
                    parts.append(f"对应手术: {r['surgery']}")
                lines.append("  · " + " | ".join(parts))

        # 2. case_notes 麻醉子阶段
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
                if any(k in sub for k in _ANES_KEYWORDS) or any(k in content for k in _ANES_KEYWORDS):
                    hit_rows.append((sub or "未分类", content))
            if hit_rows:
                lines.append("")
                lines.append(f"文书麻醉相关子阶段 (共 {len(hit_rows)} 条):")
                for i, (sub, content) in enumerate(hit_rows[:5], 1):
                    excerpt = content[:160] + ("..." if len(content) > 160 else "")
                    lines.append(f"  [{i}] 子阶段: {sub}")
                    lines.append(f"      {excerpt}")
                if len(hit_rows) > 5:
                    lines.append(f"  ... 共 {len(hit_rows)} 条, 已显示前 5 条")

        if not lines:
            return (
                "该患者病案首页手术表无麻醉记录 + 文书无麻醉子阶段. "
                "若费用仍有全麻收费且确无任何手术/操作 → 才可能为虚构 (极罕见)."
            )
        if has_authoritative:
            conclusion = (
                "\n\n结论: 病案首页手术表已记录麻醉医师签名/全麻方式 = 麻醉服务真实开展 (权威依据). "
                "据此, 全身麻醉/麻醉相关收费有据, 不应仅因 case_notes 缺『麻醉记录单』而判虚构."
            )
        else:
            conclusion = (
                "\n\n注: 手术表有麻醉相关字段但无麻醉医师签名/全麻码, 信号偏弱, 结合费用与手术综合判断."
            )
        return "\n".join(lines) + conclusion

    return execute


def reset_cache() -> None:
    _SS_CACHE.clear()
