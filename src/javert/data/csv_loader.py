# -*- coding: utf-8 -*-
"""CsvLoader — lazy load + 进程内单例缓存的 csv 实现."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from .loader import DataLoader

logger = logging.getLogger("javert.data.csv_loader")


class CsvLoader(DataLoader):
    """从 `case_notes.csv` 与 `shi_fee.csv` lazy load + 缓存."""

    def __init__(self, notes_path: Path, fees_path: Path, overlay_dir: Path | None = None):
        """overlay_dir: 仅工作台传 (coexist) — 把 data_import 的同名文件叠加到 base 数据上,
        让外部接入病人和 shi 演示病人并存于工作台。审计侧不传 (overlay_dir=None) → 不叠加,
        只读自己配置的目录, 互不干扰。"""
        self.notes_path = notes_path
        self.fees_path = fees_path
        self.overlay_dir = overlay_dir
        self._notes: pd.DataFrame | None = None
        self._fees: pd.DataFrame | None = None

    def _overlay(self, df: pd.DataFrame, filename: str, dtype: dict) -> pd.DataFrame:
        """把 overlay_dir/<filename> 追加到 base df (web coexist); 无 overlay/文件缺失 → 原样返回."""
        if self.overlay_dir is None:
            return df
        p = self.overlay_dir / filename
        if not p.exists():
            return df
        try:
            ext = pd.read_csv(p, dtype=dtype, low_memory=False)
            return pd.concat([df, ext], ignore_index=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("overlay %s 叠加失败: %s", filename, e)
            return df

    def _load_notes(self) -> pd.DataFrame:
        if self._notes is None:
            t0 = time.perf_counter()
            df = pd.read_csv(self.notes_path, dtype={"住院号": str}, low_memory=False)
            self._notes = self._overlay(df, "case_notes.csv", {"住院号": str})
            elapsed = time.perf_counter() - t0
            logger.info(
                "loaded %d rows from %s (+overlay) in %.2fs",
                len(self._notes), self.notes_path.name, elapsed,
            )
        return self._notes

    def _load_fees(self) -> pd.DataFrame:
        if self._fees is None:
            t0 = time.perf_counter()
            df = pd.read_csv(self.fees_path, dtype={"bah": str}, low_memory=False)
            self._fees = self._overlay(df, "shi_fee.csv", {"bah": str})
            elapsed = time.perf_counter() - t0
            logger.info(
                "loaded %d rows from %s (+overlay) in %.2fs",
                len(self._fees), self.fees_path.name, elapsed,
            )
        return self._fees

    def all_notes(self) -> pd.DataFrame:
        return self._load_notes()

    def all_fees(self) -> pd.DataFrame:
        return self._load_fees()

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        df = self._load_notes()
        if "住院号" in df.columns:
            mask = df["住院号"].astype(str).str.strip() == patient_id
        elif "bah" in df.columns:
            mask = df["bah"].astype(str).str.contains(patient_id, na=False)
        else:
            mask = df.iloc[:, 0].astype(str).str.contains(patient_id, na=False)
        return df[mask]

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        df = self._load_fees()
        # 费用表 bah 形如 "H31010600042-J13365 ", 需要包含匹配
        if "bah" in df.columns:
            mask = df["bah"].astype(str).str.contains(patient_id, na=False)
        else:
            mask = df.iloc[:, 0].astype(str).str.contains(patient_id, na=False)
        return df[mask]
