# -*- coding: utf-8 -*-
"""ExaminationLoader — 检查报告 (CT/MRI/超声/心电图/内镜/肺功能...) 数据接入.

源: `sy_patient_examination.csv` (270k 行, 33 MB).
按 `zyh` 列 (J/Kxxxxx 住院号) 一次性 group 成 dict[patient_id, list[record]] 缓存.

为 v0.7 引入,补 case_notes 文书之外的检查报告证据 — 解决 R131 心彩超 / R155 胸部 CT
/ R278 肾上腺激素 等规则系统性误报 (专家批注反复指出 "可查检查报告/检验报告").

公开 API:
    ExaminationLoader(path).get_examinations(patient_id, type=None, keyword=None) -> list[dict]
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock

import pandas as pd

logger = logging.getLogger("javert.data.examination_loader")

# 只 load 实际会用到的列, 省内存
EXAM_USECOLS: tuple[str, ...] = (
    "zyh",
    "checkType",
    "checkItemName",
    "checkConclusion",
    "checkDescribe",
    "checkPosition",
    "department",
    "checkDate",
    "reportDate",
    "diagnosis",
    "isPos",
    "age",
    "Sex",
    "reporter",
    "auditor",
)


class ExaminationLoader:
    """按住院号索引的检查报告 loader. 进程级单例,首次访问时构建索引."""

    def __init__(self, path: Path, overlay_dir: Path | None = None):
        """overlay_dir: 仅工作台传 — 叠加 data_import/examinations.csv (onboarding 导入患者),
        与 CsvLoader.overlay 同理。审计侧不传 → 只读 base 文件。"""
        self.path = path
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
            nrows = 0
            if self.path.exists():
                df = pd.read_csv(
                    self.path,
                    dtype=str,
                    usecols=[c for c in EXAM_USECOLS],
                    low_memory=False,
                ).fillna("")
                df["zyh"] = df["zyh"].str.strip()
                nrows = len(df)
                for pid, group in df.groupby("zyh"):
                    if not pid or pid.lower() == "nan" or pid == "zyh":
                        continue
                    index.setdefault(pid, []).extend(group.to_dict("records"))
            else:
                logger.warning("检查数据文件不存在: %s (仅用 overlay)", self.path)
            self._merge_overlay(index)
            self._index = index
            logger.info(
                "ExaminationLoader: indexed %d patients (%d rows base) from %s in %.1fs",
                len(index), nrows, self.path.name, time.perf_counter() - t0,
            )
        return self._index

    def _merge_overlay(self, index: dict[str, list[dict]]) -> None:
        """叠加 data_import/examinations.csv (onboarding 导入患者), 与 CsvLoader.overlay 同理."""
        if self.overlay_dir is None:
            return
        p = self.overlay_dir / "examinations.csv"
        if not p.exists():
            return
        try:
            cols = set(EXAM_USECOLS)
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
            logger.info("ExaminationLoader: +overlay %d 行 from %s", n, p.name)
        except Exception as e:  # noqa: BLE001
            logger.warning("ExaminationLoader overlay 失败: %s", e)

    def get_examinations(
        self,
        patient_id: str,
        check_type: str | None = None,
        keyword: str | None = None,
    ) -> list[dict]:
        """返回该患者的检查记录,按 reportDate 升序.

        Args:
            patient_id: J/Kxxxxx 住院号
            check_type: 可选,checkType 子串匹配 (CT / 心超 / 放射 / 电生理 / ...)
            keyword: 可选,在 checkItemName + checkConclusion + checkDescribe 任一命中即留
        """
        idx = self._build_index()
        pid = patient_id.strip()
        rows = idx.get(pid, [])
        if not rows:
            return []
        if check_type:
            ct = check_type.strip()
            rows = [r for r in rows if ct in (r.get("checkType") or "")]
        if keyword:
            kw = keyword.strip()

            def hit(r: dict) -> bool:
                for col in ("checkItemName", "checkConclusion", "checkDescribe", "checkPosition"):
                    if kw in (r.get(col) or ""):
                        return True
                return False

            rows = [r for r in rows if hit(r)]
        rows = sorted(rows, key=lambda r: r.get("reportDate") or r.get("checkDate") or "")
        return rows

    def patient_count(self) -> int:
        """供 cli/diagnostics 用 — 触发 index 构建."""
        return len(self._build_index())
