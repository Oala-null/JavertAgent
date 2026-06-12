# -*- coding: utf-8 -*-
"""redesign-onboarding-demo-flow 测试.

覆盖: 自动归类 (classifier) / 服务端会话态 (OnbState) / 预检可执行诊断 /
日期歧义 + 用户确认归一 / .loaded.env 诚实化 / 清空已载入.
不连真 142 / 不调 LLM.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from javert.onboarding.manifest_loader import load_manifest
from javert.web.api.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SZX_DIR = PROJECT_ROOT / "data" / "szx"
requires_szx = pytest.mark.skipif(
    not (SZX_DIR / "random_5pts_fee.csv").exists(), reason="szx fixture 不存在")


def _alias() -> dict:
    from javert.web.api.routes_onboarding import _load_alias
    return _load_alias()


# ────────────── 文件→表自动归类 (设计 D2, task 5.1) ──────────────

def test_classify_szx_fee_columns_hits_fees():
    from javert.onboarding.classifier import classify_columns
    m, alias = load_manifest(), _alias()
    cols = ["Patient_ID", "medins_list_name", "det_item_fee_sumamt",
            "fee_ocur_time", "med_chrgitm_type", "cnt", "pric"]
    res = classify_columns(cols, m, alias)
    assert res.spoke == "fees" and not res.ambiguous
    assert res.matched.get("patient_id") == "Patient_ID"
    assert res.matched.get("amount") == "det_item_fee_sumamt"


def test_classify_song_notes_columns_hits_notes():
    from javert.onboarding.classifier import classify_columns
    m, alias = load_manifest(), _alias()
    cols = ["medcasno", "record_name", "replace", "record_date"]
    res = classify_columns(cols, m, alias)
    assert res.spoke == "notes" and not res.ambiguous
    assert res.matched.get("patient_id") == "medcasno"


def test_classify_ambiguous_does_not_guess():
    from javert.onboarding.classifier import classify_columns
    m, alias = load_manifest(), _alias()
    # 仅一个患者键候选列, 多表必填覆盖相当 → 歧义不猜
    res = classify_columns(["Patient_ID"], m, alias)
    assert res.ambiguous is True and res.spoke is None
    assert "确认" in res.reason


def test_classify_bridge_lookup_file_is_ambiguous():
    # 回归: 桥表/对照表 (只有患者键列, 无其它必填) 不应被误判成某张表 (旧 bug: 05_桥表→examinations
    # → examinations.item_name 未配置 → 整个 preflight 失败)
    from javert.onboarding.classifier import classify_columns
    m, alias = load_manifest(), _alias()
    res = classify_columns(["病案号", "住院号"], m, alias)
    assert res.ambiguous is True and res.spoke is None


def test_classify_view_spokes_not_targets():
    # 麻醉/病理 (view) 无 output_file → 不在 tabular_spokes → 永不作归类目标 (设计 D9)
    from javert.onboarding.classifier import classify_columns
    m = load_manifest()
    targets = set(m.tabular_spokes())
    assert "anesthesia" not in targets and "pathology" not in targets
    res = classify_columns(["Patient_ID", "medins_list_name", "det_item_fee_sumamt",
                            "fee_ocur_time", "med_chrgitm_type"], m, _alias())
    assert res.spoke not in ("anesthesia", "pathology")


# ────────────── 键模式默认取 manifest (task 5.2) ──────────────

def test_default_key_mode_from_manifest():
    from javert.onboarding.classifier import default_key_mode
    m = load_manifest()
    assert default_key_mode(m.spoke("notes")) == "bridge"   # via_bridge 非空
    assert default_key_mode(m.spoke("fees")) == "synth"     # compound, 无桥
    assert default_key_mode(m.spoke("labs")) == "synth"     # bare, 无桥


def test_synth_keymode_falls_to_id_form():
    from javert.onboarding.etl_engine import KeyNorm, resolve_pids
    m = load_manifest()
    pids = pd.Series(["211001", "211002"])
    fees_out = resolve_pids(pids, m.spoke("fees"), "szx", KeyNorm(mode="synth"))
    labs_out = resolve_pids(pids, m.spoke("labs"), "szx", KeyNorm(mode="synth"))
    assert all("-" in v for v in fees_out)       # fees compound → 合成复合键
    assert list(labs_out) == ["211001", "211002"]  # labs bare → 裸号


# ────────────── 服务端会话态 (设计 D4, task 1.x) ──────────────

def test_session_delete_cascades_mappings():
    from javert.web.onboarding_session import new_state
    st = new_state(load_manifest())
    st.add_file("data_import/_uploads/a.csv",
                {"filename": "a.csv", "columns": ["pid", "amt"], "n_rows": 2})
    st.set_mapping("fees", "patient_id", "data_import/_uploads/a.csv", "pid")
    st.set_mapping("fees", "amount", "data_import/_uploads/a.csv", "amt")
    assert len(st.map["fees"]["fields"]) == 2
    st.remove_file("data_import/_uploads/a.csv")
    # 删文件 → 引用它的映射全清, 文件不在态 (与磁盘零残留一致)
    assert "data_import/_uploads/a.csv" not in st.files
    assert st.map["fees"]["fields"] == {}


def test_session_cross_file_rebind_clears_old():
    from javert.web.onboarding_session import new_state
    st = new_state(load_manifest())
    st.add_file("f1.csv", {"columns": ["pid", "amt"]})
    st.add_file("f2.csv", {"columns": ["pid2"]})
    st.set_mapping("fees", "patient_id", "f1.csv", "pid")
    st.set_mapping("fees", "amount", "f1.csv", "amt")
    # 同 spoke 改绑另一文件 → 旧文件映射清空
    st.set_mapping("fees", "patient_id", "f2.csv", "pid2")
    fields = st.map["fees"]["fields"]
    assert fields["patient_id"]["file"] == "f2.csv"
    assert "amount" not in fields


def test_session_invalidates_preflight_on_mutation():
    from javert.web.onboarding_session import new_state
    st = new_state(load_manifest())
    st.set_preflight({"verdict": "green"})
    assert st.preflight_stale is False
    st.set_mapping("fees", "patient_id", "f.csv", "pid")
    assert st.preflight_stale is True   # 改映射 → 旧预检过期 (设计 D7)


def test_session_default_key_modes_seeded():
    from javert.web.onboarding_session import new_state
    st = new_state(load_manifest())
    assert st.map["notes"]["key_mode"] == "bridge"
    assert st.map["fees"]["key_mode"] == "synth"


# ────────────── 预检可执行诊断 (设计 D7, task 4.1) ──────────────

def test_preflight_diagnostics_points_lowest_table():
    from javert.onboarding.etl_engine import SpokeResult
    from javert.onboarding.join_preflight import preflight_keys
    from javert.web.api.routes_onboarding import _preflight_diagnostics
    m = load_manifest()
    fees = pd.DataFrame({c: [""] * 2 for c in m.spoke("fees").output_schema})
    fees["bah"] = ["szx-211001 ", "szx-211002 "]
    notes = pd.DataFrame({c: [""] * 2 for c in m.spoke("notes").output_schema})
    notes["住院号"] = ["999111", "999222"]  # 文书键 0% 命中
    spokes = [SpokeResult("fees", "费用", "shi_fee.csv", fees, 2),
              SpokeResult("notes", "文书", "case_notes.csv", notes, 2)]
    pf = preflight_keys(spokes, {"fees": m.spoke("fees"), "notes": m.spoke("notes")})
    assert pf.verdict == "red"
    diag = _preflight_diagnostics(pf, {"fees": "费用", "notes": "文书"})
    assert diag["lowest_spoke"] == "notes"
    assert diag["lowest_rate"] == 0.0
    assert "文书" in diag["message"] and ("桥表" in diag["message"] or "0%" in diag["message"])
    assert "notes" in diag["per_spoke"]


# ────────────── 日期歧义 + 用户确认归一 (设计 D8, task 4.4) ──────────────

def test_profiler_flags_ambiguous_dayfirst():
    from javert.onboarding.profiler import detect_date_format
    # 整列全 1–12, 扫完仍无 day>12 → 歧义, 不静默默认
    p = detect_date_format(["01/05/2025", "03/04/2025", "06/07/2025"])
    assert p.is_ambiguous_dayfirst is True
    assert p.dayfirst_confident is False
    # ISO 明确 → 不歧义
    assert detect_date_format(["2025-01-05"]).is_ambiguous_dayfirst is False


def test_user_decision_overrides_dayfirst_in_etl():
    from javert.onboarding.etl_engine import normalize_dates_in_df
    m = load_manifest()
    df = pd.DataFrame({c: [""] * 2 for c in m.spoke("fees").output_schema})
    df["fee_ocur_time"] = ["01/05/2025", "02/06/2025"]  # 歧义列
    # 用户选 M/D (dayfirst=False) → 01/05 应解析为 1 月 5 日
    out, notes = normalize_dates_in_df(df.copy(), m.spoke("fees"),
                                       {"fees.fee_ocur_time": False})
    assert out["fee_ocur_time"].iloc[0] == "2025-01-05"
    # 用户选 D/M (dayfirst=True) → 01/05 应解析为 5 月 1 日
    out2, _ = normalize_dates_in_df(df.copy(), m.spoke("fees"),
                                    {"fees.fee_ocur_time": True})
    assert out2["fee_ocur_time"].iloc[0] == "2025-05-01"
    assert any("用户确认" in n for n in notes)


def test_date_sample_compare_shows_both_parses():
    from javert.web.api.routes_onboarding import _date_sample_compare
    rows = _date_sample_compare(["01/05/2025"])
    assert rows[0]["dm"] == "2025-05-01" and rows[0]["md"] == "2025-01-05"


# ────────────── .loaded.env 诚实化 (设计 D5, task 2.1) ──────────────

def test_loaded_env_omits_unproduced_tables():
    from javert.web.api.routes_onboarding import _build_loaded_env
    env = _build_loaded_env("data_import", ["fees", "notes"],
                            {"fees": "shi_fee.csv", "notes": "case_notes.csv"})
    assert "export JAVERT_FEES_FILE=shi_fee.csv" in env
    assert "export JAVERT_DATA_DIR=data_import" in env
    assert "JAVERT_SQL_ENABLED=true" in env   # 默认同步到工作台/142
    # 缺表客户: 不硬写 ZD/SS (旧 bug)
    assert "JAVERT_ZD_FILE" not in env
    assert "JAVERT_SS_FILE" not in env


def test_loaded_env_sync_toggle():
    # sync_142=False → 本地 sqlite-only (排练用); True → 同步
    from javert.web.api.routes_onboarding import _build_loaded_env
    off = _build_loaded_env("data_import", ["fees"], {"fees": "shi_fee.csv"}, sync_142=False)
    assert "JAVERT_SQL_ENABLED=false" in off
    on = _build_loaded_env("data_import", ["fees"], {"fees": "shi_fee.csv"}, sync_142=True)
    assert "JAVERT_SQL_ENABLED=true" in on


def test_loaded_env_includes_produced_tables():
    from javert.web.api.routes_onboarding import _build_loaded_env
    env = _build_loaded_env(
        "data_import", ["fees", "notes", "diagnoses", "surgeries"],
        {"fees": "shi_fee.csv", "notes": "case_notes.csv",
         "diagnoses": "shi_zd.csv", "surgeries": "shi_ss.csv"})
    assert "export JAVERT_ZD_FILE=shi_zd.csv" in env
    assert "export JAVERT_SS_FILE=shi_ss.csv" in env


def test_loaded_env_writes_batch_tag():
    # 批次标签 → JAVERT_BATCH_TAG (使 jv-go 跑出的裁决都带 tag); 空 tag 不写
    from javert.web.api.routes_onboarding import _build_loaded_env
    env = _build_loaded_env("data_import", ["fees"], {"fees": "shi_fee.csv"}, batch_tag="v2.1")
    assert "export JAVERT_BATCH_TAG=v2.1" in env
    env2 = _build_loaded_env("data_import", ["fees"], {"fees": "shi_fee.csv"}, batch_tag="")
    assert "JAVERT_BATCH_TAG" not in env2


def test_session_batch_tag_roundtrip():
    from javert.web.onboarding_session import new_state
    st = new_state(load_manifest())
    st.set_meta(batch_tag="v2.1")
    assert st.batch_tag == "v2.1"
    assert st.to_dict()["batch_tag"] == "v2.1"


def test_clear_output_full_reset(tmp_path, monkeypatch):
    # 清空全部: 删产出 + 删上传 + 会话回空白
    import javert.web.api.routes_onboarding as ro
    import javert.web.middleware as mw
    monkeypatch.setattr(mw, "_path_protected", lambda p: False)
    monkeypatch.setattr(ro, "DATA_IMPORT_DIR", tmp_path)
    (tmp_path / "shi_fee.csv").write_text("bah\n1\n")
    up = tmp_path / "_uploads"; up.mkdir()
    (up / "x.csv").write_text("a\n1\n")
    c = TestClient(create_app(with_mssql=True))
    # 先建个会话 (上传一个文件进 session)
    c.post("/api/onboarding/session", json={"action": "set_meta", "hospital_code": "demo"})
    r = c.post("/api/onboarding/clear-output", json={}).json()
    assert r["ok"] is True
    assert not (tmp_path / "shi_fee.csv").exists()
    assert not (up / "x.csv").exists()           # 上传文件也删
    assert r["state"]["files"] == {}              # 会话回空白
    assert all(not m["fields"] for m in r["state"]["map"].values())


# ────────────── 清空已载入 (设计 D5, task 2.3) ──────────────

def test_clear_output_removes_products(tmp_path, monkeypatch):
    import javert.web.api.routes_onboarding as ro
    import javert.web.middleware as mw
    monkeypatch.setattr(mw, "_path_protected", lambda p: False)
    monkeypatch.setattr(ro, "DATA_IMPORT_DIR", tmp_path)  # 不碰真实 data_import
    (tmp_path / "shi_fee.csv").write_text("bah\n1\n")
    (tmp_path / ".loaded.env").write_text("x")
    (tmp_path / "stored_输血.csv").write_text("a\n1\n")
    c = TestClient(create_app(with_mssql=True))
    r = c.post("/api/onboarding/clear-output", json={})
    assert r.json()["ok"] is True
    assert not (tmp_path / "shi_fee.csv").exists()
    assert not (tmp_path / ".loaded.env").exists()
    assert not (tmp_path / "stored_输血.csv").exists()


# ────────────── 缺表客户端到端 (设计 D5, task 2.2) ──────────────

@requires_szx
def test_start_fees_notes_only_writes_honest_env(tmp_path, monkeypatch):
    import javert.web.api.routes_onboarding as ro
    import javert.web.middleware as mw
    monkeypatch.setattr(mw, "_path_protected", lambda p: False)
    monkeypatch.setattr(ro, "DATA_IMPORT_DIR", tmp_path)  # 产出落 tmp, 不碰真实 data_import
    c = TestClient(create_app(with_mssql=True))

    def setmap(spoke, field, file, col):
        c.post("/api/onboarding/session", json={
            "action": "set_mapping", "spoke": spoke, "field": field, "file": file, "col": col})

    fee, doc = "data/szx/random_5pts_fee.csv", "data/szx/random_5pts_doc.csv"
    for field, col in [("patient_id", "Patient_ID"), ("item_name", "medins_list_name"),
                       ("amount", "det_item_fee_sumamt"), ("date", "fee_ocur_time"),
                       ("category", "med_chrgitm_type")]:
        setmap("fees", field, fee, col)
    for field, col in [("patient_id", "Patient_ID"), ("section", "record_name"),
                       ("content", "replace")]:
        setmap("notes", field, doc, col)

    r = c.post("/api/onboarding/start", json={}).json()
    assert r["ok"], r
    env = (tmp_path / ".loaded.env").read_text()
    # 缺表客户: env 不指向不存在的 shi_zd.csv/shi_ss.csv
    assert "JAVERT_ZD_FILE" not in env and "JAVERT_SS_FILE" not in env
    assert "export JAVERT_FEES_FILE=shi_fee.csv" in env
    assert (tmp_path / "shi_fee.csv").exists() and (tmp_path / "case_notes.csv").exists()
    assert not (tmp_path / "shi_zd.csv").exists()


# ────────────── 上传即自动归类 (HTTP, 设计 D2, task 5.3) ──────────────

@requires_szx
def test_upload_auto_classifies_and_returns_state(monkeypatch):
    import javert.web.middleware as mw
    monkeypatch.setattr(mw, "_path_protected", lambda p: False)
    c = TestClient(create_app(with_mssql=True))
    with open(SZX_DIR / "random_5pts_fee.csv", "rb") as f:
        j = c.post("/api/onboarding/upload",
                   files={"file": ("auto_fee.csv", f, "text/csv")}).json()
    assert j["ok"] is True
    state = j["state"]
    rel = j["file"]
    # 自动认出 fees + 自动映射患者键
    assert state["classify"][rel]["spoke"] == "fees"
    assert state["map"]["fees"]["fields"].get("patient_id", {}).get("col") == "Patient_ID"
    # 清理
    c.post("/api/onboarding/delete-upload", json={"file": rel})
