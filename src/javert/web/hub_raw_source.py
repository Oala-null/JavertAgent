# -*- coding: utf-8 -*-
"""工作台 Hub 原文源：按患者/页签最小查询、成功缓存和有界失败。"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import Executor, ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

import pandas as pd

from javert.data import hub_source as hs

logger = logging.getLogger(__name__)

_LRU_MAX = 96
_QUERY_TIMEOUT = 4
_DEADLINE_SECONDS = 5.0
_VALID_TABS = {"notes", "fees", "labs", "zd"}


class RawSourceUnavailable(RuntimeError):
    """可重试的 Hub 原文源失败；公开错误不携带患者、SQL 或连接信息。"""

    def __init__(self, error_code: str, tab: str):
        self.error_code = error_code
        self.tab = tab
        super().__init__(error_code)


def _duration_bucket(seconds: float) -> str:
    if seconds < 0.1:
        return "lt_100ms"
    if seconds < 1:
        return "100_999ms"
    if seconds < 5:
        return "1_4s"
    return "gte_5s"


def _empty_for(tab: str) -> Any:
    if tab == "labs":
        return {"labs": pd.DataFrame(), "exams": pd.DataFrame()}
    return pd.DataFrame()


class HubRawSource:
    def __init__(
        self,
        cfg,
        *,
        deadline_seconds: float = _DEADLINE_SECONDS,
        clock=time.monotonic,
        executor: Executor | None = None,
    ):
        self.cfg = cfg
        self.deadline_seconds = max(0.01, float(deadline_seconds))
        self._clock = clock
        self._cn = None
        self._yq2org: dict[str, str] | None = None
        self._cache: OrderedDict[tuple[str, str], Any] = OrderedDict()
        self._lock = threading.Lock()
        # 不用 with 临时池，超时返回时不会等待仍在驱动层取消中的查询。
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="javert-raw-hub"
        )

    def _diagnostic(
        self,
        *,
        tab: str,
        outcome: str,
        started: float,
        cache_hit: bool,
        error_code: str = "",
    ) -> None:
        logger.info(
            "raw_source source=hub tab=%s outcome=%s duration_bucket=%s "
            "cache_hit=%s error_code=%s",
            tab,
            outcome,
            _duration_bucket(self._clock() - started),
            int(cache_hit),
            error_code or "none",
        )

    def _conn(self):
        if self._cn is None:
            self._cn = hs.connect(self.cfg, timeout=_QUERY_TIMEOUT)
            self._cn.timeout = _QUERY_TIMEOUT
        return self._cn

    def _hospital_map(self, cn) -> dict[str, str]:
        if self._yq2org is None:
            self._yq2org = hs.fetch_hospital_map(cn)
        return self._yq2org

    def _query(self, pid: str, tab: str) -> Any:
        with self._lock:
            cn = self._conn()
            pids = [pid]
            if tab == "notes":
                return hs.fetch_notes(cn, pids)
            if tab == "fees":
                return hs.fetch_fees(cn, pids, self._hospital_map(cn))
            if tab == "zd":
                return hs.fetch_zd(cn, pids, self._hospital_map(cn))
            if tab == "labs":
                return {
                    "labs": hs.fetch_labs(cn, pids),
                    "exams": hs.fetch_exams(cn, pids),
                }
        raise ValueError("INVALID_RAW_TAB")

    def get_tab(self, patient_id: str, tab: str) -> Any:
        """严格页签接口：成功（包括真实空结果）缓存；失败/超时不缓存。"""

        pid = (patient_id or "").strip().upper()
        tab = (tab or "").strip().lower()
        if not pid or tab not in _VALID_TABS:
            raise ValueError("INVALID_RAW_TAB")
        started = self._clock()
        key = (pid, tab)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                value = self._cache[key]
                self._diagnostic(
                    tab=tab, outcome="success", started=started, cache_hit=True
                )
                return value
        future = self._executor.submit(self._query, pid, tab)
        try:
            value = future.result(timeout=self.deadline_seconds)
        except FutureTimeout as exc:
            future.cancel()
            self._cn = None
            self._diagnostic(
                tab=tab,
                outcome="timeout",
                started=started,
                cache_hit=False,
                error_code="HUB_TIMEOUT",
            )
            raise RawSourceUnavailable("HUB_TIMEOUT", tab) from exc
        except Exception as exc:  # noqa: BLE001
            self._cn = None
            code = "HUB_CONNECTION_ERROR" if isinstance(exc, (ConnectionError, OSError)) else "HUB_QUERY_ERROR"
            self._diagnostic(
                tab=tab,
                outcome="error",
                started=started,
                cache_hit=False,
                error_code=code,
            )
            raise RawSourceUnavailable(code, tab) from exc
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > _LRU_MAX:
                self._cache.popitem(last=False)
        self._diagnostic(tab=tab, outcome="success", started=started, cache_hit=False)
        return value

    def _compat(self, patient_id: str, tab: str) -> Any:
        try:
            return self.get_tab(patient_id, tab)
        except (RawSourceUnavailable, ValueError):
            return _empty_for(tab)

    # 消费方兼容接口。严格 HTTP tab 路径直接调用 get_tab，不吞错误。
    def get_notes(self, patient_id: str) -> pd.DataFrame:
        return self._compat(patient_id, "notes")

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        return self._compat(patient_id, "fees")

    def get_labs(self, patient_id: str) -> list[dict]:
        df = self._compat(patient_id, "labs")["labs"]
        if df is None or len(df) == 0:
            return []
        return df.sort_values("report_dt").to_dict("records")

    def get_exams(self, patient_id: str) -> list[dict]:
        df = self._compat(patient_id, "labs")["exams"]
        if df is None or len(df) == 0:
            return []
        return df.to_dict("records")

    def get_main_diagnosis(self, patient_id: str) -> str | None:
        df = self._compat(patient_id, "zd")
        if df is None or len(df) == 0:
            return None
        mains = df[df["maindiag_flag"] == 1]
        if len(mains) == 0:
            return None
        row = mains.iloc[0]
        label = str(row["inhosp_diag_name"])
        code = str(row.get("inhosp_diag_code") or "")
        return f"{label} ({code})" if code else label
