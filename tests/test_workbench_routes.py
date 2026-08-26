# -*- coding: utf-8 -*-
"""tests for routes_workbench 纯逻辑 (redesign-review-card-and-source):
fee_date 格式 / 细类分组 V前I后 / /raw 文书分桶排序 + fee_date. 不连 142 / 不走 HTTP auth.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from javert.store.models import RunWithReviews
from javert.web.api import routes_workbench as workbench_routes
from javert.web.api.routes_workbench import (
    _fmt_fee_date,
    _group_runs_by_violation_type,
    _raw_payload,
)


# =========================================================
# fee_date 格式 (dd/mm/yyyy → YYYY/MM/DD, 多格式 fallback)
# =========================================================
def test_fmt_fee_date_dd_mm_yyyy():
    # spec 场景: 05/03/2024 (dd/mm/yyyy) → 2024/03/05
    assert _fmt_fee_date("05/03/2024") == "2024/03/05"
    # 带时间部分 (实数据形态 3/8/2024 00:00:00)
    assert _fmt_fee_date("3/8/2024 00:00:00") == "2024/08/03"


def test_fmt_fee_date_iso_and_slash():
    assert _fmt_fee_date("2024-03-05") == "2024/03/05"
    assert _fmt_fee_date("2024/03/05") == "2024/03/05"


def test_fmt_fee_date_empty_and_garbage():
    assert _fmt_fee_date("") == ""
    assert _fmt_fee_date(None) == ""
    # 解析失败 → 回退原日期段 (不抛)
    assert _fmt_fee_date("不是日期") == "不是日期"


# =========================================================
# 细类分组 — V 前 I 后, 计数, 锚点, 缺 meta 归未分类
# =========================================================
def _mkrun(rid, verdict):
    return RunWithReviews(
        run_id="aud_" + rid, rule_id=rid, patient_id="P",
        verdict=verdict, confidence=0.5, reasoning="",
        created_at=datetime.now(timezone.utc),
    )


def test_group_runs_orders_v_before_i_and_counts():
    runs = [
        _mkrun("R1", "INCONCLUSIVE"),
        _mkrun("R2", "VIOLATION"),
        _mkrun("R3", "VIOLATION"),
    ]
    meta = {
        "R1": {"violation_type": "过度检查", "behavior_code": "T380202", "behavior_name": "过度检查"},
        "R2": {"violation_type": "过度检查", "behavior_code": "T380202", "behavior_name": "过度检查"},
        "R3": {"violation_type": "重复收费", "behavior_code": "T380301", "behavior_name": "重复收费"},
    }
    groups = _group_runs_by_violation_type(runs, meta)
    assert len(groups) == 2
    g0 = groups[0]
    # 组按首次出现序 (R1 先 → 过度检查 在前)
    assert g0["vt"] == "过度检查"
    assert g0["alias"] == "过度检查"
    assert g0["anchor"] == "vt-0"
    assert g0["n_v"] == 1 and g0["n_i"] == 1
    # 组内 V 前 I 后 (R2=V 排在 R1=I 前)
    assert [r.rule_id for r in g0["runs"]] == ["R2", "R1"]
    assert groups[1]["vt"] == "重复收费"
    assert groups[1]["anchor"] == "vt-1"
    assert groups[1]["n_v"] == 1 and groups[1]["n_i"] == 0


def test_group_runs_missing_meta_falls_to_unclassified():
    groups = _group_runs_by_violation_type([_mkrun("RX", "VIOLATION")], {})
    assert len(groups) == 1
    assert groups[0]["vt"] == "未分类"
    assert groups[0]["alias"] == "未分类"


def test_model_comparison_store_returns_full_latest_model_rows(monkeypatch):
    import json

    from javert.store.sqlserver_store import SqlServerStore

    rows = [(
        "aud_compare001", "R191", "CASE-AB-001", "CLEAN", 0.9,
        "合成推理", json.dumps([{"source": "note", "text": "合成证据"}]),
        json.dumps([{"tool_name": "search_notes", "arguments": {}}]),
        1234, "Qwen/Qwen3.8-27B-FP8", datetime.now(timezone.utc),
        "ab3.8", "", "合成收费项目核查未发现违规，现有事实充分",
    )]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, params):
            assert "PARTITION BY rule_id, model" in str(statement)
            assert params == {"pid": "CASE-AB-001", "tag": "ab3.8"}
            return SimpleNamespace(fetchall=lambda: rows)

    store = SqlServerStore()
    monkeypatch.setattr(store, "get_engine", lambda: SimpleNamespace(connect=Connection))
    out = store.list_model_comparison_runs("CASE-AB-001", "ab3.8")
    assert out[0]["model"] == "Qwen/Qwen3.8-27B-FP8"
    assert out[0]["evidence"][0]["text"] == "合成证据"
    assert out[0]["headline"] == "合成收费项目核查未发现违规，现有事实充分"
    assert out[0]["tool_calls"][0]["tool_name"] == "search_notes"


def test_group_runs_alias_compresses_long_violation_type():
    runs = [_mkrun("R1", "VIOLATION")]
    meta = {"R1": {
        "violation_type": "虚构医药服务项目或以骗保为目的串换项目",
        "behavior_code": "T380206",
        "behavior_name": "提供不必要的医药服务",
    }}
    groups = _group_runs_by_violation_type(runs, meta)
    # behavior-naming: 展示名 = 行为认定名称 (behavior_names.yaml), 不再是压缩短词
    assert groups[0]["alias"] == "提供不必要的医药服务"


# =========================================================
# /raw 端点 — 文书分桶 + bucket_order 升序 + fee_date 预格式化
# (直接调路由函数, 绕过 HTTP auth; 合成 loader 避免依赖本机病例 CSV)
# =========================================================
@pytest.fixture
def raw_payload(monkeypatch):
    loader = SimpleNamespace(
        get_notes=lambda _pid: pd.DataFrame({
            "阶段": ["出院记录", "日常病程记录", "入院记录", "首次病程记录"],
            "内容": ["出院", "较晚病程", "入院", "较早病程"],
            "事件时间": [
                "2024-03-04 09:00:00", "2024-03-03 09:00:00",
                "2024-03-01 09:00:00", "2024-03-02 09:00:00",
            ],
        }),
        get_fees=lambda _pid: pd.DataFrame({
            "medins_list_name": ["合成费用甲", "合成费用乙"],
            "fee_ocur_time": ["05/03/2024 08:00:00", "2024-03-06"],
        }),
    )
    lab_loader = SimpleNamespace(get_lab_results=lambda _pid: [{
        "report_dt": "07/03/2024", "rpt_itemname": "血红蛋白",
        "result": "132", "result_flag": "正常",
    }])
    exam_loader = SimpleNamespace(get_examinations=lambda _pid: [{
        "reportDate": "2024-03-08", "checkType": "CT",
        "checkItemName": "胸部 CT", "checkConclusion": "未见明显异常",
    }])

    monkeypatch.setattr(workbench_routes, "_get_loader", lambda: loader)
    monkeypatch.setattr(
        workbench_routes, "_get_lab_loader", lambda: lab_loader,
    )
    monkeypatch.setattr(
        workbench_routes, "_get_exam_loader", lambda: exam_loader,
    )
    monkeypatch.setattr(
        workbench_routes, "_get_main_diagnosis", lambda patient_id: "合成主诊断",
    )
    return _raw_payload("P-SYNTH")


def test_raw_endpoint_notes_bucketed_and_sorted(raw_payload):
    data = raw_payload
    notes = data["notes"]
    assert notes, "合成患者应有文书"
    # 每条带 bucket + bucket_order
    assert all("bucket" in n and "bucket_order" in n for n in notes)
    # 输出按 bucket_order 升序 (临床文书序分组的前提)
    orders = [n["bucket_order"] for n in notes]
    assert orders == sorted(orders), "notes 应按 bucket_order 升序排列"
    assert [n["bucket"] for n in notes] == [
        "入院记录", "病程记录", "病程记录", "出院小结",
    ]
    # 同一 bucket 内按 事件时间 升序 (抽第一个非空 bucket 验证)
    from itertools import groupby
    for _b, grp in groupby(notes, key=lambda n: n["bucket_order"]):
        ts = [n.get("ts", "") for n in grp]
        assert ts == sorted(ts), "同桶内应按事件时间升序"


def test_raw_endpoint_fees_have_formatted_date(raw_payload):
    data = raw_payload
    fees = data["fees"]
    assert fees, "合成患者应有费用"
    assert all("fee_date" in f for f in fees)
    dated = [f["fee_date"] for f in fees if f["fee_date"]]
    assert dated, "至少部分 fee 应有可解析日期"
    assert all(re.match(r"^\d{4}/\d{2}/\d{2}$", d) for d in dated), \
        "fee_date 应为 YYYY/MM/DD"
    assert dated == ["2024/03/05", "2024/03/06"]


# =========================================================
# /raw 端点 — 检验(化验) + 检查(影像) 数据 (新增检验记录 tab)
# (合成患者同时有检验+检查记录)
# =========================================================
def test_raw_endpoint_includes_labs_and_exams(raw_payload):
    data = raw_payload
    for key in ("labs", "exams", "n_labs", "n_exams"):
        assert key in data, f"raw 响应应含 {key}"
    assert data["n_labs"] == len(data["labs"])
    assert data["n_exams"] == len(data["exams"])
    assert data["labs"], "合成患者应有检验记录"
    assert data["exams"], "合成患者应有检查记录"
    # 检验行 shape (前端 _labsPanelHtml 依赖这些 key)
    lab = data["labs"][0]
    assert {"date", "item", "result", "flag"}.issubset(lab.keys())
    # 日期归一为 YYYY/MM/DD (空串放行)
    assert lab["date"] == "" or re.match(r"^\d{4}/\d{2}/\d{2}$", lab["date"])
    assert (lab["date"], lab["item"], lab["result"]) == (
        "2024/03/07", "血红蛋白", "132",
    )
    # 检查行 shape
    exam = data["exams"][0]
    assert {"date", "check_type", "item", "conclusion"}.issubset(exam.keys())
    assert (exam["date"], exam["check_type"], exam["item"]) == (
        "2024/03/08", "CT", "胸部 CT",
    )


# =========================================================
# boost-llm-efficiency: sidebar TTL 缓存 (detail 页不复跑全表 CTE)
# =========================================================
def test_sidebar_patients_ttl_cache():
    import time as _time

    from javert.web.api import routes_workbench as rw

    rw._sidebar_cache.clear()
    calls: list[str] = []

    class _FakeStore:
        def list_patients_with_violations(self, filter_mode):
            calls.append(filter_mode)
            return []

    store = _FakeStore()
    try:
        # 列表页: 总是现查 + 刷新缓存
        rw._sidebar_patients(store, "v_and_i", allow_cached=False)
        assert calls == ["v_and_i"]
        # detail 页: TTL 内吃缓存, 不再打全表 CTE
        rw._sidebar_patients(store, "v_and_i", allow_cached=True)
        assert calls == ["v_and_i"]
        # 不同 filter → 各自缓存
        rw._sidebar_patients(store, "all", allow_cached=True)
        assert calls == ["v_and_i", "all"]
        # TTL 过期 → 重查
        rw._sidebar_cache["v_and_i"] = (_time.monotonic() - 999.0, [])
        rw._sidebar_patients(store, "v_and_i", allow_cached=True)
        assert calls == ["v_and_i", "all", "v_and_i"]
        # allow_cached=False (列表页) 即便缓存新鲜也现查
        rw._sidebar_patients(store, "all", allow_cached=False)
        assert calls == ["v_and_i", "all", "v_and_i", "all"]
    finally:
        rw._sidebar_cache.clear()


def test_resolve_hits_refreshes_legacy_drug_cache(monkeypatch):
    """旧药品缓存缺少依据时应回读历史 evidence，不要求重跑模型或手工删缓存。"""
    import json

    from javert.web.hit_resolver import Anchor, HitItem, hits_to_json

    fee_name = "某药注射液(商品名)"
    fee_df = pd.DataFrame(
        [[fee_name, "X-SYNTH-001", "L-SYNTH-001"]],
        columns=["medins_list_name", "med_list_codg", "medins_list_codg"],
    )
    monkeypatch.setattr(
        workbench_routes,
        "_get_loader",
        lambda: SimpleNamespace(get_fees=lambda _patient_id: fee_df),
    )
    monkeypatch.setattr(workbench_routes, "load_kb_drugs", lambda: {})

    run = SimpleNamespace(
        run_id="aud_legacy_leaflet",
        rule_id="RD_SYNTH",
        patient_id="P-SYNTH",
        evidence_json=json.dumps(
            [
                {
                    "source": "drug_audit_lookup",
                    "locator": fee_name,
                    "text": (
                        "药品：某药注射液；依据：说明书适应证原文。"
                        "检出逻辑：诊断不符合说明书适应证。"
                    ),
                },
                {"source": "fee", "locator": fee_name, "text": "费用明细命中。"},
            ],
            ensure_ascii=False,
        ),
        tool_calls_json="[]",
    )
    old_cache = hits_to_json(
        [
            HitItem(
                source="drug",
                name=fee_name,
                code_nat="X-SYNTH-001",
                matched_fee_name=fee_name,
                restriction="旧缓存已有但需要刷新的依据。",
                anchor=Anchor(tab="fees", query=fee_name),
            ),
            HitItem(
                source="fee",
                name=fee_name,
                code_nat="X-SYNTH-001",
                matched_fee_name=fee_name,
                anchor=Anchor(tab="fees", query=fee_name),
            ),
        ]
    )

    out = workbench_routes._resolve_hits_for_runs(
        "P-SYNTH",
        [run],
        {"RD_SYNTH": {"drug_rule_type": "超说明书"}},
        {run.run_id: old_cache},
    )

    visible = [h for h in out[run.run_id] if h.source in ("drug", "fee")]
    assert len(visible) == 1
    assert visible[0].source == "drug"
    assert visible[0].restriction == "说明书适应证原文。"
