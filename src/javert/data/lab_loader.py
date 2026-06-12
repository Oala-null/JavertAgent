# -*- coding: utf-8 -*-
"""LabLoader — 检验/化验报告数据接入.

源: `sy_检验.csv` (856k 行, 392 MB). 体量大,用 chunksize 流读 + 按 zyh group 累积,
最终缓存到进程级 dict[patient_id, list[record]].

为 v0.7 引入,解决 R153 (AFP/肿瘤标志物) / R278 (肾上腺激素) / R155 (检查指征)
等规则系统性误报 — 专家批注反复指出 "可查检验报告".

公开 API:
    LabLoader(path).get_lab_results(patient_id, item_keyword=None, abnormal_only=False) -> list[dict]
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock

import pandas as pd

logger = logging.getLogger("javert.data.lab_loader")

LAB_USECOLS: tuple[str, ...] = (
    "zyh",
    "rpt_itemname",
    "rpt_itemcode",  # 英文缩写如 TSH/AFP/CEA, 让 LLM 用英文也能命中
    "result",
    "result_unit",
    "result_ref",
    "result_flag",
    "diagnosisOpinion",
    "department",
    "report_dt",
    "specimen",
    "inspectionName",
    "age",
    "sex",
    "trier",
    "auditor",
)

# 异常 flag 集合 — 用于 abnormal_only 过滤
NORMAL_FLAGS: tuple[str, ...] = ("", "正常", "N")


class LabLoader:
    """按住院号索引的检验/化验报告 loader. chunksize 流读,首次访问时构建索引."""

    def __init__(self, path: Path, chunksize: int = 100_000, overlay_dir: Path | None = None):
        """overlay_dir: 仅工作台传 — 把 data_import/lab_results.csv (onboarding 导入患者)
        叠加到 base 检验数据上, 让外部接入病人与 base 演示病人并存 (同 CsvLoader.overlay)。
        审计侧不传 → 只读自己配置的 base 文件。"""
        self.path = path
        self.chunksize = chunksize
        self.overlay_dir = overlay_dir
        self._index: dict[str, list[dict]] | None = None
        self._lock = Lock()

    def _build_index(self) -> dict[str, list[dict]]:
        if self._index is not None:
            return self._index
        with self._lock:
            if self._index is not None:
                return self._index
            t0 = time.perf_counter()
            index: dict[str, list[dict]] = {}
            total = 0
            if self.path.exists():
                for chunk in pd.read_csv(
                    self.path,
                    chunksize=self.chunksize,
                    dtype=str,
                    usecols=[c for c in LAB_USECOLS],
                    low_memory=False,
                ):
                    chunk = chunk.fillna("")
                    chunk["zyh"] = chunk["zyh"].str.strip()
                    for pid, group in chunk.groupby("zyh"):
                        if not pid or pid.lower() == "nan" or pid == "zyh":
                            continue
                        index.setdefault(pid, []).extend(group.to_dict("records"))
                    total += len(chunk)
            else:
                logger.warning("检验数据文件不存在: %s (仅用 overlay)", self.path)
            self._merge_overlay(index)
            self._index = index
            logger.info(
                "LabLoader: indexed %d patients (%d rows base) from %s in %.1fs",
                len(index), total, self.path.name, time.perf_counter() - t0,
            )
        return self._index

    def _merge_overlay(self, index: dict[str, list[dict]]) -> None:
        """叠加 data_import/lab_results.csv (onboarding 导入患者), 与 CsvLoader.overlay 同理."""
        if self.overlay_dir is None:
            return
        p = self.overlay_dir / "lab_results.csv"
        if not p.exists():
            return
        try:
            cols = set(LAB_USECOLS)
            df = pd.read_csv(p, dtype=str, usecols=lambda c: c in cols, low_memory=False).fillna("")
            if "zyh" not in df.columns:
                return
            df["zyh"] = df["zyh"].str.strip()
            n = 0
            for pid, group in df.groupby("zyh"):
                if not pid or pid.lower() in ("nan", "zyh"):
                    continue
                index.setdefault(pid, []).extend(group.to_dict("records"))
                n += len(group)
            logger.info("LabLoader: +overlay %d 行 from %s", n, p.name)
        except Exception as e:  # noqa: BLE001
            logger.warning("LabLoader overlay 失败: %s", e)

    def get_lab_results(
        self,
        patient_id: str,
        item_keyword: str | None = None,
        abnormal_only: bool = False,
    ) -> list[dict]:
        """返回该患者的检验记录,按 report_dt 升序.

        Args:
            patient_id: J/Kxxxxx 住院号
            item_keyword: 可选,在 rpt_itemname / inspectionName 任一命中即留
                          (中文/英文均可,大小写不敏感)
            abnormal_only: 仅返回 result_flag ∉ {"", "正常", "N"} 的行
        """
        idx = self._build_index()
        pid = patient_id.strip()
        rows = idx.get(pid, [])
        if not rows:
            return []
        if abnormal_only:
            rows = [r for r in rows if (r.get("result_flag") or "") not in NORMAL_FLAGS]
        if item_keyword:
            kw = item_keyword.strip().lower()

            def hit(r: dict) -> bool:
                # rpt_itemcode 含 TSH/AFP/CEA 等英文缩写; rpt_itemname 中文项名;
                # inspectionName 检验类别 (如 "免疫(BN2)"). 任一命中即留.
                for col in ("rpt_itemname", "rpt_itemcode", "inspectionName"):
                    if kw in (r.get(col) or "").lower():
                        return True
                return False

            rows = [r for r in rows if hit(r)]
        rows = sorted(rows, key=lambda r: r.get("report_dt") or "")
        return rows

    def patient_count(self) -> int:
        return len(self._build_index())
