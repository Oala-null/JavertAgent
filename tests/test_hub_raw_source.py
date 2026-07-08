# -*- coding: utf-8 -*-
"""add-workbench-sql-raw-source: HubRawSource 适配器 + routes 链式回退 单测.

不连真库 — hs.connect / fetch_* 全部 monkeypatch; 路由函数直接调用 (不走 TestClient,
鉴权与本 change 无关).
"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi import HTTPException

import javert.data.hub_source as hs
import javert.web.api.routes_workbench as rw
from javert.config import JavertConfig
from javert.web.hub_raw_source import HubRawSource


def _notes_df(pid: str = "211999999") -> pd.DataFrame:
    return pd.DataFrame({
        "住院号": [pid], "事件时间": ["2025-12-01 08:00:00"], "阶段": ["入院记录"],
        "子阶段": ["主诉"], "内容": ["牙痛 3 天"], "来源文件": ["data_hub"],
    })


def _fees_df(pid: str = "211999999") -> pd.DataFrame:
    return pd.DataFrame({
        "bah": [f"42506084200-{pid}"], "medins_list_name": ["拔牙"], "spec": [""],
        "cnt": [1.0], "pric": ["100"], "det_item_fee_sumamt": [100.0],
        "medins_chrgitm_type": ["治疗"], "fee_ocur_time": ["2025-12-01 09:00:00"],
    })


class _StubCfg(JavertConfig):
    """真 config 子类, 只翻 hub 开关 (data_path 等 property 保持可用)."""
    hub_raw_enabled: bool = True
    sql_password: str = "x"


# =========================================================
# HubRawSource 单元
# =========================================================
def test_degrade_on_sql_error_and_no_error_cache(monkeypatch):
    """D6: 连接抛错 → 空结果不冒泡; 错误不缓存 (下次重试重连)."""
    attempts = []

    def boom(cfg, database=None, timeout=60):
        attempts.append(1)
        raise RuntimeError("net down")

    monkeypatch.setattr(hs, "connect", boom)
    src = HubRawSource(_StubCfg())
    assert len(src.get_notes("211999999")) == 0
    assert src.get_labs("211999999") == []
    assert len(attempts) == 2  # 两次调用两次重试 — 错误未被缓存


def test_lru_caches_bundle_per_patient(monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "connect", lambda cfg, database=None, timeout=60: type("C", (), {})())
    monkeypatch.setattr(hs, "fetch_hospital_map", lambda cn: {"0003": "42506084200"})
    monkeypatch.setattr(hs, "fetch_notes", lambda cn, pids: (calls.append(1), _notes_df())[1])
    monkeypatch.setattr(hs, "fetch_fees", lambda cn, pids, m: _fees_df())
    monkeypatch.setattr(hs, "fetch_zd", lambda cn, pids, m: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_labs", lambda cn, pids: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_exams", lambda cn, pids: pd.DataFrame())
    src = HubRawSource(_StubCfg())
    assert len(src.get_notes("211999999")) == 1
    assert len(src.get_fees("211999999")) == 1
    assert len(src.get_notes("211999999")) == 1
    assert len(calls) == 1  # 三次访问一次查库


def test_main_dx_label_format(monkeypatch):
    zd = pd.DataFrame({
        "maindiag_flag": [0, 1],
        "inhosp_diag_name": ["龋齿", "牙髓炎"],
        "inhosp_diag_code": ["K02.9", "K04.0"],
    })
    monkeypatch.setattr(hs, "connect", lambda cfg, database=None, timeout=60: type("C", (), {})())
    monkeypatch.setattr(hs, "fetch_hospital_map", lambda cn: {})
    monkeypatch.setattr(hs, "fetch_notes", lambda cn, pids: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_fees", lambda cn, pids, m: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_zd", lambda cn, pids, m: zd)
    monkeypatch.setattr(hs, "fetch_labs", lambda cn, pids: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_exams", lambda cn, pids: pd.DataFrame())
    src = HubRawSource(_StubCfg())
    assert src.get_main_diagnosis("211999999") == "牙髓炎 (K04.0)"


# =========================================================
# routes 链式回退
# =========================================================
class _EmptyLoader:
    def get_fees(self, pid):
        return None

    def get_notes(self, pid):
        return None


class _HubSentinel:
    """开关关 / CSV 命中时绝不该碰 hub — 一碰即炸."""

    def __getattr__(self, name):
        raise AssertionError(f"hub source 不应被访问: {name}")


class _HubStub:
    def get_notes(self, pid):
        return _notes_df(pid)

    def get_fees(self, pid):
        return _fees_df(pid)

    def get_labs(self, pid):
        return [{"report_dt": "2025-12-01", "rpt_itemname": "血常规", "result": "5.0"}]

    def get_exams(self, pid):
        return []

    def get_main_diagnosis(self, pid):
        return "牙髓炎 (K04.0)"


@pytest.fixture
def _route_env(monkeypatch):
    """公共桩: 空 CSV loader + 廉价 lab/exam loader + zd 缓存清零."""
    monkeypatch.setattr(rw, "_get_loader", lambda: _EmptyLoader())
    monkeypatch.setattr(rw, "_get_lab_loader",
                        lambda: type("L", (), {"get_lab_results": lambda self, p: []})())
    monkeypatch.setattr(rw, "_get_exam_loader",
                        lambda: type("E", (), {"get_examinations": lambda self, p: []})())
    monkeypatch.setattr(rw, "_zd_cache", {}, raising=False)


def test_raw_404_flag_off_hub_untouched(monkeypatch, _route_env):
    cfg = _StubCfg(hub_raw_enabled=False)
    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _HubSentinel())
    with pytest.raises(HTTPException) as ei:
        rw._raw_payload("211999999")
    assert ei.value.status_code == 404


def test_raw_hub_hit_flag_on(monkeypatch, _route_env):
    cfg = _StubCfg()
    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _HubStub())
    out = rw._raw_payload("211999999")
    assert out["fees"] and out["notes"]
    assert out["notes"][0]["content"] == "牙痛 3 天"


def test_raw_double_miss_404_flag_on(monkeypatch, _route_env):
    class _EmptyHub(_HubStub):
        def get_notes(self, pid):
            return pd.DataFrame()

        def get_fees(self, pid):
            return pd.DataFrame()

    cfg = _StubCfg()
    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _EmptyHub())
    with pytest.raises(HTTPException) as ei:
        rw._raw_payload("XNOPE")
    assert ei.value.status_code == 404


def test_csv_hit_never_queries_hub(monkeypatch):
    class _CsvLoaderStub:
        def get_fees(self, pid):
            return _fees_df(pid)

        def get_notes(self, pid):
            return _notes_df(pid)

    cfg = _StubCfg()
    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(rw, "_get_loader", lambda: _CsvLoaderStub())
    monkeypatch.setattr(rw, "_get_lab_loader",
                        lambda: type("L", (), {"get_lab_results": lambda self, p: [{"report_dt": "d"}]})())
    monkeypatch.setattr(rw, "_get_exam_loader",
                        lambda: type("E", (), {"get_examinations": lambda self, p: [{"reportDate": "d"}]})())
    monkeypatch.setattr(rw, "_zd_cache", {"211999999": "已缓存主诊"}, raising=False)
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _HubSentinel())
    out = rw._raw_payload("211999999")
    assert out["fees"] and out["notes"]


def test_labs_fallback_to_hub(monkeypatch, _route_env):
    cfg = _StubCfg()
    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _HubStub())
    rows = rw._labs_to_list("211999999")
    assert rows and rows[0]["item"] == "血常规"


def test_main_dx_fallback_to_hub(monkeypatch, _route_env):
    cfg = _StubCfg()
    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _HubStub())
    monkeypatch.setattr(rw, "_zd_cache", {}, raising=False)
    assert rw._get_main_diagnosis("211999999") == "牙髓炎 (K04.0)"