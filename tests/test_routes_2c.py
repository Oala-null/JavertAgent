# -*- coding: utf-8 -*-
"""2C 平台对接接口测试 (submit/results) — 不连 LLM / 142.

契约: docs/2c对接_javert审计服务.md
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import pytest


class _FakeLoader:
    """既有样例与语义化 v2 样例有数据, 其余患者查无."""

    _PATIENTS = {"J66252", "CASE-V2-001"}

    def get_notes(self, patient_id: str) -> pd.DataFrame:
        if patient_id in self._PATIENTS:
            return pd.DataFrame({"record_name": ["入院记录"]})
        return pd.DataFrame()

    def get_fees(self, patient_id: str) -> pd.DataFrame:
        if patient_id in self._PATIENTS:
            return pd.DataFrame({
                "medins_list_name": [
                    "麻醉后复苏监护(PACU)",
                    "麻醉后复苏监护(PACU)",
                    "静脉输液",
                    "注射用维泊妥珠单抗",
                ],
                "med_list_codg": [
                    "331501001",
                    "331501001",
                    "120400001",
                    "SYNTHETIC-DRUG-CODE",
                ],
                "medins_list_codg": ["F00123", "F00123", "F00456", "FDRUG01"],
                "fee_ocur_time": [
                    "2026-07-01 08:30:00",
                    "2026-07-02 09:45:00",
                    "2026-07-03 10:00:00",
                    "2026-07-04 11:15:00",
                ],
                "cnt": [1, 1, 1, 1],
                "pric": [300, 300, 25.5, 18800],
                "acord_dept_codg": ["D001", "D001", "D002", "D003"],
                "acord_dept_name": ["麻醉科", "麻醉科", "输液室", "肿瘤科"],
                "orders_dr_code": ["DR001", "DR001", "DR002", "DR003"],
                "orders_dr_name": ["医生甲", "医生甲", "医生乙", "医生丙"],
            })
        return pd.DataFrame()


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient: 关 142 双写 + sqlite 落 tmp + stub 掉真审计."""
    for key in list(os.environ):
        if key.startswith("JAVERT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JAVERT_SQL_ENABLED", "false")
    monkeypatch.setenv("JAVERT_AUDIT_DB", str(tmp_path / "audit.sqlite"))
    monkeypatch.setenv("JAVERT_SESSION_SECRET", "pytest-session-secret-not-for-prod")

    from javert.config import reset_config_cache
    reset_config_cache()

    if "javert.web.api.main" in sys.modules:
        del sys.modules["javert.web.api.main"]
    if "javert.store.sqlserver_store" in sys.modules:
        from javert.store.sqlserver_store import reset_sqlserver_store
        reset_sqlserver_store()

    from javert.web.api import routes_audit
    monkeypatch.setattr(routes_audit, "_get_loader", lambda: _FakeLoader())
    monkeypatch.setattr(routes_audit, "_2c_run_patient", lambda syxh: None)
    monkeypatch.setattr(routes_audit, "_hub_probe", lambda syxh: False)  # 默认中台也查无
    routes_audit._2c_tasks.clear()
    getattr(routes_audit, "_clear_2c_card_cache", lambda: None)()

    from fastapi.testclient import TestClient
    from javert.web.api.main import create_app

    app = create_app(with_mssql=True)
    with TestClient(app) as c:
        yield c

    routes_audit._2c_tasks.clear()
    getattr(routes_audit, "_clear_2c_card_cache", lambda: None)()
    reset_config_cache()


