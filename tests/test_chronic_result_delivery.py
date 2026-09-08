"""慢病独立存储/API/Workbench 合成端到端回归；不连接真实数据源。"""
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from javert.audit.result import AuditResult
from javert.chronic.contracts import ClinicalCriteriaEvaluation
from javert.clinical_criteria.contracts import CriterionAssessment, ProofNode
from javert.config import JavertConfig
from javert.store.audit_store import SqliteStore
from javert.store.models import RunWithReviews, PatientSidebarItem, User, clinical_criteria_fields
from javert.store.sqlserver_store import SqlServerStore
from javert.web.api import routes_audit, routes_workbench
from javert.web.templating import render, _clinical_evidence_anchor

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def evaluation(state="SATISFIED", *, blocked=False, matched=True):
    assessment = CriterionAssessment(
        criterion_id="CD02.SYNTHETIC", criterion_type="numeric", state=state,
        reason="合成阈值证据", expected_condition={"gte": 7},
        normalized_facts=[dict(fact_type="lab", raw_value="8", normalized_value=8,
            raw_unit="mmol/L", canonical_unit="mmol/L", observation_time=NOW,
            source_domain="lab", source_row_key="synthetic-row")],
        evidence_anchors=[dict(source="lab", locator="synthetic-row", text="合成检验 8 mmol/L")],
        missing_items=["合成复测记录"],
    )
    proof = ProofNode(node_id="CD02.SYNTHETIC", operator="leaf", state=state, assessment=assessment)
    disposition = {"SATISFIED": "QUALIFIED", "NOT_SATISFIED": "NOT_QUALIFIED"}.get(state, "REVIEW_REQUIRED")
    return ClinicalCriteriaEvaluation(
        schema_version="1.0.0", rule_id="CD02", disease_id="synthetic", disease_name="合成病种",
        policy_version="2025", release_id="synthetic-release", disease_revision_id="CD02-synthetic",
        asset_checksum="sha256:" + "a" * 64, execution_status="BLOCKED" if blocked else "EVALUATED",
        evaluation_mode="shadow", root_state=None if blocked else state,
        qualified=None if blocked or state in {"UNKNOWN", "CONFLICT"} else state == "SATISFIED",
        qualification_disposition="REVIEW_REQUIRED" if blocked else disposition,
        legacy_verdict="INCONCLUSIVE" if blocked or disposition == "REVIEW_REQUIRED" else "CLEAN",
        proof_tree=None if blocked else proof, shadow_proof_tree=proof if blocked else None,
        blocking_reasons=[dict(block_reason_code="UNRELEASED", unresolved_question="合成标准待签发",
            affected_node_ids=[proof.node_id], required_approver_roles=["expert"],
            prohibited_fallbacks=["2020_POLICY", "LLM", "LOCAL_DEFAULT"])] if blocked else [],
        data_quality_flags=["CHRONIC_CANDIDATE_MATCHED" if matched else "CHRONIC_NO_CANDIDATE"],
        normalizer_version="test", evaluator_version="test", evaluated_at=NOW,
    )


def result(ce=None):
    ce = ce or evaluation()
    return AuditResult(run_id="aud_CHRONICSYN01", rule_id="CD02", patient_id="SYNTHETIC-CASE",
        verdict=ce.legacy_verdict, clinical_criteria_evaluation=ce, started_at=NOW)


def sqlstore(monkeypatch, rows=None):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows or []
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    store = SqlServerStore(JavertConfig(_env_file=None, sql_enabled=False))
    monkeypatch.setattr(store, "get_engine", lambda: engine)
    return store, conn


@pytest.mark.parametrize("state", ["SATISFIED", "NOT_SATISFIED", "UNKNOWN", "CONFLICT"])
def test_sqlite_roundtrip_and_pending_exact_read(tmp_path, state):
    store = SqliteStore(tmp_path / "synthetic.sqlite")
    store.init_schema()
    r = result(evaluation(state))
    store.write(r, batch_tag="慢病")
    assert store.find_by_run_id(r.run_id).clinical_criteria_evaluation == r.clinical_criteria_evaluation
    assert store.find_unsynced()[0].clinical_criteria_evaluation == r.clinical_criteria_evaluation
    row = store.conn.execute("SELECT clinical_criteria_json, eligibility_json, batch_tag FROM audit_runs").fetchone()
    assert row[0] and row[1] is None and row[2] == "慢病"
    store.close()


