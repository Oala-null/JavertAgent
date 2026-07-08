# -*- coding: utf-8 -*-
"""工作台 hub 原文源 (add-workbench-sql-raw-source D3/D4/D6).

CSV (base+overlay) 双 miss 的患者按患者号实时查 142 hub 库 (cfg.hub_database),
返回与 CsvLoader / LabLoader 消费方同形的结构 — routes 下游零改动.

- 逐患者 LRU (maxsize 32, 无 TTL — 出院数据静态); 空结果也缓存 (防 404 反复查库),
  异常结果不缓存 (下次重试).
- 任何 SQL 异常 → warning 日志 + 空结果 (D6: 降级为 miss, 不是 500).
- pyodbc 连接非线程安全, 单锁串行化 (工作台低并发, 够用).
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict

import pandas as pd

from javert.data import hub_source as hs

logger = logging.getLogger(__name__)

_LRU_MAX = 32
_QUERY_TIMEOUT = 15  # 秒; 单患者索引查询实测 <1s, 网络异常时不拖死请求线程


def _empty_bundle() -> dict:
    return {"notes": pd.DataFrame(), "fees": pd.DataFrame(),
            "zd": pd.DataFrame(), "labs": pd.DataFrame(), "exams": pd.DataFrame()}


class HubRawSource:
    def __init__(self, cfg):
        self.cfg = cfg
        self._cn = None
        self._yq2org: dict[str, str] | None = None
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def _conn(self):
        if self._cn is None:
            self._cn = hs.connect(self.cfg, timeout=10)
            self._cn.timeout = _QUERY_TIMEOUT
        return self._cn

    def _bundle(self, patient_id: str) -> dict:
        pid = (patient_id or "").strip().upper()
        if not pid:
            return _empty_bundle()
        with self._lock:
            if pid in self._cache:
                self._cache.move_to_end(pid)
                return self._cache[pid]
            try:
                cn = self._conn()
                if self._yq2org is None:
                    self._yq2org = hs.fetch_hospital_map(cn)
                pids = [pid]
                bundle = {
                    "notes": hs.fetch_notes(cn, pids),
                    "fees": hs.fetch_fees(cn, pids, self._yq2org),
                    "zd": hs.fetch_zd(cn, pids, self._yq2org),
                    "labs": hs.fetch_labs(cn, pids),
                    "exams": hs.fetch_exams(cn, pids),
                }
            except Exception as e:  # noqa: BLE001 — D6: 降级为 miss, 不缓存, 连接重建
                logger.warning("hub 原文查询失败 patient=%s: %s", pid, e)
                self._cn = None
                return _empty_bundle()
            self._cache[pid] = bundle
            while len(self._cache) > _LRU_MAX:
                self._cache.popitem(last=False)
            return bundle

    # ── 消费方接口 (与 CsvLoader / LabLoader 结果同形) ──

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        return self._bundle(patient_id)["notes"]

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return self._bundle(patient_id)["fees"]

    def get_labs(self, patient_id: str) -> list[dict]:
        """与 LabLoader.get_lab_results 同形: list[dict], report_dt 升序."""
        df = self._bundle(patient_id)["labs"]
        if df is None or len(df) == 0:
            return []
        return df.sort_values("report_dt").to_dict("records")

    def get_exams(self, patient_id: str) -> list[dict]:
        df = self._bundle(patient_id)["exams"]
        if df is None or len(df) == 0:
            return []
        return df.to_dict("records")

    def get_main_diagnosis(self, patient_id: str) -> str | None:
        """maindiag_flag=1 首行 → '诊断名 (编码)' (与 _get_main_diagnosis label 同格式)."""
        df = self._bundle(patient_id)["zd"]
        if df is None or len(df) == 0:
            return None
        mains = df[df["maindiag_flag"] == 1]
        if len(mains) == 0:
            return None
        row = mains.iloc[0]
        label = str(row["inhosp_diag_name"])
        code = str(row.get("inhosp_diag_code") or "")
        if code:
            label += f" ({code})"
        return label