def _wait_done(client, syxh: str, timeout: float = 3.0) -> dict:
    """轮询直到 status=done (worker 是后台线程)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/audit/results/{syxh}").json()
        if body["status"] == "done":
            return body
        time.sleep(0.05)
    raise AssertionError(f"{syxh} 未在 {timeout}s 内 done")


def test_public_no_auth_required():
    """submit/results 免鉴权, /api/audit 其余路径仍受保护."""
    from javert.web.middleware import _path_protected

    assert not _path_protected("/api/audit/submit")
    assert not _path_protected("/api/audit/results/J66252")
    assert not _path_protected("/api/audit/v2/submit")
    assert not _path_protected("/api/audit/v2/results/J66252")
    assert not _path_protected("/api/audit/v3/submit")
    assert not _path_protected("/api/audit/v3/results/J66252")
    assert _path_protected("/api/audit/run")
    assert _path_protected("/api/audit/runs")
    assert _path_protected("/api/audit/v2/run")
    assert _path_protected("/api/audit/v3/run")


def test_behavior_mapping_virtual_is_standard_and_swap_stays_independent():
    from javert.web.rule_meta import behavior_code, behavior_name, reset_cache

    reset_cache()
    assert behavior_code("虚构医药服务项目") == "T380206"
    assert behavior_name("虚构医药服务项目") == "提供不必要的医药服务"
    assert behavior_code("虚构医药服务") == "T380206"
    assert behavior_name("虚构医药服务项目或以骗保为目的串换项目") == (
        "提供不必要的医药服务"
    )
    assert behavior_code("串换项目") == ""
    assert behavior_name("串换项目") == "串换药品、医用耗材、诊疗项目和服务设施"


def test_submit_accept_and_reject(client):
    resp = client.post("/api/audit/submit", json=[
        {"SYXH": "J66252", "YLZZJGDM": "H31010600042"},
        {"SYXH": "J99999", "YLZZJGDM": "H31010600042"},
        {"SYXH": "", "YLZZJGDM": ""},
    ])
    assert resp.status_code == 202
    body = resp.json()
    assert len(body["accepted"]) == 1
    first_accepted = body["accepted"][0]
    assert first_accepted["SYXH"] == "J66252"
    assert first_accepted["source"] == "local"
    assert first_accepted["attempt_id"].startswith("att_")
    first_attempt = first_accepted["attempt_id"]
    assert {r["SYXH"]: r["reason"] for r in body["rejected"]} == {
        "J99999": "本地与数据中台均查无此患者",
        "": "SYXH 为空",
    }
    body = _wait_done(client, "J66252")
    assert body["YLZZJGDM"] == "H31010600042"
    assert body["attempt_id"] == first_attempt
    assert body["outcome"] == "succeeded"
    assert body["progress"] == {"total": 0, "completed": 0, "failed": 0}

    # 重复提交已 done 的患者 → 重新受理 (重跑)
    resp2 = client.post("/api/audit/submit", json=[{"SYXH": "J66252"}])
    second_accepted = resp2.json()["accepted"][0]
    assert second_accepted["SYXH"] == "J66252"
    assert second_accepted["source"] == "local"
    assert second_accepted["attempt_id"].startswith("att_")
    assert second_accepted["attempt_id"] != first_attempt
    body2 = _wait_done(client, "J66252")  # 等 worker 消费完再 teardown, 防 monkeypatch 撤销后跑真审计
    assert body2["attempt_id"] == second_accepted["attempt_id"]


def test_v2_submit_reuses_running_v1_attempt(client, monkeypatch):
    """v1/v2 共用任务状态，切换版本不能把同一患者重复入队。"""
    from javert.web.api import routes_audit

    routes_audit._2c_tasks["CASE-V2-001"] = {
        "YLZZJGDM": "H-SYNTHETIC",
        "status": "running",
        "outcome": "running",
        "attempt_id": "att_shared_v2",
        "source": "local",
        "stage": "audit",
        "total": 2,
        "run_ids": [],
        "failed": [],
        "submitted_at": "2026-07-24T00:00:00+00:00",
    }
    queued: list[str] = []
    monkeypatch.setattr(routes_audit._2c_queue, "put", queued.append)

    response = client.post(
        "/api/audit/v2/submit",
        json=[{"SYXH": "CASE-V2-001", "YLZZJGDM": "H-SYNTHETIC"}],
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == [{
        "SYXH": "CASE-V2-001",
        "source": "local",
        "attempt_id": "att_shared_v2",
    }]
    assert queued == []


def test_v3_submit_reuses_running_attempt(client, monkeypatch):
    """v3 submit 与 v1/v2 共用 running attempt，不产生第二个任务。"""
    from javert.web.api import routes_audit

    routes_audit._2c_tasks["CASE-V2-001"] = {
        "YLZZJGDM": "H-SYNTHETIC",
        "status": "running",
        "outcome": "running",
        "attempt_id": "att_shared_v3",
        "source": "local",
        "stage": "audit",
        "total": 2,
        "run_ids": [],
        "failed": [],
        "submitted_at": "2026-07-27T00:00:00+00:00",
    }
    queued: list[str] = []
    monkeypatch.setattr(routes_audit._2c_queue, "put", queued.append)

    response = client.post(
        "/api/audit/v3/submit",
        json=[{"SYXH": "CASE-V2-001", "YLZZJGDM": "H-SYNTHETIC"}],
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == [{
        "SYXH": "CASE-V2-001",
        "source": "local",
        "attempt_id": "att_shared_v3",
    }]
    assert queued == []


def test_submit_hub_fallback(client, monkeypatch):
    """本地查无 → 中台有 → 受理 source=hub; 中台连接失败 → 诚实 reason."""
    from javert.web.api import routes_audit

    monkeypatch.setattr(routes_audit, "_hub_probe", lambda syxh: True)
    body = client.post("/api/audit/submit", json=[{"SYXH": "211449756"}]).json()
    assert body["accepted"][0]["SYXH"] == "211449756"
    assert body["accepted"][0]["source"] == "hub"
    assert body["accepted"][0]["attempt_id"].startswith("att_")
    assert routes_audit._2c_tasks["211449756"]["source"] == "hub"
    _wait_done(client, "211449756")

    def _boom(syxh):
        raise RuntimeError("hub down")

    monkeypatch.setattr(routes_audit, "_hub_probe", _boom)
    body = client.post("/api/audit/submit", json=[{"SYXH": "211000000"}]).json()
    assert body["rejected"] == [{"SYXH": "211000000", "reason": "本地查无, 数据中台连接失败"}]


def test_hub_cfg_paths(tmp_path, monkeypatch):
    """_hub_cfg_for: 整链路路径全指向取数目录 — 含部署机 .env 把 notes/fees
    指到合并版文件 (shi_fee_with_szx.csv) 的场景 (62 实测踩过)."""
    from javert.config import get_config, reset_config_cache
    from javert.web.api.routes_audit import _hub_cfg_for

    monkeypatch.setenv("JAVERT_FEES_FILE", "shi_fee_with_szx.csv")
    monkeypatch.setenv("JAVERT_NOTES_FILE", "case_notes_with_szx.csv")
    reset_config_cache()
    try:
        cfg2 = _hub_cfg_for(get_config(), tmp_path)
    finally:
        reset_config_cache()
    assert cfg2.notes_path == tmp_path / "case_notes.csv"
    assert cfg2.fees_path == tmp_path / "shi_fee.csv"
    assert cfg2.zd_path == tmp_path / "shi_zd.csv"
    assert cfg2.ss_path == tmp_path / "shi_ss.csv"
    assert cfg2.labs_path == tmp_path / "lab_results.csv"
    assert cfg2.examinations_path == tmp_path / "examinations.csv"


def test_results_unknown(client):
    body = client.get("/api/audit/results/NEVER").json()
    assert body["status"] == "unknown"
    assert body["outcome"] == "unknown"
    assert body["attempt_id"] is None
    assert body["error_code"] == ""
    assert body["retryable"] is False
    assert body["progress"] == {"total": 0, "completed": 0, "failed": 0}
    assert body["results"] == []
    assert body["summary"] == {"total": 0, "violation": 0, "inconclusive": 0, "clean": 0}


def test_v2_results_unknown_has_versioned_empty_cards(client):
    body = client.get("/api/audit/v2/results/CASE-UNKNOWN").json()
    assert body["api_version"] == "2.0"
    assert body["status"] == "unknown"
    assert body["outcome"] == "unknown"
    assert body["cards"] == []
    assert "results" not in body


def test_v3_results_unknown_has_versioned_empty_cards(client):
    body = client.get("/api/audit/v3/results/CASE-UNKNOWN").json()
    assert body["api_version"] == "3.0"
    assert body["status"] == "unknown"
    assert body["progress"] == {"total": 0, "completed": 0, "failed": 0}
    assert body["cards"] == []
    assert "results" not in body


def test_results_done_with_runs(client):
    """注入一条已落库的裁决 → results 出全字段."""
    from javert.audit.result import AuditResult, Evidence
    from javert.config import get_config
    from javert.store.audit_store import SqliteStore
    from javert.web.api import routes_audit

    result = AuditResult(
        run_id="aud_TESTtest0001",
        rule_id="R191",
        patient_id="J66252",
        verdict="VIOLATION",
        confidence=0.85,
        reasoning="R191 经 search_fees 核实, 应判 VIOLATION",
        evidence=[Evidence(source="search_fees", locator="麻醉后复苏监护(PACU)", text="费用明细中出现 ¥300 麻醉后复苏监护(PACU)项")],
        duration_ms=45000,
        model="test-model",
        started_at=datetime(2026, 7, 15, 8, 0, 0, tzinfo=timezone.utc),
    )
    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    store.write(result)
    store.close()

    routes_audit._2c_tasks["J66252"] = {
        "YLZZJGDM": "H31010600042", "status": "done", "total": 1,
        "run_ids": ["aud_TESTtest0001"], "failed": [],
        "submitted_at": "2026-07-15T08:00:00+00:00",
    }

    body = client.get("/api/audit/results/J66252").json()
    assert body["status"] == "done"
    assert body["summary"] == {"total": 1, "violation": 1, "inconclusive": 0, "clean": 0}
    (item,) = body["results"]
    assert item["rule_id"] == "R191"
    assert item["handling_level"] == "违规（阻断）"
    assert item["verdict"] == "VIOLATION"
    assert item["verdict_label"] == "违规"
    assert item["evidence"] == [{
        "source": "search_fees",
        "locator": "麻醉后复苏监护(PACU)",
        "text": "费用明细中出现 ¥300 麻醉后复苏监护(PACU)项",
    }]
    assert item["finished_at"] == "2026-07-15T08:00:45+00:00"
    assert item["rule_name"]  # R191 yaml 存在 → violation_type 非空
    assert item["behavior_code"] == "T380301"
    assert set(item["public_explanation"]) == {
        "conclusion", "narrative", "audit_items", "charge_facts", "basis", "clinical_evidence", "review_needs",
    }
    assert "promise" in item
    # hits: V 结果 join 患者费用行出编码 (2C 侧凭 code_nat/matched_fee_name 对明细)
    (hit,) = item["hits"]
    assert hit["source"] == "fee"
    assert hit["matched_fee_name"] == "麻醉后复苏监护(PACU)"
    assert hit["code_nat"] == "331501001"
    assert hit["code_local"] == "F00123"
    # behavior-naming 三新字段: 顶层扁平编码/名称 + 行为认定名称 (R191=重复收费)
    assert item["hit_codes"] == ["331501001"]
    assert item["hit_names"] == ["麻醉后复苏监护(PACU)"]
    assert item["behavior_name"] == "重复收费"
    # reasoning 对外自然语言化: 术语全部映射, R 代号/工具名/英文判定词不外泄
    assert item["reasoning"] == "本规则 经 费用明细检索 核实, 应判 违规"

    # 重启恢复: 任务表清空后, results 回放 sqlite 历史 (每规则最新) 按 done 返回
    routes_audit._2c_tasks.clear()
    body2 = client.get("/api/audit/results/J66252").json()
    assert body2["status"] == "done"
    assert body2["outcome"] == "succeeded"
    assert body2["attempt_id"] is None  # 重启后的 legacy/history replay 无 attempt
    assert body2["summary"]["total"] == 1
    assert body2["results"][0]["run_id"] == "aud_TESTtest0001"


def test_v2_multiple_hits_keep_code_name_time_aligned(client):
    """一个卡片多个项目；同项目多日期按 occurrence_time 展开且三数组同索引。"""
    from javert.audit.result import AuditResult, Evidence
    from javert.config import get_config
    from javert.store.audit_store import SqliteStore
    from javert.web.api import routes_audit

    result = AuditResult(
        run_id="aud_V2_MULTI_001",
        rule_id="R191",
        patient_id="CASE-V2-001",
        verdict="VIOLATION",
        confidence=0.88,
        reasoning="费用明细支持多个命中项目。",
        evidence=[
            Evidence(
                source="search_fees",
                locator="麻醉后复苏监护(PACU)",
                text="费用明细出现麻醉后复苏监护(PACU)。",
            ),
            Evidence(
                source="search_fees",
                locator="静脉输液",
                text="费用明细出现静脉输液。",
            ),
        ],
        duration_ms=1000,
        model="test-model",
        started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )
    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    store.write(result)
    store.close()
    routes_audit._2c_tasks["CASE-V2-001"] = {
        "YLZZJGDM": "H-SYNTHETIC",
        "status": "done",
        "outcome": "succeeded",
        "attempt_id": "att_v2_multi",
        "source": "local",
        "stage": "done",
        "total": 1,
        "run_ids": [result.run_id],
        "failed": [],
        "error_code": "",
        "retryable": False,
        "submitted_at": "2026-07-24T00:00:00+00:00",
    }

    response = client.get("/api/audit/v2/results/CASE-V2-001")

    assert response.status_code == 200
    body = response.json()
    assert body["api_version"] == "2.0"
    assert "results" not in body
    (card,) = body["cards"]
    assert card["category"] == {"code": "T380301", "title": "重复收费"}
    assert set(card["public_explanation"]) == {
        "conclusion", "narrative", "audit_items", "charge_facts", "basis",
        "clinical_evidence", "review_needs",
    }
    assert "promise" in card
    assert len(card["matched_items"]) == 3
    assert card["hit_codes"] == [
        item["code"] for item in card["matched_items"]
    ]
    assert card["hit_names"] == [
        item["name"] for item in card["matched_items"]
    ]
    assert card["hit_times"] == [
        item["occurrence_time"] for item in card["matched_items"]
    ]
    assert card["hit_times"] == [
        "2026-07-01 08:30:00",
        "2026-07-02 09:45:00",
        "2026-07-03 10:00:00",
    ]

    v2_before = body
    v3 = client.get("/api/audit/v3/results/CASE-V2-001").json()
    assert v3["api_version"] == "3.0"
    assert "results" not in v3
    (v3_card,) = v3["cards"]
    assert v3_card["public_explanation"] == card["public_explanation"]
    assert v3_card["promise"] == card["promise"]
    assert len(v3_card["matched_items"]) == 3
    first = v3_card["matched_items"][0]
    assert first["quantity"] == 1
    assert first["unit_price"] == 300
    assert first["ordering_department_code"] == "D001"
    assert first["ordering_department_name"] == "麻醉科"
    assert first["ordering_doctor_id"] == "DR001"
    assert first["ordering_doctor_name"] == "医生甲"
    assert v3_card["hit_codes"] == [x["code"] for x in v3_card["matched_items"]]
    assert v3_card["hit_names"] == [x["name"] for x in v3_card["matched_items"]]
    assert v3_card["hit_times"] == [
        x["occurrence_time"] for x in v3_card["matched_items"]
    ]
    assert client.get("/api/audit/v2/results/CASE-V2-001").json() == v2_before


def test_v3_expands_same_item_time_to_source_rows_without_dedup():
    """同项目同时间逐收费行返回；即使展示字段相同也不得静默去重。"""
    from javert.web.api.routes_audit import _v3_matched_items

    fee_df = pd.DataFrame({
        "medins_list_name": ["语义化收费项目"] * 3,
        "med_list_codg": ["SYNTHETIC-CODE"] * 3,
        "medins_list_codg": ["LOCAL-CODE"] * 3,
        "fee_ocur_time": ["2026-07-27 08:00:00"] * 3,
        "cnt": [1, 2.5, 2.5],
        "pric": [10, 20.25, 20.25],
        "acord_dept_codg": ["D001", "D002", "D002"],
        "acord_dept_name": ["科室甲", "科室乙", "科室乙"],
        "orders_dr_code": ["DR001", "DR002", "DR002"],
        "orders_dr_name": ["医生甲", "医生乙", "医生乙"],
    })
    base = {
        "code": "SYNTHETIC-CODE",
        "name": "语义化收费项目",
        "occurrence_time": "2026-07-27 08:00:00",
        "source": "fee",
        "code_nat": "SYNTHETIC-CODE",
        "code_local": "LOCAL-CODE",
        "matched_fee_name": "语义化收费项目",
        "restriction": "",
        "review_note": "",
    }

    expanded = _v3_matched_items([base], fee_df)

    assert len(expanded) == 3
    assert [x["quantity"] for x in expanded] == [1, 2.5, 2.5]
    assert [x["unit_price"] for x in expanded] == [10, 20.25, 20.25]
    assert [x["ordering_department_code"] for x in expanded] == [
        "D001", "D002", "D002",
    ]
    assert expanded[1] == expanded[2]  # 相同展示行仍保留两个数组元素


def test_v3_missing_charge_fields_keep_item_with_stable_types():
    from javert.web.api.routes_audit import _v3_matched_items

    fee_df = pd.DataFrame({
        "medins_list_name": ["语义化收费项目"],
        "med_list_codg": ["SYNTHETIC-CODE"],
        "fee_ocur_time": ["2026-07-27 08:00:00"],
        "cnt": ["not-a-number"],
        "pric": [""],
    })
    base = {
        "code": "SYNTHETIC-CODE",
        "name": "语义化收费项目",
        "occurrence_time": "2026-07-27 08:00:00",
        "source": "fee",
        "code_nat": "SYNTHETIC-CODE",
        "code_local": "",
        "matched_fee_name": "语义化收费项目",
        "restriction": "",
        "review_note": "",
    }

    (item,) = _v3_matched_items([base], fee_df)

    assert item["quantity"] is None
    assert item["unit_price"] is None
    assert item["ordering_department_code"] == ""
    assert item["ordering_department_name"] == ""
    assert item["ordering_doctor_id"] == ""
    assert item["ordering_doctor_name"] == ""


def test_v2_time_is_normalized_and_unmatched_search_term_is_not_a_hit(client):
    """斜杠日期统一格式；仅检索过但无实际费用行的 PTCA 不得冒充命中项。"""
    from javert.web.api.routes_audit import _v2_hit_payloads
    from javert.web.hit_resolver import Anchor, HitItem

    fee_df = pd.DataFrame({
        "medins_list_name": ["语义化收费项目"],
        "med_list_codg": ["SYNTHETIC-CODE"],
        "medins_list_codg": ["LOCAL-CODE"],
        "fee_ocur_time": ["8/1/2025 00:00:00"],
        "cnt": [1],
    })
    actual = HitItem(
        source="fee",
        name="语义化收费项目",
        code_nat="SYNTHETIC-CODE",
        code_local="LOCAL-CODE",
        matched_fee_name="语义化收费项目",
        anchor=Anchor(tab="fees", query="语义化收费项目"),
    )
    searched_only = HitItem(
        source="fee",
        name="PTCA",
        anchor=Anchor(tab="fees", query="PTCA", match_level="name"),
    )

    hits, matched_items = _v2_hit_payloads([actual, searched_only], fee_df)

    assert len(hits) == 2  # 原始追溯锚点仍保留
    assert matched_items == [{
        "code": "SYNTHETIC-CODE",
        "name": "语义化收费项目",
        "occurrence_time": "2025-08-01 00:00:00",
        "source": "fee",
        "code_nat": "SYNTHETIC-CODE",
        "code_local": "LOCAL-CODE",
        "matched_fee_name": "语义化收费项目",
        "restriction": "",
        "review_note": "",
    }]


def test_v2_precheck_not_applicable_is_not_labeled_compliant(client):
    """底层仍是 CLEAN，但 C 端展示应明确为“不适用”而非普通“合规”。"""
    from javert.audit.result import AuditResult
    from javert.config import get_config
    from javert.store.audit_store import SqliteStore
    from javert.web.api import routes_audit

    result = AuditResult(
        run_id="aud_V2NOTAPP0001",
        rule_id="R191",
        patient_id="CASE-V2-001",
        verdict="CLEAN",
        confidence=1.0,
        reasoning="预检: 未见 A 类 (主项) 费用命中, 规则不适用 → CLEAN",
        duration_ms=1,
        model="deterministic-precheck",
        started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )
    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    store.write(result)
    store.close()
    routes_audit._2c_tasks["CASE-V2-001"] = {
        "YLZZJGDM": "H-SYNTHETIC",
        "status": "done",
        "outcome": "succeeded",
        "attempt_id": "att_v2_not_applicable",
        "source": "local",
        "stage": "done",
        "total": 1,
        "run_ids": [result.run_id],
        "failed": [],
        "error_code": "",
        "retryable": False,
        "submitted_at": "2026-07-24T00:00:00+00:00",
    }

    (card,) = client.get("/api/audit/v2/results/CASE-V2-001").json()["cards"]

    assert card["verdict"] == "CLEAN"
    assert card["handling_level"] == "违规（阻断）"
    assert card["rule"]["handling_level"] == "违规（阻断）"
    assert card["verdict_label"] == "不适用"
    assert card["applicability"] == "NOT_APPLICABLE"
    assert card["applicability_label"] == "不适用"

    (v3_card,) = client.get(
        "/api/audit/v3/results/CASE-V2-001"
    ).json()["cards"]
    assert v3_card["rule_id"] == "R191"
    assert v3_card["handling_level"] == "违规（阻断）"
    assert v3_card["rule"]["handling_level"] == "违规（阻断）"
    assert v3_card["matched_items"] == []
    assert v3_card["hit_codes"] == []
    assert v3_card["hit_names"] == []
    assert v3_card["hit_times"] == []


def test_v2_returns_all_39_completed_cards(client, caplog):
    """完成列表有 39 条时，v2 不得静默截断或按 verdict 过滤。"""
    from javert.audit.result import AuditResult
    from javert.config import get_config
    from javert.store.audit_store import SqliteStore
    from javert.web.api import routes_audit

    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    results = [
        AuditResult(
            run_id=f"aud_V2FULL{index:06d}",
            rule_id=f"R{index:03d}",
            patient_id="CASE-V2-001",
            verdict="CLEAN",
            confidence=1.0,
            reasoning="确定性核查完成。",
            duration_ms=1,
            model="deterministic-test",
            started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        for index in range(1, 40)
    ]
    for result in results:
        store.write(result)
    store.close()
    routes_audit._2c_tasks["CASE-V2-001"] = {
        "YLZZJGDM": "H-SYNTHETIC",
        "status": "done",
        "outcome": "succeeded",
        "attempt_id": "att_v2_full_39",
        "source": "local",
        "stage": "done",
        "total": 39,
        "run_ids": [result.run_id for result in results],
        "failed": [],
        "error_code": "",
        "retryable": False,
        "submitted_at": "2026-07-24T00:00:00+00:00",
    }

    import logging

    caplog.set_level(logging.INFO, logger="uvicorn.error")
    body = client.get("/api/audit/v2/results/CASE-V2-001").json()

    assert body["progress"] == {"total": 39, "completed": 39, "failed": 0}
    assert body["summary"]["total"] == 39
    assert len(body["cards"]) == 39
    (message,) = [
        record.getMessage()
        for record in caplog.records
        if "2c_v2_outbound" in record.getMessage()
    ]
    assert "total=39 completed=39 failed=0 v1_results=39 cards=39" in message
    assert "cache_hits=0 cache_misses=39 build_ms=" in message
    assert "CASE-V2-001" not in message
    assert "att_v2_full_39" not in message
    assert "aud_V2FULL" not in message

    v3 = client.get("/api/audit/v3/results/CASE-V2-001").json()
    assert v3["api_version"] == "3.0"
    assert v3["status"] == "done"
    assert v3["outcome"] == "succeeded"
    assert v3["progress"] == {"total": 39, "completed": 39, "failed": 0}
    assert len(v3["cards"]) == 39


def test_v3_running_response_is_incremental(client):
    """running 时 cards 是已完成部分，progress 明确总数与完成数。"""
    from javert.audit.result import AuditResult
    from javert.config import get_config
    from javert.store.audit_store import SqliteStore
    from javert.web.api import routes_audit

    result = AuditResult(
        run_id="aud_V3RUNNING001",
        rule_id="R191",
        patient_id="CASE-V2-001",
        verdict="CLEAN",
        confidence=1.0,
        reasoning="确定性核查完成。",
        duration_ms=1,
        model="deterministic-test",
        started_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
    )
    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    store.write(result)
    store.close()
    routes_audit._2c_tasks["CASE-V2-001"] = {
        "YLZZJGDM": "H-SYNTHETIC",
        "status": "running",
        "outcome": "running",
        "attempt_id": "att_v3_running",
        "source": "local",
        "stage": "audit",
        "total": 39,
        "run_ids": [result.run_id],
        "failed": [],
        "error_code": "",
        "retryable": False,
        "submitted_at": "2026-07-27T00:00:00+00:00",
    }

    body = client.get("/api/audit/v3/results/CASE-V2-001").json()

    assert body["status"] == "running"
    assert body["outcome"] == "running"
    assert body["progress"] == {"total": 39, "completed": 1, "failed": 0}
    assert len(body["cards"]) == 1


def test_v2_clean_drug_keeps_hit_and_full_oncology_conditions(client):
    """CLEAN RD04 仍返回被审核肿瘤药和结构化限定条件，并清理占位文案。"""
    from javert.audit.result import AuditResult, Evidence
    from javert.config import get_config
    from javert.oncology.contracts import (
        AuditDisposition,
        CriterionAssessment,
        CriterionState,
        EligibilityEvaluation,
        EligibilityStatus,
        ProofNode,
    )
    from javert.store.audit_store import SqliteStore
    from javert.web.api import routes_audit

    assessment = CriterionAssessment(
        criterion_id="synthetic-diagnosis",
        criterion_type="diagnosis",
        state=CriterionState.SATISFIED,
        reason="已记录目标诊断，暂未描述其他无关内容。",
    )
    evaluation = EligibilityEvaluation(
        audit_disposition=AuditDisposition.NO_VIOLATION_FOUND,
        eligibility_status=EligibilityStatus.SATISFIED,
        rule_id="synthetic-oncology-rule",
        rule_version="1.0.0",
        indication_branch_id="synthetic-branch",
        criterion_assessments=[assessment],
        proof_tree=ProofNode(
            node_id="synthetic-root",
            operator="leaf",
            state=CriterionState.SATISFIED,
            criterion_id=assessment.criterion_id,
            criterion_type=assessment.criterion_type,
            assessment=assessment,
        ),
    )
    result = AuditResult(
        run_id="aud_V2CLEAN00001",
        rule_id="RD04",
        patient_id="CASE-V2-001",
        verdict="CLEAN",
        confidence=1.0,
        reasoning="暂未描述",
        evidence=[
            Evidence(
                source="drug_indication",
                locator="注射用维泊妥珠单抗",
                text="命中患者净正收费药品。",
            )
        ],
        duration_ms=500,
        model="deterministic-test",
        started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        eligibility_evaluation=evaluation,
    )
    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    store.write(result)
    store.close()
    routes_audit._2c_tasks["CASE-V2-001"] = {
        "YLZZJGDM": "H-SYNTHETIC",
        "status": "done",
        "outcome": "succeeded",
        "attempt_id": "att_v2_clean",
        "source": "local",
        "stage": "done",
        "total": 1,
        "run_ids": [result.run_id],
        "failed": [],
        "error_code": "",
        "retryable": False,
        "submitted_at": "2026-07-24T00:00:00+00:00",
    }

    body = client.get("/api/audit/v2/results/CASE-V2-001").json()

    assert body["summary"]["clean"] == 1
    (card,) = body["cards"]
    assert card["verdict"] == "CLEAN"
    assert card["matched_items"][0]["name"] == "注射用维泊妥珠单抗"
    assert card["matched_items"][0]["occurrence_time"] == "2026-07-04 11:15:00"
    assert card["eligibility_evaluation"]["criterion_assessments"][0][
        "criterion_id"
    ] == "synthetic-diagnosis"
    assert card["eligibility_evaluation"]["proof_tree"]["node_id"] == "synthetic-root"
    assert "暂未描述" not in json.dumps(body, ensure_ascii=False)


def test_running_duplicate_submit_reuses_attempt(client, monkeypatch):
    """running 幂等提交复用当前 attempt，不能重复入队。"""
    from javert.web.api import routes_audit

    routes_audit._2c_tasks["J66252"] = {
        "YLZZJGDM": "H31010600042",
        "status": "running",
        "outcome": "running",
        "attempt_id": "att_existing0001",
        "source": "local",
        "stage": "audit",
        "total": 2,
        "run_ids": [],
        "failed": [],
        "submitted_at": "2026-07-22T00:00:00+00:00",
    }
    queued: list[str] = []
    monkeypatch.setattr(routes_audit._2c_queue, "put", queued.append)

    body = client.post("/api/audit/submit", json=[{"SYXH": "J66252"}]).json()

    assert body["accepted"] == [{
        "SYXH": "J66252", "source": "local", "attempt_id": "att_existing0001",
    }]
    assert queued == []


def test_running_duplicate_submit_does_not_reprobe_sources(client, monkeypatch):
    """running 幂等命中应先于数据源探测，避免瞬时连接故障把重复提交驳回。"""
    from javert.web.api import routes_audit

    routes_audit._2c_tasks["J66252"] = {
        "YLZZJGDM": "H31010600042",
        "status": "running",
        "outcome": "running",
        "attempt_id": "att_existing0002",
        "source": "hub",
        "stage": "hub_fetch",
        "total": None,
        "run_ids": [],
        "failed": [],
        "submitted_at": "2026-07-22T00:00:00+00:00",
    }

    def _must_not_probe():
        raise AssertionError("running duplicate must not load data source")

    monkeypatch.setattr(routes_audit, "_get_loader", _must_not_probe)
    body = client.post("/api/audit/submit", json=[{"SYXH": "J66252"}]).json()

    assert body["accepted"] == [{
        "SYXH": "J66252", "source": "hub", "attempt_id": "att_existing0002",
    }]
    assert body["rejected"] == []


def test_all_rule_failures_have_failed_outcome(client, monkeypatch):
    """所有入选规则均失败时没有成功子结果，不能标为 partial。"""
    from javert.web.api import routes_audit

    def _fail_every_rule(syxh: str) -> None:
        with routes_audit._2c_lock:
            task = routes_audit._2c_tasks[syxh]
            task["stage"] = "audit"
            task["total"] = 2
            task["failed"].extend(["R020", "R232"])

    monkeypatch.setattr(routes_audit, "_2c_run_patient", _fail_every_rule)
    client.post("/api/audit/submit", json=[{"SYXH": "J66252"}])
    body = _wait_done(client, "J66252")

    assert body["status"] == "done"
    assert body["outcome"] == "failed"
    assert body["error_code"] == "RULE_FAILURES"
    assert body["retryable"] is True
    assert body["results"] == []
    assert body["progress"] == {"total": 2, "completed": 0, "failed": 2}


def test_patient_level_failure_has_terminal_outcome(client, monkeypatch):
    """患者级 Hub 异常保持 status 兼容，但必须显式 outcome=failed。"""
    from javert.web.api import routes_audit

    def _fail_in_hub(syxh: str) -> None:
        with routes_audit._2c_lock:
            routes_audit._2c_tasks[syxh]["stage"] = "hub_fetch"
        raise RuntimeError("synthetic hub failure")

    monkeypatch.setattr(routes_audit, "_2c_run_patient", _fail_in_hub)
    accepted = client.post(
        "/api/audit/submit", json=[{"SYXH": "J66252"}]
    ).json()["accepted"][0]
    body = _wait_done(client, "J66252")

    assert body["attempt_id"] == accepted["attempt_id"]
    assert body["status"] == "done"
    assert body["outcome"] == "failed"
    assert body["error_code"] == "HUB_FETCH_FAILED"
    assert body["retryable"] is True
    assert body["results"] == []
    assert body["progress"] == {"total": 0, "completed": 0, "failed": 0}
    assert body["error"] == "审计中断: RuntimeError"


def test_results_partial_and_malformed_diagnostic(client):
    """单规则失败返回 partial；malformed 对外为中文且带稳定诊断码。"""
    from javert.audit.result import AuditResult
    from javert.config import get_config
    from javert.store.audit_store import SqliteStore
    from javert.web.api import routes_audit

    result = AuditResult(
        run_id="aud_MALFORMED001",
        rule_id="R020",
        patient_id="J66252",
        verdict="INCONCLUSIVE",
        confidence=0.0,
        reasoning="malformed verdict JSON (repair failed)",
        evidence=[],
        duration_ms=100,
        model="test-model",
        started_at=datetime(2026, 7, 22, 8, 0, 0, tzinfo=timezone.utc),
    )
    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    store.write(result)
    store.close()
    routes_audit._2c_tasks["J66252"] = {
        "YLZZJGDM": "H31010600042",
        "status": "done",
        "outcome": "partial",
        "attempt_id": "att_partial0001",
        "source": "local",
        "stage": "done",
        "total": 2,
        "run_ids": [result.run_id],
        "failed": ["R232"],
        "error_code": "RULE_FAILURES",
        "retryable": True,
        "submitted_at": "2026-07-22T08:00:00+00:00",
    }

    body = client.get("/api/audit/results/J66252").json()

    assert body["outcome"] == "partial"
    assert body["progress"] == {"total": 2, "completed": 1, "failed": 1}
    assert body["error_code"] == "RULE_FAILURES"
    assert body["retryable"] is True
    item = body["results"][0]
    assert item["diagnostic_code"] == "LLM_OUTPUT_MALFORMED"
    assert item["retryable"] is True
    assert item["reasoning"] == "模型输出格式异常，本规则未完成自动判定，需人工复核。"
    assert "malformed" not in item["reasoning"]

    # 即使 task 本身执行成功，存在可重试的规则诊断也要上卷到顶层。
    routes_audit._2c_tasks["J66252"].update({
        "outcome": "succeeded",
        "total": 1,
        "failed": [],
        "error_code": "",
        "retryable": False,
    })
    body2 = client.get("/api/audit/results/J66252").json()
    assert body2["outcome"] == "succeeded"
    assert body2["retryable"] is True


def test_truncated_diagnostic_is_chinese_and_retryable():
    from javert.web.api.routes_audit import _2c_result_diagnostic

    reasoning, code, retryable = _2c_result_diagnostic(
        "LLM output truncated (repair failed)"
    )
    assert reasoning == "模型输出达到长度上限，本规则未完成自动判定，需人工复核。"
    assert code == "LLM_OUTPUT_TRUNCATED"
    assert retryable is True


def _seed_projection_result(
    run_id: str,
    *,
    rule_id: str = "R191",
    evidence: list | None = None,
    verdict: str = "CLEAN",
) -> None:
    """为投影缓存测试写入一条不触发 v1 hit 解析的 CLEAN run。"""
    from javert.audit.result import AuditResult
    from javert.config import get_config
    from javert.store.audit_store import SqliteStore

    result = AuditResult(
        run_id=run_id,
        rule_id=rule_id,
        patient_id="CASE-V2-001",
        verdict=verdict,
        confidence=1.0,
        reasoning="确定性核查完成。",
        evidence=evidence or [],
        duration_ms=1,
        model="deterministic-test",
        started_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    store = SqliteStore(get_config().audit_db_path)
    store.init_schema()
    store.write(result)
    store.close()


def _set_projection_task(attempt_id: str, run_ids: list[str]) -> None:
    from javert.web.api import routes_audit

    routes_audit._2c_tasks["CASE-V2-001"] = {
        "YLZZJGDM": "H-SYNTHETIC",
        "status": "running",
        "outcome": "running",
        "attempt_id": attempt_id,
        "source": "local",
        "stage": "audit",
        "total": 2,
        "run_ids": list(run_ids),
        "failed": [],
        "error_code": "",
        "retryable": False,
        "submitted_at": "2026-07-28T00:00:00+00:00",
    }


def test_v2_projection_cache_reuses_old_cards_and_builds_only_increment(client, monkeypatch):
    """同 attempt 重复轮询不重算旧 run，进度增加时只构建新增 run。"""
    from javert.web.api import routes_audit

    _seed_projection_result("aud_CACHE_V2_001", rule_id="R191")
    _seed_projection_result("aud_CACHE_V2_002", rule_id="R020")
    _set_projection_task("att_cache_v2", ["aud_CACHE_V2_001"])

    actual = routes_audit.resolve_hits_from_json
    calls = 0

    def _counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return actual(*args, **kwargs)

    monkeypatch.setattr(routes_audit, "resolve_hits_from_json", _counted)

    first = client.get("/api/audit/v2/results/CASE-V2-001").json()
    repeated = client.get("/api/audit/v2/results/CASE-V2-001").json()
    assert first == repeated
    assert len(first["cards"]) == 1
    assert calls == 1

    with routes_audit._2c_lock:
        routes_audit._2c_tasks["CASE-V2-001"]["run_ids"].append(
            "aud_CACHE_V2_002"
        )
    incremented = client.get("/api/audit/v2/results/CASE-V2-001").json()
    assert len(incremented["cards"]) == 2
    assert calls == 2
    client.get("/api/audit/v2/results/CASE-V2-001")
    assert calls == 2


def test_v2_projection_does_not_build_discarded_v1_hits(client, monkeypatch):
    """v2 不应先计算一遍不会返回的 v1 hits，再重复构建完整 card。"""
    from javert.audit.result import Evidence
    from javert.web.api import routes_audit

    _seed_projection_result(
        "aud_NODUPHITS001",
        verdict="VIOLATION",
        evidence=[Evidence(
            source="search_fees",
            locator="麻醉后复苏监护(PACU)",
            text="费用明细出现麻醉后复苏监护(PACU)。",
        )],
    )
    _set_projection_task("att_no_duplicate_v1_hits", ["aud_NODUPHITS001"])
    actual = routes_audit.resolve_hits_from_json
    calls = 0

    def _counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return actual(*args, **kwargs)

    monkeypatch.setattr(routes_audit, "resolve_hits_from_json", _counted)
    body = client.get("/api/audit/v2/results/CASE-V2-001").json()

    assert len(body["cards"]) == 1
    assert calls == 1


def test_history_replay_without_attempt_reuses_projection_cache(client, monkeypatch):
    """服务重启后 attempt 丢失，SQLite 历史快照仍须复用卡片投影。"""
    from javert.web.api import routes_audit

    _seed_projection_result("aud_HISTCACHE001")
    routes_audit._2c_tasks.clear()
    actual = routes_audit.resolve_hits_from_json
    calls = 0

    def _counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return actual(*args, **kwargs)

    monkeypatch.setattr(routes_audit, "resolve_hits_from_json", _counted)
    first = client.get("/api/audit/v2/results/CASE-V2-001").json()
    repeated = client.get("/api/audit/v2/results/CASE-V2-001").json()

    assert first == repeated
    assert first["attempt_id"] is None
    assert len(first["cards"]) == 1
    assert calls == 1


def test_v3_projection_cache_is_version_and_attempt_isolated(client, monkeypatch):
    """v3 重复查询复用收费行投影，且不污染 v2 或新 attempt。"""
    from javert.audit.result import Evidence
    from javert.web.api import routes_audit

    _seed_projection_result(
        "aud_CACHE_V3_001",
        evidence=[Evidence(
            source="search_fees",
            locator="麻醉后复苏监护(PACU)",
            text="费用明细出现麻醉后复苏监护(PACU)。",
        )],
    )
    _set_projection_task("att_cache_v3_first", ["aud_CACHE_V3_001"])

    actual = routes_audit._v3_matched_items
    calls = 0

    def _counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return actual(*args, **kwargs)

    monkeypatch.setattr(routes_audit, "_v3_matched_items", _counted)

    first = client.get("/api/audit/v3/results/CASE-V2-001").json()
    repeated = client.get("/api/audit/v3/results/CASE-V2-001").json()
    assert first == repeated
    assert calls == 1

    v2 = client.get("/api/audit/v2/results/CASE-V2-001").json()
    assert all(
        "quantity" not in item
        for card in v2["cards"]
        for item in card["matched_items"]
    )

    with routes_audit._2c_lock:
        routes_audit._2c_tasks["CASE-V2-001"]["attempt_id"] = (
            "att_cache_v3_second"
        )
    client.get("/api/audit/v3/results/CASE-V2-001")
    assert calls == 2


def test_same_attempt_concurrent_v2_projection_is_single_flight(client, monkeypatch):
    """并发缓存缺失时，昂贵 hit 投影只执行一次。"""
    from javert.web.api import routes_audit

    _seed_projection_result("aud_CCONCUR00001")
    _set_projection_task("att_cache_concurrent", ["aud_CCONCUR00001"])

    actual = routes_audit.resolve_hits_from_json
    calls = 0
    calls_lock = threading.Lock()

    def _slow_counted(*args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.1)
        return actual(*args, **kwargs)

    monkeypatch.setattr(routes_audit, "resolve_hits_from_json", _slow_counted)
    start = threading.Barrier(3)

    def _get():
        start.wait()
        return routes_audit.results_2c_v2("CASE-V2-001")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_get) for _ in range(2)]
        start.wait()
        payloads = [future.result(timeout=3) for future in futures]

    assert payloads[0] == payloads[1]
    assert calls == 1


def test_projection_card_cache_is_bounded_lru(client, monkeypatch):
    """超出上限淘汰最久未使用卡片，不改变重新构建能力。"""
    from javert.web.api import routes_audit

    routes_audit._clear_2c_card_cache()
    monkeypatch.setattr(routes_audit, "_2C_CARD_CACHE_MAX", 2)
    routes_audit._2c_card_cache_put("2.0", "att_lru", "run_1", {"n": 1})
    routes_audit._2c_card_cache_put("2.0", "att_lru", "run_2", {"n": 2})
    assert routes_audit._2c_card_cache_get("2.0", "att_lru", "run_1") == {"n": 1}
    routes_audit._2c_card_cache_put("2.0", "att_lru", "run_3", {"n": 3})

    assert routes_audit._2c_card_cache_get("2.0", "att_lru", "run_2") is None
    assert routes_audit._2c_card_cache_get("2.0", "att_lru", "run_1") == {"n": 1}
    assert routes_audit._2c_card_cache_get("2.0", "att_lru", "run_3") == {"n": 3}