def test_nullable_idempotent_migration_keeps_old_row(tmp_path):
    store = SqliteStore(tmp_path / "old.sqlite")
    schema = (ROOT / "src/javert/store/schema.sql").read_text().replace("    clinical_criteria_json TEXT,\n", "")
    store.conn.executescript(schema)
    store.conn.execute("INSERT INTO audit_runs (run_id,rule_id,patient_id,verdict,reasoning,started_at) VALUES (?,?,?,?,?,?)",
        ("aud_LEGACYSYN001", "R191", "SYNTHETIC-OLD", "CLEAN", "原合成文本", NOW.isoformat()))
    store.conn.commit()
    # Even the pre-migration row reader accepts an absent column.
    assert store.find_by_run_id("aud_LEGACYSYN001").clinical_criteria_evaluation is None
    store.init_schema(); store.init_schema()
    old = store.find_by_run_id("aud_LEGACYSYN001")
    assert old.reasoning == "原合成文本" and old.verdict == "CLEAN"
    assert old.clinical_criteria_evaluation is None
    cols = store.conn.execute("PRAGMA table_info(audit_runs)").fetchall()
    assert sum(c[1] == "clinical_criteria_json" and c[3] == 0 for c in cols) == 1
    ddl = (ROOT / "scripts/sql/create_javert_tables.sql").read_text()
    assert "WHERE Name = N'clinical_criteria_json'" in ddl
    assert "ALTER TABLE javert_audit_runs ADD clinical_criteria_json NVARCHAR(MAX) NULL" in ddl
    store.close()


@pytest.mark.parametrize("update", [{"rule_id": "R191"}, {"verdict": "VIOLATION"}, {"verdict": "INCONCLUSIVE"}])
def test_result_rejects_mixed_rule_and_projection(update):
    with pytest.raises(ValidationError):
        AuditResult.model_validate({**result().model_dump(), **update})


def test_result_rejects_oncology_mix():
    from javert.oncology.contracts import EligibilityEvaluation
    oncology = EligibilityEvaluation(audit_disposition="NO_VIOLATION_FOUND", eligibility_status="SATISFIED", proof_tree=dict(node_id="onco", operator="leaf", state="SATISFIED", assessment=dict(criterion_id="onco", criterion_type="diagnosis", state="SATISFIED")))
    with pytest.raises(ValidationError, match="混用"):
        AuditResult.model_validate({**result().model_dump(), "eligibility_evaluation": oncology})


def test_sqlserver_roundtrip_and_events(monkeypatch):
    r = result(evaluation(blocked=True))
    store, conn = sqlstore(monkeypatch)
    conn.execute.return_value.fetchone.return_value = None
    assert store.write_audit(r, batch_tag="慢病")
    params = conn.execute.call_args.args[1]
    assert params["eligibility_json"] is None
    assert json.loads(params["clinical_criteria_json"]) == r.clinical_criteria_evaluation.model_dump(mode="json")
    conn.execute.return_value.fetchone.return_value = (
        r.run_id, r.rule_id, r.patient_id, r.verdict, 0, "", "[]", "[]", 0, "test", NOW,
        "", None, None, None, None, params["clinical_criteria_json"])
    assert store.find_audit_by_run_id(r.run_id).clinical_criteria_evaluation == r.clinical_criteria_evaluation
    conn.execute.return_value.fetchall.return_value = [
        (1, r.run_id, r.patient_id, r.rule_id, r.verdict, 0, NOW, None, None, "", params["clinical_criteria_json"])]
    event = store.fetch_runs_since_id(0)[0]
    assert event["clinical_criteria_evaluation"] == r.clinical_criteria_evaluation.model_dump(mode="json")
    assert "clinical_criteria_json" in str(conn.execute.call_args.args[0])


@pytest.mark.parametrize("raw", [None, "", "{", "{}", '[]'])
def test_workbench_bad_json_fail_closed(raw):
    fields = clinical_criteria_fields(raw, "CD02", "CLEAN")
    assert fields["clinical_criteria_evaluation"] is None
    assert fields.get("clinical_criteria_invalid", False) == bool(raw)


