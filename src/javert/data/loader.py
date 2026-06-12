# -*- coding: utf-8 -*-
"""DataLoader 抽象接口 — 文书 + 费用按 patient_id 取 DataFrame."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataLoader(ABC):
    """患者数据接入接口. 实现见 `csv_loader.CsvLoader`."""

    @abstractmethod
    def get_notes(self, patient_id: str) -> pd.DataFrame:
        """返回该患者的全部文书行 (case_notes 子集). 无记录时返回空 DataFrame."""

    @abstractmethod
    def get_fees(self, patient_id: str) -> pd.DataFrame:
        """返回该患者的全部费用明细 (shi_fee 子集). 无记录时返回空 DataFrame."""

    @abstractmethod
    def all_notes(self) -> pd.DataFrame:
        """返回完整 case_notes DataFrame. 工具内部按 patient_id 自筛."""

    @abstractmethod
    def all_fees(self) -> pd.DataFrame:
        """返回完整 shi_fee DataFrame. 工具内部按 bah 包含匹配."""
