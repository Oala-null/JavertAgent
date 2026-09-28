# -*- coding: utf-8 -*-
"""add-workbench-sql-raw-source: HubRawSource 适配器 + routes 链式回退 单测.

不连真库 — hs.connect / fetch_* 全部 monkeypatch; 路由函数直接调用 (不走 TestClient,
鉴权与本 change 无关).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient

import javert.data.hub_source as hs
import javert.web.api.routes_workbench as rw
from javert.config import JavertConfig
from javert.web.hub_raw_source import HubRawSource, RawSourceUnavailable


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
    monkeypatch.setattr(hs, "fetch_hospital_map", lambda cn, **kw: {"0003": "42506084200"})
    monkeypatch.setattr(hs, "fetch_notes", lambda cn, pids, **kw: (calls.append(1), _notes_df())[1])
    monkeypatch.setattr(hs, "fetch_fees", lambda cn, pids, m, **kw: _fees_df())
    monkeypatch.setattr(hs, "fetch_zd", lambda cn, pids, m, **kw: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_labs", lambda cn, pids, **kw: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_exams", lambda cn, pids, **kw: pd.DataFrame())
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
    monkeypatch.setattr(hs, "fetch_hospital_map", lambda cn, **kw: {})
    monkeypatch.setattr(hs, "fetch_notes", lambda cn, pids, **kw: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_fees", lambda cn, pids, m, **kw: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_zd", lambda cn, pids, m, **kw: zd)
    monkeypatch.setattr(hs, "fetch_labs", lambda cn, pids, **kw: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_exams", lambda cn, pids, **kw: pd.DataFrame())
    src = HubRawSource(_StubCfg())
    assert src.get_main_diagnosis("211999999") == "牙髓炎 (K04.0)"


def test_success_cache_is_per_patient_and_tab(monkeypatch):
    calls = {"notes": 0, "fees": 0, "labs": 0, "exams": 0}
    monkeypatch.setattr(hs, "connect", lambda cfg, database=None, timeout=60: type("C", (), {})())
    monkeypatch.setattr(hs, "fetch_hospital_map", lambda cn, **kw: {})
    monkeypatch.setattr(hs, "fetch_notes", lambda cn, pids, **kw: (calls.__setitem__("notes", calls["notes"] + 1), _notes_df())[1])
    monkeypatch.setattr(hs, "fetch_fees", lambda cn, pids, m, **kw: (calls.__setitem__("fees", calls["fees"] + 1), _fees_df())[1])
    monkeypatch.setattr(hs, "fetch_labs", lambda cn, pids, **kw: (calls.__setitem__("labs", calls["labs"] + 1), pd.DataFrame())[1])
    monkeypatch.setattr(hs, "fetch_exams", lambda cn, pids, **kw: (calls.__setitem__("exams", calls["exams"] + 1), pd.DataFrame())[1])
    src = HubRawSource(_StubCfg())
    src.get_tab("211999999", "fees")
    src.get_tab("211999999", "fees")
    assert calls == {"notes": 0, "fees": 1, "labs": 0, "exams": 0}
    src.get_tab("211999999", "notes")
    assert calls["notes"] == 1


def test_table_prefix_propagates_to_every_raw_tab(monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(hs, "connect", lambda cfg, database=None, timeout=60: type("C", (), {})())
    monkeypatch.setattr(hs, "fetch_hospital_map", lambda cn, **kw: (seen.append(("hospital", kw["table_prefix"])), {})[1])
    monkeypatch.setattr(hs, "fetch_notes", lambda cn, pids, **kw: (seen.append(("notes", kw["table_prefix"])), pd.DataFrame())[1])
    monkeypatch.setattr(hs, "fetch_fees", lambda cn, pids, m, **kw: (seen.append(("fees", kw["table_prefix"])), pd.DataFrame())[1])
    monkeypatch.setattr(hs, "fetch_zd", lambda cn, pids, m, **kw: (seen.append(("zd", kw["table_prefix"])), pd.DataFrame())[1])
    monkeypatch.setattr(hs, "fetch_basics", lambda cn, pids, **kw: (seen.append(("basics", kw["table_prefix"])), pd.DataFrame())[1])
    monkeypatch.setattr(hs, "fetch_labs", lambda cn, pids, **kw: (seen.append(("labs", kw["table_prefix"])), pd.DataFrame())[1])
    monkeypatch.setattr(hs, "fetch_exams", lambda cn, pids, **kw: (seen.append(("exams", kw["table_prefix"])), pd.DataFrame())[1])

    src = HubRawSource(_StubCfg(hub_table_prefix="desus_"))
    for tab in ("notes", "fees", "zd", "basics", "labs"):
        src.get_tab("211999999", tab)

    assert {name for name, _ in seen} == {
        "hospital", "notes", "fees", "zd", "basics", "labs", "exams"
    }
    assert {prefix for _, prefix in seen} == {"desus_"}


def test_basics_are_exposed_as_one_structured_record(monkeypatch):
    monkeypatch.setattr(hs, "connect", lambda cfg, database=None, timeout=60: type("C", (), {})())
    monkeypatch.setattr(
        hs,
        "fetch_basics",
        lambda cn, pids, **kw: pd.DataFrame([{
            "patient_id": pids[0],
            "gender": "女",
            "age": "45",
            "admission_date": "2026-01-01",
            "discharge_date": "2026-01-03",
            "los_days": "3",
            "department": "测试科",
            "doctor": "测试医生",
        }]),
    )
    src = HubRawSource(_StubCfg())

    basics = src.get_basics("211999999")

    assert basics["gender"] == "女"
    assert basics["age"] == "45"
    assert basics["department"] == "测试科"


def test_timeout_returns_quickly_is_not_cached_and_diagnostics_hide_patient(monkeypatch, caplog):
    attempts = []
    monkeypatch.setattr(hs, "connect", lambda cfg, database=None, timeout=60: type("C", (), {})())

    def slow_then_recover(cn, pids, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            time.sleep(0.08)
        return _notes_df()

    monkeypatch.setattr(hs, "fetch_notes", slow_then_recover)
    src = HubRawSource(_StubCfg(), deadline_seconds=0.01)
    started = time.monotonic()
    with caplog.at_level(logging.INFO), pytest.raises(RawSourceUnavailable) as exc:
        src.get_tab("211999999", "notes")
    assert exc.value.error_code == "HUB_TIMEOUT"
    assert time.monotonic() - started < 0.06
    assert "211999999" not in caplog.text
    time.sleep(0.09)
    assert len(src.get_tab("211999999", "notes")) == 1
    assert len(attempts) == 2


def test_disconnect_failure_does_not_poison_tab_cache(monkeypatch):
    attempts = []
    monkeypatch.setattr(hs, "connect", lambda cfg, database=None, timeout=60: type("C", (), {})())
    monkeypatch.setattr(hs, "fetch_hospital_map", lambda cn, **kw: {})

    def fetch(cn, pids, mapping, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("synthetic disconnect")
        return _fees_df()

    monkeypatch.setattr(hs, "fetch_fees", fetch)
    src = HubRawSource(_StubCfg())
    with pytest.raises(RawSourceUnavailable) as exc:
        src.get_tab("211999999", "fees")
    assert exc.value.error_code == "HUB_CONNECTION_ERROR"
    assert len(src.get_tab("211999999", "fees")) == 1
    assert len(attempts) == 2


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

    def get_tab(self, pid, tab):
        if tab == "notes":
            return self.get_notes(pid)
        if tab == "fees":
            return self.get_fees(pid)
        if tab == "labs":
            return {"labs": pd.DataFrame(self.get_labs(pid)), "exams": pd.DataFrame()}
        raise AssertionError(f"unexpected tab: {tab}")


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


def test_tab_fees_does_not_wait_for_notes_or_labs(monkeypatch, _route_env):
    calls = []

    class _TabHub:
        def get_tab(self, pid, tab):
            calls.append(tab)
            assert tab == "fees"
            return _fees_df(pid)

    monkeypatch.setattr(rw, "get_config", lambda: _StubCfg())
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _TabHub())
    out = rw._raw_payload("211999999", tab="fees")
    assert out["tab"] == "fees" and out["n_fees"] == 1
    assert calls == ["fees"]


def test_tab_selects_profile_from_latest_batch_tag(monkeypatch, _route_env):
    class _TaggedStore:
        def latest_batch_tag_for_patient(self, _pid):
            return "desus"

    cfg = _StubCfg(
        hub_raw_profiles={
            "desus": {"database": "TP_data_hub", "table_prefix": "desus_"}
        }
    )
    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(rw, "get_sqlserver_store", lambda: _TaggedStore())
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _HubSentinel())
    monkeypatch.setattr(rw, "_get_hub_profile_source", lambda tag: _HubStub())

    out = rw._raw_payload("211999999", tab="fees")
    assert out["source"] == "hub"
    assert out["n_fees"] == 1


def test_ocr_profile_bypasses_csv_and_default_hub(monkeypatch, _route_env):
    class _TaggedStore:
        def latest_batch_tag_for_patient(self, _pid):
            return "ocr1.0"

    cfg = _StubCfg(
        hub_raw_enabled=False,
        hub_raw_profiles={
            "ocr1.0": {"database": "TP_data_hub", "table_prefix": "desus_"}
        },
    )
    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(rw, "get_sqlserver_store", lambda: _TaggedStore())
    monkeypatch.setattr(rw, "_get_loader", lambda: _HubSentinel())
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _HubSentinel())
    monkeypatch.setattr(
        rw,
        "_get_hub_profile_source",
        lambda tag: _HubStub() if tag == "ocr1.0" else None,
    )

    out = rw._raw_payload("MASKED-OCR-1")

    assert out["source"] == "hub"
    assert out["fees"] and out["notes"]
    assert out["main_diagnosis"] == "牙髓炎 (K04.0)"


def test_profile_source_uses_isolated_database_and_prefix(monkeypatch):
    import javert.web.hub_raw_source as hub_module

    captured = []
    cfg = _StubCfg(
        hub_database="sh_yb_platform",
        hub_table_prefix="",
        hub_raw_profiles={
            "desus": {"database": "TP_data_hub", "table_prefix": "desus_"}
        },
    )

    class _CapturedSource:
        def __init__(self, source_cfg):
            captured.append((source_cfg.hub_database, source_cfg.hub_table_prefix))

    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(hub_module, "HubRawSource", _CapturedSource)
    monkeypatch.setattr(rw, "_hub_profile_source_singletons", {})

    first = rw._get_hub_profile_source("desus")
    second = rw._get_hub_profile_source("desus")
    assert first is second
    assert captured == [("TP_data_hub", "desus_")]
    assert cfg.hub_database == "sh_yb_platform"
    assert cfg.hub_table_prefix == ""


def test_tab_without_matching_profile_keeps_default_hub(monkeypatch, _route_env):
    class _TaggedStore:
        def latest_batch_tag_for_patient(self, _pid):
            return "ordinary"

    cfg = _StubCfg(
        hub_raw_profiles={
            "desus": {"database": "TP_data_hub", "table_prefix": "desus_"}
        }
    )
    monkeypatch.setattr(rw, "get_config", lambda: cfg)
    monkeypatch.setattr(rw, "get_sqlserver_store", lambda: _TaggedStore())
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _HubStub())
    monkeypatch.setattr(rw, "_get_hub_profile_source", lambda tag: None)

    out = rw._raw_payload("211999999", tab="notes")
    assert out["source"] == "hub"
    assert out["n_notes"] == 1


def test_tab_hub_failure_is_retryable_503_not_404(monkeypatch, _route_env):
    class _UnavailableHub:
        def get_tab(self, pid, tab):
            raise RawSourceUnavailable("HUB_TIMEOUT", tab)

    monkeypatch.setattr(rw, "get_config", lambda: _StubCfg())
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _UnavailableHub())
    with pytest.raises(HTTPException) as exc:
        rw._raw_payload("211999999", tab="fees")
    assert exc.value.status_code == 503
    assert exc.value.detail == {
        "code": "HUB_TIMEOUT",
        "message": "原文数据源暂不可用，请稍后重试。",
        "retryable": True,
        "tab": "fees",
    }


def test_tab_true_double_miss_is_404(monkeypatch, _route_env):
    class _EmptyTabHub:
        def get_tab(self, pid, tab):
            return pd.DataFrame()

    monkeypatch.setattr(rw, "get_config", lambda: _StubCfg())
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _EmptyTabHub())
    with pytest.raises(HTTPException) as exc:
        rw._raw_payload("XNOPE", tab="fees")
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "RAW_TAB_NOT_FOUND"


def test_tab_http_contract_distinguishes_503_and_404(monkeypatch, _route_env):
    class _SwitchingHub:
        def get_tab(self, pid, tab):
            if pid == "CASE-UNAVAILABLE":
                raise RawSourceUnavailable("HUB_QUERY_ERROR", tab)
            return pd.DataFrame()

    monkeypatch.setattr(rw, "get_config", lambda: _StubCfg())
    monkeypatch.setattr(rw, "_get_hub_source", lambda: _SwitchingHub())
    monkeypatch.setattr(rw, "_log_raw_access", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(rw.router)
    client = TestClient(app)
    unavailable = client.get("/api/patient/CASE-UNAVAILABLE/raw?tab=fees")
    missing = client.get("/api/patient/CASE-MISSING/raw?tab=fees")
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["retryable"] is True
    assert unavailable.json()["detail"]["code"] == "HUB_QUERY_ERROR"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "RAW_TAB_NOT_FOUND"


def test_frontend_uses_lazy_tab_requests_and_chinese_failure_states():
    js = (Path(__file__).parents[1] / "src/javert/web/static/app.js").read_text(encoding="utf-8")
    assert '"/raw?tab="' in js
    assert "switchRawTab" in js
    assert "原文数据源暂不可用，请稍后重试" in js
    assert "该页签暂无原始数据" in js
    assert "HTTP 502" not in js
