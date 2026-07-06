# -*- coding: utf-8 -*-
"""CsvLoader — lazy load + 进程内单例缓存的 csv 实现."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .loader import DataLoader

logger = logging.getLogger("javert.data.csv_loader")

# 索引类型: (mode, {键值: 行位置数组}); mode = "exact" (住院号精确) / "contains" (bah 包含)
_KeyIndex = tuple[str, dict[str, np.ndarray]]


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
        # per-patient 键索引 (加载时构建一次) — get_notes/get_fees O(键数) 替代整表扫描
        self._notes_index: _KeyIndex | None = None
        self._fees_index: _KeyIndex | None = None

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

    @staticmethod
    def _build_index(df: pd.DataFrame, exact_col: str | None) -> _KeyIndex:
        """按患者键列分组 → {键值: 行位置数组}. exact_col 存在时精确匹配 (strip 后);
        否则 bah / 首列走包含匹配 (键唯一值仅数千, 远小于行数)."""
        if exact_col is not None and exact_col in df.columns:
            key = df[exact_col].astype(str).str.strip()
            mode = "exact"
        elif "bah" in df.columns:
            key = df["bah"].astype(str)
            mode = "contains"
        else:
            key = df.iloc[:, 0].astype(str)
            mode = "contains"
        return mode, df.groupby(key, sort=False).indices

    @staticmethod
    def _select(df: pd.DataFrame, index: _KeyIndex, patient_id: str) -> pd.DataFrame:
        mode, groups = index
        if mode == "exact":
            pos = groups.get(patient_id)
            hits = [pos] if pos is not None else []
        else:
            # ponytail: 纯子串匹配 (原实现是 str.contains 正则; 住院号均为字母数字, 语义等价)
            hits = [pos for k, pos in groups.items() if patient_id in k]
        if not hits:
            return df.iloc[0:0]
        return df.iloc[np.sort(np.concatenate(hits))]

    def _load_notes(self) -> pd.DataFrame:
        if self._notes is None:
            t0 = time.perf_counter()
            df = pd.read_csv(self.notes_path, dtype={"住院号": str}, low_memory=False)
            self._notes = self._overlay(df, "case_notes.csv", {"住院号": str})
            self._notes_index = self._build_index(self._notes, exact_col="住院号")
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
            # 费用表 bah 形如 "H31010600042-J13365 ", 走包含匹配索引
            self._fees_index = self._build_index(self._fees, exact_col=None)
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
        assert self._notes_index is not None
        return self._select(df, self._notes_index, patient_id)

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        df = self._load_fees()
        assert self._fees_index is not None
        return self._select(df, self._fees_index, patient_id)