def test_workbench_latest_history_and_projection_guard(monkeypatch):
    r = result(evaluation(blocked=True))
    row = (r.run_id, r.rule_id, r.patient_id, r.verdict, 0, "", "[]", "[]", 0, "test", NOW, NOW, "test", "慢病", "", None, None, "", r.clinical_criteria_evaluation.model_dump_json())
    store, conn = sqlstore(monkeypatch)
    conn.execute.side_effect = [SimpleNamespace(fetchall=lambda: [row, (*row[:18], "{")]), SimpleNamespace(fetchall=lambda: [])]
    runs = store.list_runs_for_patient(r.patient_id, "all")
    assert runs[0].clinical_criteria_evaluation == r.clinical_criteria_evaluation
    assert runs[0].history[0].clinical_criteria_invalid
    assert clinical_criteria_fields(row[18], "RD04", r.verdict)["clinical_criteria_invalid"]
    assert clinical_criteria_fields(row[18], "CD02", "CLEAN")["clinical_criteria_invalid"]


@pytest.mark.parametrize("state,blocked,label", [("SATISFIED",False,"符合慢病认定标准（QUALIFIED）"), ("NOT_SATISFIED",False,"不符合当前认定条件"), ("UNKNOWN",False,"需补充材料或人工复核"), ("SATISFIED",True,"暂不自动判定。")])
def test_panel_and_candidate_badges(state, blocked, label):
    r = result(evaluation(state, blocked=blocked))
    run = RunWithReviews(**r.model_dump(), created_at=NOW)
    html = render("patient_detail.html", runs=[run], patients=[], active_patient=r.patient_id,
        filter="all", filter_label="全部", current_user=User(id=1,username="synthetic",created_at=NOW),
        overview=None, run_groups=routes_workbench._group_runs_by_violation_type([run], {}))
    assert label in html and "慢病命中（待复核）" in html
    assert 'class="violation-card chronic-card"' in html
    assert 'data-node-id="CD02.SYNTHETIC"' in html and "合成复测记录" in html
    assert "mmol/L" in html and "synthetic-row" in html
    assert "门诊慢性病认定条件评估" in html
    if blocked: assert "已核查的候选证据" in html
    run.clinical_criteria_evaluation = evaluation(blocked=True, matched=False)
    html = render("patient_detail.html", runs=[run], patients=[], active_patient=r.patient_id,
        filter="all", filter_label="全部", current_user=User(id=1,username="synthetic",created_at=NOW), overview=None)
    assert "慢病无候选命中" in html and "慢病命中（待复核）" not in html


def test_all_clean_batch_discoverable_and_excluded_from_badges(monkeypatch):
    store, conn = sqlstore(monkeypatch)
    counts = [("SYNTHETIC-CASE", 0, 0, 0, "慢病", NOW, 2, 1), ("SYNTHETIC-ORDINARY",0,0,1,None,NOW,0,0)]
    conn.execute.side_effect = [SimpleNamespace(fetchall=lambda: counts), SimpleNamespace(fetchall=lambda: [])]
    patients = store.list_patients_with_violations("v_and_i")
    assert [p.patient_id for p in patients] == ["SYNTHETIC-CASE"]
    assert patients[0].v_count == patients[0].i_count == patients[0].c_count == 0
    html = render("_sidebar.html", patients=patients, active_patient=None, filter="v_and_i")
    assert "filter=all" in html and "慢病命中（待复核） 1" in html
    assert "CHRONIC_CANDIDATE_MATCHED" in str(conn.execute.call_args_list[0].args[0])
    for tag in ["Chronic_Disease", "%E6%85%A2%E7%97%85"]:
        req=Request({"type":"http","query_string":f"batch_tag={tag}".encode(),"headers":[]})
        assert routes_workbench._filter_from(req, None) == "all"
    assert routes_workbench._filter_from(Request({"type":"http","query_string":b"","headers":[]}), None) == "v_and_i"


