# -*- coding: utf-8 -*-
"""onboarding 路由测试 (add-visual-schema-onboarding Layer3).

鉴权闸 (302/401/503) + 连接预检同源逻辑 + 上传抽列 + manifest 星图视图.
不连真 142 / 不调 LLM.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from javert.web.api.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SZX_DIR = PROJECT_ROOT / "data" / "szx"
requires_szx = pytest.mark.skipif(
    not (SZX_DIR / "random_5pts_fee.csv").exists(), reason="szx fixture 不存在")


@pytest.fixture(scope="module")
def client_mssql():
    return TestClient(create_app(with_mssql=True))


@pytest.fixture(scope="module")
def client_no_mssql():
    return TestClient(create_app(with_mssql=False))


# ────────────── 鉴权闸 ──────────────

def test_onboarding_requires_login_redirect(client_mssql):
    r = client_mssql.get("/onboarding", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


def test_onboarding_api_unauth_401(client_mssql):
    r = client_mssql.post("/api/onboarding/preflight", json={"mapping": {}})
    assert r.status_code == 401


def test_onboarding_no_mssql_503(client_no_mssql):
    r = client_no_mssql.get("/onboarding", follow_redirects=False)
    assert r.status_code == 503


# ────────────── manifest 星图视图 ──────────────

def test_manifest_view_only_shows_processable_spokes():
    from javert.onboarding.manifest_loader import load_manifest
    from javert.web.api.routes_onboarding import manifest_view
    view = manifest_view(load_manifest())
    keys = {v["key"] for v in view}
    assert keys == {"fees", "notes", "diagnoses", "surgeries", "labs", "examinations",
                    "anesthesia", "pathology", "orders"}
    # 每个展示的 spoke 都有处理路径 (tool 或 tabular)
    for v in view:
        assert v["tool"] is not None or v["is_tabular"]
    fees = next(v for v in view if v["key"] == "fees")
    assert any(f["required"] for f in fees["fields"])


# ────────────── 连接预检同源逻辑 (GUI/CLI 共用) ──────────────

def _szx_mapping() -> dict:
    with open(PROJECT_ROOT / "configs" / "column_mapping.yaml", encoding="utf-8") as f:
        m = yaml.safe_load(f)
    files = {"fees": "random_5pts_fee.csv", "notes": "random_5pts_doc.csv",
             "diagnoses": "random_5pts_zd.csv", "surgeries": "random_5pts_oprn.csv"}
    for spoke, fname in files.items():
        m[spoke]["file"] = str(SZX_DIR / fname)
    return m


@requires_szx
def test_preflight_payload_green_on_szx():
    from javert.web.api.routes_onboarding import _preflight_payload
    payload = _preflight_payload(_szx_mapping())
    assert payload["ok"]
    assert len(payload["spokes"]) == 4
    # szx 4 表同一 Patient_ID 命名空间 → 应高覆盖
    assert payload["preflight"]["verdict"] in ("green", "yellow")


def test_preflight_payload_required_gate_blocks():
    from javert.web.api.routes_onboarding import _preflight_payload
    # 费用缺必填 amount → fatal
    bad = {"hospital_code": "x", "fees": {"file": "data/szx/random_5pts_fee.csv",
           "columns": {"patient_id": "Patient_ID", "item_name": "medins_list_name"}}}
    payload = _preflight_payload(bad)
    assert payload["ok"] is False
    assert any("amount" in e for e in payload["errors"])


@requires_szx
def test_read_columns_extracts_header():
    from javert.web.api.routes_onboarding import _read_columns
    cols, sample, n, exact = _read_columns(SZX_DIR / "random_5pts_fee.csv")
    assert "Patient_ID" in cols
    assert n > 0 and len(sample) > 0
    assert exact is True  # 小文件 (<500 行) 精确


@requires_szx
def test_read_columns_is_header_only_fast():
    # 行数估算不全扫: szx fee 1621 行, 估算值应在合理区间 (±35%)
    from javert.web.api.routes_onboarding import _read_columns, _estimate_csv_rows
    n, exact = _estimate_csv_rows(SZX_DIR / "random_5pts_fee.csv")
    assert 1000 < n < 2500  # 真实 1621, 全文件 <500 行? 不, 1621>500 → 估算
    cols, sample, n2, exact2 = _read_columns(SZX_DIR / "random_5pts_fee.csv")
    assert len(cols) > 3


# ────────────── 路径越权防护 ──────────────

def test_safe_path_rejects_escape():
    from javert.web.api.routes_onboarding import UnsafePath, _safe_path
    # 越权 (绝对/遍历) + 项目内敏感文件 (.env 含 session secret / configs / 审计库) 均须拒绝
    for bad in ("/etc/passwd", "../../../../etc/passwd", ".env",
                "configs/llm.yaml", "output/audit.sqlite", "src/javert/config.py"):
        with pytest.raises(UnsafePath):
            _safe_path(bad)
    # 仅数据目录合法
    assert _safe_path("data/szx/random_5pts_fee.csv").name == "random_5pts_fee.csv"
    assert _safe_path("data_import/_uploads/x.csv").name == "x.csv"


def test_validate_mapping_paths_rejects_escape():
    from javert.web.api.routes_onboarding import UnsafePath, _validate_mapping_paths
    with pytest.raises(UnsafePath):
        _validate_mapping_paths({"fees": {"file": "/etc/passwd", "columns": {}}})
    with pytest.raises(UnsafePath):
        _validate_mapping_paths({"notes": {"file": "data/x.csv",
                                "bridge": {"file": "/etc/shadow"}, "columns": {}}})


def test_profile_endpoint_rejects_traversal(client_mssql, monkeypatch):
    import javert.web.middleware as mw
    monkeypatch.setattr(mw, "_path_protected", lambda p: False)
    c = TestClient(create_app(with_mssql=True))
    r = c.post("/api/onboarding/profile", json={"file": ".env", "column": "x"})
    assert r.status_code == 400
    assert "越权" in r.json()["error"]


def test_delete_upload_removes_file_and_blocks_data_dir(tmp_path, monkeypatch):
    import javert.web.middleware as mw
    import javert.web.api.routes_onboarding as ro

    monkeypatch.setattr(mw, "_path_protected", lambda p: False)
    project_root = tmp_path / "project"
    upload_dir = project_root / "data_import/_uploads"
    protected = project_root / "data/router/violation_dict.json"
    protected.parent.mkdir(parents=True)
    protected.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ro, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(ro, "UPLOAD_DIR", upload_dir)

    c = TestClient(create_app(with_mssql=True))
    # 上传 → 删除 → 文件消失
    source = io.BytesIO(b"Patient_ID,medins_list_name,cnt,pric\nTEST-P001,item,1,10\n")
    up = c.post(
        "/api/onboarding/upload",
        files={"file": ("del_t.csv", source, "text/csv")},
    ).json()
    assert (project_root / up["file"]).exists()
    r = c.post("/api/onboarding/delete-upload", json={"file": up["file"]})
    assert r.json()["ok"] is True
    assert not (project_root / up["file"]).exists()
    # 不能删 _uploads 之外的既有数据
    bad = c.post("/api/onboarding/delete-upload",
                 json={"file": "data/router/violation_dict.json"})
    assert bad.status_code == 400
    assert protected.exists()


def test_upload_path_replaces_same_name(tmp_path, monkeypatch):
    # 设计 D4 (task 1.4): 同名重传替换 logical 文件, 不再生 foo_1.csv 幽灵文件
    import javert.web.api.routes_onboarding as ro
    monkeypatch.setattr(ro, "UPLOAD_DIR", tmp_path)
    (tmp_path / "fee.csv").write_text("x")
    p2 = ro._upload_path("fee.csv")
    assert p2.name == "fee.csv"  # 同名 → 同路径 (覆盖)


def test_stored_spokes_visible_in_overview(tmp_path, monkeypatch):
    # review finding #3: 声明的 stored 新表数据 MUST 在病案概览可见 (不静默丢)
    import json
    import javert.web.patient_overview as po
    from javert.config import PROJECT_ROOT
    base = PROJECT_ROOT / "data_import"
    base.mkdir(parents=True, exist_ok=True)
    (base / "stored_输血记录.csv").write_text(
        "patient_id,血型,数量\n211001,A,2\n211002,B,1\n", encoding="utf-8")
    (base / "_stored_spokes.json").write_text(json.dumps(
        [{"name": "输血记录", "file": "stored_输血记录.csv", "patient_col": "patient_id",
          "note": "已接收·暂不参与判定"}], ensure_ascii=False), encoding="utf-8")
    po.reset_caches()
    try:
        out = po.get_stored_spokes("211001")
        assert len(out) == 1
        assert out[0]["name"] == "输血记录"
        assert out[0]["total"] == 1  # 只该患者的行
        assert "暂不参与判定" in out[0]["note"]
    finally:
        (base / "stored_输血记录.csv").unlink(missing_ok=True)
        (base / "_stored_spokes.json").unlink(missing_ok=True)
        po.reset_caches()
