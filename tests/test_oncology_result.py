# -*- coding: utf-8 -*-
"""结构化结果、文书建议、双库兼容与移植文书缺口金标."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from javert.audit.result import AuditResult, Evidence, ToolCall
from javert.audit.runner import _structured_evidence, _structured_reasoning
from javert.oncology.contracts import (
    AuditDisposition,
    CriterionAssessment,
    CriterionState,
    EligibilityEvaluation,
    EligibilityStatus,
    EvidenceAnchor,
    ProofNode,
)
from javert.oncology.guidance import (
    TRANSPLANT_SUGGESTION_TEXT,
    generate_documentation_suggestions,
    load_documentation_templates,
)
from javert.store.audit_store import SqliteStore
from javert.store.models import RunWithReviews
from javert.store.sqlserver_store import SqlServerStore
from javert.web.api.routes_audit import _result_payload
from javert.web.templating import render


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "oncology" / "pola_transplant_gap.json"


def _leaf(
    criterion_id: str,
    criterion_type: str,
    state: CriterionState,
    reason: str,
) -> tuple[CriterionAssessment, ProofNode]:
    assessment = CriterionAssessment(
        criterion_id=criterion_id,
        criterion_type=criterion_type,
        state=state,
        reason=reason,
    )
    return assessment, ProofNode(
        node_id=criterion_id,
        operator="leaf",
        state=state,
        criterion_id=criterion_id,
        criterion_type=criterion_type,
        assessment=assessment,
    )


def _pola_transplant_gap_evaluation() -> EligibilityEvaluation:
    items = [
        _leaf("pola-rr-diagnosis", "diagnosis", CriterionState.SATISFIED, "DLBCL"),
        _leaf("pola-prior-multiline", "prior_therapy", CriterionState.SATISFIED, "多线治疗"),
        _leaf("pola-progression", "treatment_status", CriterionState.SATISFIED, "疾病进展"),
        _leaf(
            "pola-transplant-ineligible",
            "clinician_assessment",
            CriterionState.UNKNOWN,
            "未见不适合造血干细胞移植的临床评估",
        ),
    ]
    return EligibilityEvaluation(
        audit_disposition=AuditDisposition.NO_VIOLATION_FOUND,
        eligibility_status=EligibilityStatus.DOCUMENTATION_GAP,
        rule_id="elig-pola-dlbcl-rr-transplant-2025",
        rule_version="1.0.0",
        indication_branch_id="dlbcl-relapsed-refractory-transplant-ineligible",
        criterion_assessments=[item[0] for item in items],
        proof_tree=ProofNode(
            node_id="pola-rr-root",
            operator="all",
            state=CriterionState.UNKNOWN,
            decisive_child_ids=["pola-transplant-ineligible"],
            children=[item[1] for item in items],
        ),
    )


def _result(evaluation: EligibilityEvaluation | None = None) -> AuditResult:
    return AuditResult(
        run_id="aud_pola_gap_001",
        rule_id="RD04",
        patient_id="GOLDEN-POLA-TRANSPLANT-GAP",
        verdict=evaluation.legacy_verdict if evaluation else "INCONCLUSIVE",
        confidence=0.9,
        reasoning="结构化资格测试",
        evidence=[Evidence(source="note", locator="golden", text="去标识事实")],
        tool_calls=[ToolCall(tool_name="drug_audit_lookup")],
        duration_ms=12,
        model="deterministic-test",
        started_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
        eligibility_evaluation=evaluation,
    )


def _released_scope_evaluation(
    policy_scope: str,
    *,
    violation: bool = False,
) -> EligibilityEvaluation:
    base = _pola_transplant_gap_evaluation()
    label = (
        "医保支付限定"
        if policy_scope == "INSURANCE_PAYMENT"
        else "指南适应证"
    )
    first = base.criterion_assessments[0].model_copy(
        update={
            "evidence_anchors": [
                EvidenceAnchor(
                    source="notes",
                    locator=f"scope:{policy_scope}",
                    text=f"{label}去标识证据",
                )
            ]
        }
    )
    return EligibilityEvaluation.model_validate(
        {
            **base.model_dump(mode="json"),
            "audit_disposition": (
                "VIOLATION_FOUND" if violation else "NO_VIOLATION_FOUND"
            ),
            "eligibility_status": (
                "NOT_SATISFIED" if violation else "DOCUMENTATION_GAP"
            ),
            "legacy_verdict": "VIOLATION" if violation else "CLEAN",
            "rule_id": f"rule-{policy_scope.lower()}",
            "rule_revision_id": f"revision-{policy_scope.lower()}",
            "indication_branch_id": f"branch-{policy_scope.lower()}",
            "release_id": "release-dual-synthetic",
            "drug_concept_id": "drug-dual-synthetic",
            "policy_scope": policy_scope,
            "source_type": policy_scope,
            "policy_scope_display_label": label,
            "source_document_ids": [f"document-{policy_scope.lower()}"],
            "source_fragment_ids": [f"fragment-{policy_scope.lower()}"],
            "source_versions": [f"{policy_scope.lower()}@2025"],
            "criterion_assessments": [
                first.model_dump(mode="json"),
                *[
                    item.model_dump(mode="json")
                    for item in base.criterion_assessments[1:]
                ],
            ],
            "rule_effective_from": "2026-01-01",
            "rule_effective_to": "2027-12-31",
            "evaluated_service_date": "2026-06-18",
            "temporal_applicability": "IN_WINDOW",
        }
    )


def _dual_scope_evaluation() -> EligibilityEvaluation:
    insurance = _released_scope_evaluation("INSURANCE_PAYMENT")
    guideline = _released_scope_evaluation("GUIDELINE_INDICATION", violation=True)
    return EligibilityEvaluation.model_validate(
        {
            **guideline.model_dump(mode="json", exclude={"scope_evaluations"}),
            "scope_evaluations": [
                insurance.as_scope_evaluation().model_dump(mode="json"),
                guideline.as_scope_evaluation().model_dump(mode="json"),
            ],
        }
    )


def test_audit_result_rejects_inconsistent_projection():
    with pytest.raises(ValidationError, match="兼容投影"):
        _result(_pola_transplant_gap_evaluation()).model_copy(
            update={"verdict": "VIOLATION"}
        ).model_validate(
            {
                **_result(_pola_transplant_gap_evaluation()).model_dump(),
                "verdict": "VIOLATION",
            }
        )


def test_old_audit_result_deserializes_without_eligibility():
    raw = _result().model_dump()
    raw.pop("eligibility_evaluation")
    loaded = AuditResult.model_validate(raw)
    assert loaded.eligibility_evaluation is None
    assert loaded.verdict == "INCONCLUSIVE"


def test_scope_evaluations_enforce_uniqueness_and_worst_projection():
    evaluation = _dual_scope_evaluation()
    assert evaluation.legacy_verdict == "VIOLATION"

    duplicate = evaluation.model_dump(mode="json")
    duplicate["scope_evaluations"].append(duplicate["scope_evaluations"][0])
    with pytest.raises(ValidationError, match="只能保留一个资格状态"):
        EligibilityEvaluation.model_validate(duplicate)

    less_severe = evaluation.model_dump(mode="json")
    less_severe["audit_disposition"] = "NO_VIOLATION_FOUND"
    less_severe["legacy_verdict"] = "CLEAN"
    with pytest.raises(ValidationError, match="最严重确定性投影"):
        EligibilityEvaluation.model_validate(less_severe)


def test_pola_transplant_gap_guidance_is_exact_and_does_not_change_facts():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    evaluation = _pola_transplant_gap_evaluation()
    before = evaluation.model_dump(mode="json")
    suggestions = generate_documentation_suggestions(
        evaluation,
        patient_context={
            "age": fixture["patient_context"]["age"],
            "multi_line_treatment": True,
        },
    )
    after = evaluation.model_dump(mode="json")
    assert before == after
    assert len(suggestions) == 1
    assert suggestions[0].suggested_content == TRANSPLANT_SUGGESTION_TEXT
    assert suggestions[0].suggested_content == fixture["expected_suggestion"]
    transplant = next(
        item
        for item in evaluation.criterion_assessments
        if item.criterion_id == "pola-transplant-ineligible"
    )
    assert transplant.state == CriterionState.UNKNOWN
    assert suggestions[0].safety_note


def test_disabling_guidance_changes_only_suggestion_output():
    evaluation = _pola_transplant_gap_evaluation()
    before = evaluation.model_dump(mode="json")
    assert generate_documentation_suggestions(
        evaluation,
        patient_context={"age": 74, "multi_line_treatment": True},
        enabled=False,
    ) == []
    assert evaluation.model_dump(mode="json") == before


def test_hospital_can_customize_guidance_wording(tmp_path: Path):
    config = tmp_path / "templates.json"
    config.write_text(
        json.dumps(
            {
                "templates": [
                    {
                        "criterion_id": "pola-transplant-ineligible",
                        "title": "本院移植评估提示",
                        "rationale": "减少报销材料缺项",
                        "content_template": "本院提示：患者{age}岁，请补充移植适合性评估。",
                        "required_context": ["age"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    suggestions = generate_documentation_suggestions(
        _pola_transplant_gap_evaluation(),
        patient_context={"age": 74},
        templates=load_documentation_templates(config),
    )
    assert suggestions[0].title == "本院移植评估提示"
    assert suggestions[0].suggested_content == "本院提示：患者74岁，请补充移植适合性评估。"


def test_sqlite_new_and_historical_rows_round_trip(tmp_path: Path):
    db = tmp_path / "audit.sqlite"
    store = SqliteStore(db)
    store.init_schema()
    evaluation = _pola_transplant_gap_evaluation()
    suggestions = generate_documentation_suggestions(
        evaluation,
        patient_context={"age": 74, "multi_line_treatment": True},
    )
    evaluation = evaluation.model_copy(
        update={"documentation_suggestions": suggestions}
    )
    result = _result(evaluation)
    store.write(result)
    loaded = store.find_by_run_id(result.run_id)
    assert loaded is not None
    assert loaded.eligibility_evaluation == evaluation
    assert loaded.verdict == "CLEAN"
    store.close()

    old_db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(old_db)
    conn.execute(
        """
        CREATE TABLE audit_runs (
            run_id TEXT PRIMARY KEY, rule_id TEXT, patient_id TEXT,
            verdict TEXT, confidence REAL, reasoning TEXT,
            evidence_json TEXT, tool_calls_json TEXT,
            duration_ms INTEGER, model TEXT, started_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO _meta VALUES ('schema_version', '1')")
    conn.execute(
        "INSERT INTO audit_runs "
        "(run_id, rule_id, patient_id, verdict, evidence_json, tool_calls_json, started_at) "
        "VALUES (?, ?, ?, ?, '[]', '[]', ?)",
        (
            "aud_HISTORICAL01",
            "R001",
            "OLD",
            "CLEAN",
            "2025-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()
    old_store = SqliteStore(old_db)
    old_store.init_schema()
    cols = {
        row[1]
        for row in old_store.conn.execute("PRAGMA table_info(audit_runs)").fetchall()
    }
    assert "eligibility_json" in cols
    old = old_store.find_by_run_id("aud_HISTORICAL01")
    assert old is not None
    assert old.eligibility_evaluation is None
    old_store.init_schema()
    assert sum(
        row[1] == "eligibility_json"
        for row in old_store.conn.execute("PRAGMA table_info(audit_runs)").fetchall()
    ) == 1
    old_store.close()


def _fake_engine(conn: MagicMock) -> MagicMock:
    engine = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = conn
    context.__exit__.return_value = False
    engine.connect.return_value = context
    return engine


def test_sqlserver_write_and_read_keep_eligibility_json(monkeypatch):
    result = _result(_dual_scope_evaluation())
    write_conn = MagicMock()
    not_found = MagicMock()
    not_found.fetchone.return_value = None
    inserted = MagicMock()
    write_conn.execute.side_effect = [not_found, inserted]
    write_store = SqlServerStore()
    monkeypatch.setattr(write_store, "get_engine", lambda: _fake_engine(write_conn))
    assert write_store.write_audit(result) is True
    params = write_conn.execute.call_args_list[1].args[1]
    written = json.loads(params["eligibility_json"])
    assert written["eligibility_status"] == "NOT_SATISFIED"
    assert {item["policy_scope"] for item in written["scope_evaluations"]} == {
        "INSURANCE_PAYMENT",
        "GUIDELINE_INDICATION",
    }

    read_conn = MagicMock()
    row_result = MagicMock()
    row_result.fetchone.return_value = (
        result.run_id,
        result.rule_id,
        result.patient_id,
        result.verdict,
        result.confidence,
        result.reasoning,
        json.dumps([item.model_dump() for item in result.evidence], ensure_ascii=False),
        json.dumps([item.model_dump() for item in result.tool_calls], ensure_ascii=False),
        result.duration_ms,
        result.model,
        result.started_at,
        "",
        params["eligibility_json"],
    )
    read_conn.execute.return_value = row_result
    read_store = SqlServerStore()
    monkeypatch.setattr(read_store, "get_engine", lambda: _fake_engine(read_conn))
    loaded = read_store.find_audit_by_run_id(result.run_id)
    assert loaded is not None
    assert loaded.eligibility_evaluation == result.eligibility_evaluation


def test_sqlserver_ddl_migration_is_nullable_and_idempotent():
    ddl = (ROOT / "scripts" / "sql" / "create_javert_tables.sql").read_text(
        encoding="utf-8"
    )
    assert "eligibility_json    NVARCHAR(MAX)  NULL" in ddl
    assert "IF NOT EXISTS" in ddl
    assert "ALTER TABLE javert_audit_runs ADD eligibility_json NVARCHAR(MAX) NULL" in ddl


def test_api_payload_and_workbench_show_pola_transplant_gap_suggestion():
    evaluation = _pola_transplant_gap_evaluation()
    evaluation = evaluation.model_copy(
        update={
            "documentation_suggestions": generate_documentation_suggestions(
                evaluation,
                patient_context={"age": 74, "multi_line_treatment": True},
            )
        }
    )
    result = _result(evaluation)
    payload = _result_payload(result)
    assert payload["verdict"] == "CLEAN"
    assert payload["eligibility_evaluation"]["audit_disposition"] == "NO_VIOLATION_FOUND"
    assert payload["eligibility_evaluation"]["eligibility_status"] == "DOCUMENTATION_GAP"

    run = RunWithReviews(
        run_id=result.run_id,
        rule_id=result.rule_id,
        patient_id=result.patient_id,
        verdict=result.verdict,
        confidence=result.confidence,
        reasoning=result.reasoning,
        created_at=result.started_at,
        eligibility_evaluation=evaluation,
    )
    html = render(
        "patient_detail.html",
        title="golden",
        current_user=type("User", (), {"id": 1, "username": "tester"})(),
        patients=[],
        active_patient=result.patient_id,
        filter="all",
        filter_label="全部",
        runs=[run],
    )
    # 面板改造: 英文双轴/裸 JSON 不再铺在卡片主体; 改为 reasoning 之后的折叠 follow-up.
    assert "肿瘤靶向药用药方案合理性" in html
    assert "待补文书" in html  # DOCUMENTATION_GAP 的中文 pill
    assert "移植适合性评估" in html  # criterion_type 中文标签
    assert TRANSPLANT_SUGGESTION_TEXT in html
    assert "认同 (V)" in html
    assert "改判不明 (I)" in html
    assert "驳回 (C)" in html


def test_release_scope_and_temporal_provenance_round_trip_as_additive_fields(
    tmp_path: Path,
):
    old_evaluation = _pola_transplant_gap_evaluation()
    assert old_evaluation.release_id is None
    assert old_evaluation.policy_scope is None
    assert old_evaluation.temporal_applicability is None
    assert old_evaluation.scope_evaluations == []

    evaluation = EligibilityEvaluation.model_validate(
        {
            **old_evaluation.model_dump(mode="json"),
            "release_id": "release-synthetic",
            "rule_revision_id": "revision-synthetic",
            "drug_concept_id": "drug-synthetic",
            "policy_scope": "GUIDELINE_INDICATION",
            "source_type": "GUIDELINE_INDICATION",
            "policy_scope_display_label": "指南适应证",
            "source_document_ids": ["source-document-synthetic"],
            "source_fragment_ids": ["source-fragment-synthetic"],
            "source_versions": ["synthetic-guideline@2025"],
            "rule_effective_from": "2026-01-01",
            "rule_effective_to": "2027-12-31",
            "evaluated_service_date": "2025-12-31",
            "effective_date_enforced": False,
            "temporal_applicability": "BEFORE_EFFECTIVE_WINDOW",
            "temporal_warning": "核查当期指南/医保限定是否适用",
        }
    )
    result = _result(evaluation)

    store = SqliteStore(tmp_path / "release-provenance.sqlite")
    store.init_schema()
    store.write(result)
    loaded = store.find_by_run_id(result.run_id)
    store.close()

    assert loaded is not None and loaded.eligibility_evaluation is not None
    persisted = loaded.eligibility_evaluation
    assert persisted.release_id == "release-synthetic"
    assert persisted.rule_revision_id == "revision-synthetic"
    assert persisted.policy_scope == "GUIDELINE_INDICATION"
    assert persisted.evaluated_service_date.isoformat() == "2025-12-31"
    assert persisted.temporal_applicability == "BEFORE_EFFECTIVE_WINDOW"
    assert persisted.effective_date_enforced is False

    api_payload = _result_payload(loaded)["eligibility_evaluation"]
    assert api_payload["release_id"] == "release-synthetic"
    assert api_payload["policy_scope"] == "GUIDELINE_INDICATION"
    assert api_payload["rule_effective_from"] == "2026-01-01"
    assert api_payload["temporal_warning"] == "核查当期指南/医保限定是否适用"

    run = RunWithReviews(
        run_id=result.run_id,
        rule_id=result.rule_id,
        patient_id=result.patient_id,
        verdict=result.verdict,
        confidence=result.confidence,
        reasoning=result.reasoning,
        created_at=result.started_at,
        eligibility_evaluation=evaluation,
    )
    html = render(
        "patient_detail.html",
        title="release provenance",
        current_user=type("User", (), {"id": 1, "username": "tester"})(),
        patients=[],
        active_patient=result.patient_id,
        filter="all",
        filter_label="全部",
        runs=[run],
    )
    assert "release-synthetic" in html
    assert "revision-synthetic" in html
    assert "指南适应证" in html
    assert "BEFORE_EFFECTIVE_WINDOW" in html
    assert "窗口前回溯应用最早已批准版本" in html
    assert "source-document-synthetic" in html
    assert "source-fragment-synthetic" in html


def test_dual_scope_round_trips_through_store_api_runner_and_workbench(
    tmp_path: Path,
):
    evaluation = _dual_scope_evaluation()
    result = _result(evaluation)
    store = SqliteStore(tmp_path / "dual-scope.sqlite")
    store.init_schema()
    store.write(result)
    loaded = store.find_by_run_id(result.run_id)
    store.close()

    assert loaded is not None and loaded.eligibility_evaluation is not None
    persisted = loaded.eligibility_evaluation
    assert len(persisted.scope_evaluations) == 2
    assert persisted.legacy_verdict == "VIOLATION"

    api_payload = _result_payload(loaded)["eligibility_evaluation"]
    assert {item["policy_scope"] for item in api_payload["scope_evaluations"]} == {
        "INSURANCE_PAYMENT",
        "GUIDELINE_INDICATION",
    }

    reasoning = _structured_reasoning(persisted, ["合成肿瘤药"])
    assert "[医保支付限定]" in reasoning
    assert "[指南适应证]" in reasoning
    assert "法定说明书" not in reasoning
    evidence = _structured_evidence(persisted, ["合成肿瘤药"])
    assert {item.locator for item in evidence if item.source == "notes"} == {
        "scope:INSURANCE_PAYMENT",
        "scope:GUIDELINE_INDICATION",
    }

    run = RunWithReviews(
        run_id=result.run_id,
        rule_id=result.rule_id,
        patient_id=result.patient_id,
        verdict=result.verdict,
        confidence=result.confidence,
        reasoning=reasoning,
        created_at=result.started_at,
        eligibility_evaluation=persisted,
    )
    html = render(
        "patient_detail.html",
        title="dual scope",
        current_user=type("User", (), {"id": 1, "username": "tester"})(),
        patients=[],
        active_patient=result.patient_id,
        filter="all",
        filter_label="全部",
        runs=[run],
    )
    assert 'data-policy-scope="INSURANCE_PAYMENT"' in html
    assert 'data-policy-scope="GUIDELINE_INDICATION"' in html
    assert "医保支付限定" in html
    assert "指南适应证" in html
    assert "法定说明书" not in html


def test_guideline_provenance_cannot_be_serialized_as_label_source():
    with pytest.raises(ValidationError, match="不得显示成法定说明书"):
        EligibilityEvaluation.model_validate(
            {
                **_pola_transplant_gap_evaluation().model_dump(mode="json"),
                "policy_scope": "GUIDELINE_INDICATION",
                "source_type": "GUIDELINE_INDICATION",
                "policy_scope_display_label": "法定说明书",
            }
        )