def test_api_result_payload_and_detail(tmp_path, monkeypatch):
    r = result(evaluation(blocked=True))
    cfg = JavertConfig(_env_file=None, audit_db=str(tmp_path / "synthetic.sqlite"), sql_enabled=False)
    monkeypatch.setattr(routes_audit, "get_config", lambda: cfg)
    monkeypatch.setattr(routes_audit, "load_rule_meta", lambda: {})
    store=SqliteStore(cfg.audit_db_path);store.init_schema();store.write(r)
    payload = routes_audit._result_payload(r)
    assert payload["clinical_criteria_evaluation"] == r.clinical_criteria_evaluation.model_dump(mode="json")
    assert payload["eligibility_evaluation"] is None
    detail = routes_audit.get_audit_run(r.run_id)
    assert detail.clinical_criteria_evaluation == r.clinical_criteria_evaluation
    store.close()


def test_precise_anchor_no_locator_is_unresolved():
    from javert.clinical_criteria.contracts import EvidenceAnchor
    assert _clinical_evidence_anchor(EvidenceAnchor(source="lab", text="合成"))["unresolved"]
    anchor = _clinical_evidence_anchor(EvidenceAnchor(source="lab", locator="synthetic-row", text="合成"))
    assert anchor["tab"] == "labs" and anchor["exact"] and not anchor["unresolved"]


def test_2c_versions_preserve_structure_and_null_compatibility(tmp_path, monkeypatch):
    r = result(evaluation(blocked=True))
    cfg = JavertConfig(_env_file=None, audit_db=str(tmp_path / "synthetic.sqlite"), sql_enabled=False)
    monkeypatch.setattr(routes_audit, "get_config", lambda: cfg)
    monkeypatch.setattr(routes_audit, "load_rule_meta", lambda: {})
    monkeypatch.setattr(routes_audit, "_get_loader", lambda: SimpleNamespace(get_fees=lambda _: None))
    monkeypatch.setattr(routes_audit, "_v2_fee_df", lambda _: None)
    monkeypatch.setattr(routes_audit, "load_kb_drugs", lambda: {})
    monkeypatch.setattr(routes_audit, "resolve_hits_from_json", lambda *args: [])
    monkeypatch.setattr(routes_audit, "_2c_tasks", {})
    routes_audit._clear_2c_card_cache()
    store = SqliteStore(cfg.audit_db_path);store.init_schema();store.write(r)
    expected = r.clinical_criteria_evaluation.model_dump(mode="json")
    assert routes_audit.results_2c(r.patient_id)["results"][0]["clinical_criteria_evaluation"] == expected
    for endpoint in [routes_audit.results_2c_v2, routes_audit.results_2c_v3]:
        assert endpoint(r.patient_id)["cards"][0]["clinical_criteria_evaluation"] == expected
    # Presentation cleanup must never rewrite proof text or checksum-governed fields.
    payload = {"clinical_criteria_evaluation": {"missing_items": [routes_audit._V2_PLACEHOLDER_TEXT]}}
    assert routes_audit._clean_v2_payload(payload) == payload
    store.close()
    routes_audit._clear_2c_card_cache()


@pytest.mark.asyncio
async def test_watcher_publishes_additive_clinical_payload(monkeypatch):
    from javert.web.api import routes_sse
    r = result(evaluation(blocked=True))
    watcher = routes_sse.AuditWatcher()
    payloads = []
    async def publish(event, payload):
        payloads.append((event, payload))
        watcher._stop.set()
    monkeypatch.setattr(routes_sse, "load_rule_meta", lambda: {})
    monkeypatch.setattr(routes_sse, "event_bus", SimpleNamespace(publish=publish))
    monkeypatch.setattr(routes_sse, "get_sqlserver_store", lambda: SimpleNamespace(
        fetch_runs_since_id=lambda *_: [{**r.model_dump(mode="json"), "id":1}],
        has_other_runs=lambda *_: False))
    await watcher._loop()
    assert watcher.last_error is None
    assert payloads[0][1]["clinical_criteria_evaluation"] == r.clinical_criteria_evaluation.model_dump(mode="json")
    assert payloads[0][1]["eligibility_evaluation"] is None


def test_persist_pending_precise_retry_preserves_blocked_proof(tmp_path, monkeypatch):
    from javert.store import result_persister
    r=result(evaluation(blocked=True))
    cfg=JavertConfig(_env_file=None, sql_enabled=True, batch_tag="慢病")
    monkeypatch.setattr(result_persister, "get_config", lambda: cfg)
    archive=MagicMock();archive.write_audit.side_effect=[False, True]
    monkeypatch.setattr(result_persister, "get_sqlserver_store", lambda: archive)
    loader=MagicMock()
    store=SqliteStore(tmp_path / "retry.sqlite");store.init_schema()
    assert result_persister.persist_one(r, sqlite_store=store, source_loader=loader)["sync_state"] == "pending"
    loader.get_fees.assert_not_called()
    loaded=store.find_by_run_id(r.run_id)
    assert archive.write_audit(loaded, batch_tag="慢病")
    store.mark_synced(r.run_id)
    assert loaded.clinical_criteria_evaluation == r.clinical_criteria_evaluation
    assert store.find_unsynced() == []
    store.close()


def test_source_locators_survive_note_sorting_and_domain_mapping():
    import pandas as pd
    rows=routes_workbench._notes_to_list(pd.DataFrame([
        {"阶段":"出院记录","子阶段":"后","内容":"合成后文","事件时间":"2026-09-08"},
        {"阶段":"入院记录","子阶段":"前","内容":"合成前文","事件时间":"2026-09-01"}]))
    assert {row["content"]:row["source_locator"] for row in rows} == {"合成后文":"notes:0","合成前文":"notes:1"}
    assert routes_workbench._format_lab_rows([{}])[0]["source_locator"] == "labs:0"
    assert routes_workbench._format_exam_rows([{}])[0]["source_locator"] == "examinations:0"


def test_batch_entry_does_not_change_ordinary_filter_cookie(monkeypatch):
    user=User(id=1, username="synthetic", created_at=NOW)
    monkeypatch.setattr(routes_workbench, "current_user", lambda _: user)
    monkeypatch.setattr(routes_workbench, "get_sqlserver_store", lambda: SimpleNamespace(
        get_since_last_login_stats=lambda **_: None))
    monkeypatch.setattr(routes_workbench, "_sidebar_patients", lambda *_, **__: [])
    request=Request({"type":"http", "query_string":b"batch_tag=Chronic_Disease&filter=all", "headers":[], "session":{}})
    response=routes_workbench.workbench_index(request, filter="all")
    assert "set-cookie" not in response.headers


def test_count_proof_and_escaped_evidence_are_rendered():
    ce = evaluation()
    leaf = ce.proof_tree
    root = ProofNode(node_id="CD02.COUNT", operator="at_least_n", state="SATISFIED",
        children=[leaf], decisive_child_ids=[leaf.node_id], threshold=1, lower_bound=1, upper_bound=1)
    ce.proof_tree=root
    ce.proof_tree.children[0].assessment.evidence_anchors[0].text='<script>synthetic</script>'
    run=RunWithReviews(**result(ce).model_dump(),created_at=NOW)
    html=render("_clinical_criteria.html",run=run)
    assert "至少 1 项" in html and "已满足下界 1" in html and "可能满足上界 1" in html
    assert "决定分支：CD02.SYNTHETIC" in html
    assert '<script>synthetic</script>' not in html
    assert '&lt;script&gt;synthetic&lt;/script&gt;' in html


def test_expert_panel_shows_summary_and_quality_flags():
    ce=evaluation(blocked=True)
    ce.shadow_proof_tree.assessment.expected_condition["summary"]="合成检验达到原文阈值"
    ce.missing_items=["CD02.SYNTHETIC"]
    ce.data_quality_flags += ["OCR_UNVERIFIED", "EXTRACTION_FAILED", "EXTRACTION_TRUNCATED", "FEE_COMPLETENESS_NOT_VERIFIED", "CANDIDATE_EVIDENCE:CD02.SYNTHETIC"]
    run=RunWithReviews(**result(ce).model_dump(),created_at=NOW)
    html=render("_clinical_criteria.html",run=run)
    assert "OCR 识别内容未人工核对" in html
    assert "候选证据抽取失败" in html and "候选证据抽取被截断" in html
    assert "费用资料完整性尚未核验" in html
    assert "<strong>合成检验达到原文阈值</strong> · 满足" in html
    assert "待补材料：合成检验达到原文阈值" in html
    assert "<details><summary>技术明细</summary>" in html
    assert "<strong>慢病命中（待复核）</strong>" in html
    assert "<strong>标准尚未签发" not in html
