"""慢病试跑 runtime 合成测试；不读取患者或扫描政策 PDF。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from javert.audit.rule_loader import load_rule
from javert.audit.runner import Runner
from javert.chronic.contracts import ClinicalCriteriaEvaluation, CriterionNode
from javert.chronic.facts import collect_records, extract_candidates
from javert.chronic.knowledge import load_criteria_asset
from javert.chronic.runtime import _leaf_assessment, build_shadow_proof, evaluate_chronic_rule
from javert.config import JavertConfig
from javert.tools.tool_executor import ToolExecutor

ROOT = Path(__file__).resolve().parents[1]
CASE = "synthetic-case"


class Loader:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = 0

    def get_notes(self, patient_id):
        assert patient_id == CASE
        self.calls += 1
        return pd.DataFrame(self.rows)

    def get_fees(self, patient_id):
        pytest.fail("CD must not read fees")


class Provider:
    model_name = "synthetic-provider"

    def __init__(self, candidate=None, fail=False):
        self.candidate = candidate
        self.fail = fail
        self.calls = []

    def chat_with_retry(self, messages, **kwargs):
        payload = json.loads(messages[-1]["content"])
        assert CASE not in messages[-1]["content"]
        self.calls.append(payload)
        if self.fail:
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR_MUST_NOT_ESCAPE")
        # 单记录沿用既有测试回调；真实 provider 始终接收 records 数组。
        callback_payload = {**payload, **payload["records"][0]} if len(payload["records"]) == 1 else payload
        items = self.candidate(callback_payload) if self.candidate else []
        return {"content": json.dumps({"candidates": items}, ensure_ascii=False)}


def note(text="已确诊糖尿病。", **kwargs):
    return {"住院号": CASE, "文书ID": "page-1", "事件时间": "", "阶段": "临床",
            "子阶段": "诊断", "内容": text, "来源文件": "page-1", **kwargs}


def config(mode="shadow"):
    return JavertConfig(chronic_disease_criteria=mode)


def rule(code="CD03"):
    return load_rule(ROOT / "configs/rules" / f"{code}.yaml")


def candidate(payload, **updates):
    item = {"node_id": "CD03.DM_HISTORY", "source_locator": payload["source_locator"],
            "quote": "已确诊糖尿病。", "raw_value": "糖尿病", "unit": "",
            "polarity": "positive", "certainty": "confirmed"}
    return [{**item, **updates}]


def leaves(proof):
    return [proof] if not proof.children else [leaf for child in proof.children for leaf in leaves(child)]


def test_off_zero_loader_llm_and_no_gate(monkeypatch):
    loader, provider = Loader([note()]), Provider()
    runner = Runner(ToolExecutor(), provider=provider, config=config("off"), loader=loader)
    monkeypatch.setattr(runner, "_build_net_fee_ctx", lambda *_: pytest.fail("fees"))
    monkeypatch.setattr(runner, "_run_precheck", lambda *_: pytest.fail("precheck"))
    result = runner.audit(rule("CD10"), CASE)
    assert result.verdict == "INCONCLUSIVE"
    assert result.clinical_criteria_evaluation.qualified is None
    assert "CHRONIC_DISABLED" in result.clinical_criteria_evaluation.data_quality_flags
    assert loader.calls == 0 and provider.calls == []
    assert result.tool_calls == [] and result.eligibility_evaluation is None


@pytest.mark.parametrize("number", range(1, 21))
def test_all_twenty_runner_rules_missing_data_are_review(number):
    runner = Runner(ToolExecutor(), provider=Provider(), config=config(), loader=Loader())
    result = runner.audit(rule(f"CD{number:02d}"), CASE)
    evaluation = result.clinical_criteria_evaluation
    assert result.verdict == "INCONCLUSIVE"
    assert evaluation.execution_status == "BLOCKED"
    assert evaluation.qualified is None and evaluation.root_state is None
    assert evaluation.proof_tree is None and evaluation.shadow_proof_tree is not None
    assert evaluation.shadow_proof_tree.state == "UNKNOWN"
    assert evaluation.missing_items
    assert "CHRONIC_NO_CANDIDATE" in evaluation.data_quality_flags
    assert "CRITERIA_ASSET_INVALID" not in evaluation.data_quality_flags
    assert ClinicalCriteriaEvaluation.model_validate_json(evaluation.model_dump_json()) == evaluation


def test_verified_quote_is_retained_as_candidate_not_qualified(capsys):
    messages = []
    result = Runner(ToolExecutor(), provider=Provider(candidate), config=config(),
                    loader=Loader([note()]), emit=messages.append).audit(rule(), CASE)
    evaluation = result.clinical_criteria_evaluation
    assert evaluation.qualified is None and evaluation.qualification_disposition == "REVIEW_REQUIRED"
    assert "CHRONIC_CANDIDATE_MATCHED" in evaluation.data_quality_flags
    assert "CHRONIC_NO_CANDIDATE" not in evaluation.data_quality_flags
    assert "CANDIDATE_EVIDENCE:CD03.DM_HISTORY" in evaluation.data_quality_flags
    assessment = next(n.assessment for n in leaves(evaluation.shadow_proof_tree) if n.node_id == "CD03.DM_HISTORY")
    assert assessment.evidence_anchors[0].text == "已确诊糖尿病。"
    assert assessment.evidence_anchors[0].anchor["document_id"] == "page-1"
    assert assessment.normalized_facts[0].observation_time is None
    assert messages == [] and capsys.readouterr().out == ""


@pytest.mark.parametrize("update", [
    {"quote": "虚构病历"}, {"source_locator": "notes:99"}, {"raw_value": "虚构词语"},
    {"node_id": "CD20.ROOT"}, {"node_id": []}, {"raw_value": True}, {"unit": "invented"},
])
def test_invalid_candidate_does_not_mark_match(update):
    evaluation = evaluate_chronic_rule(rule(), CASE, loader=Loader([note()]),
        provider=Provider(lambda p: candidate(p, **update)), config=config())
    assert "CANDIDATE_REJECTED" in evaluation.data_quality_flags
    assert "CANDIDATE_REJECTED_COUNT:1" in evaluation.data_quality_flags
    assert "CHRONIC_NO_CANDIDATE" in evaluation.data_quality_flags


def test_ocr_unverified_and_negation_keep_unknown():
    evaluation = evaluate_chronic_rule(rule(), CASE, loader=Loader([note("OCR未人工核对\n已确诊糖尿病。")]),
                                      provider=Provider(candidate), config=config())
    assert "OCR_UNVERIFIED" in evaluation.data_quality_flags
    assert "CHRONIC_CANDIDATE_MATCHED" in evaluation.data_quality_flags
    assert all(n.state == "UNKNOWN" for n in leaves(evaluation.shadow_proof_tree))
    records, _ = collect_records(Loader([note("否认糖尿病。")]), CASE)
    node = next(n for n in load_criteria_asset(ROOT / "configs/chronic_disease_criteria.json").policy_sets[0].disease_revisions[2].nodes if n.node_id == "CD03.DM_HISTORY")
    facts, _ = extract_candidates(records, [node], Provider(lambda p: candidate(p, quote="糖尿病")))
    assert facts[node.node_id][0].uncertainty_reason == "ASSERTION_POLARITY_UNVERIFIED"


@pytest.mark.parametrize("page_chars,expected_calls", [(200, 3), (800, 5)])
def test_every_page_and_long_page_is_visited(page_chars, expected_calls):
    rows = [note(f"合成临床页{i}。".ljust(page_chars, "甲"), **{"文书ID": f"page-{i}"}) for i in range(25)]
    rows.append(note("合成内容" * 3000 + "已确诊糖尿病。", **{"文书ID": "page-last"}))

    def propose(payload):
        items = []
        for record in payload["records"]:
            if "已确诊糖尿病。" in record["text"]:
                items.extend(candidate(record))
        if len(payload["records"]) > 1:
            first, second = payload["records"][:2]
            # 引文确实在本批中，但属于另一页，必须拒绝挪用。
            quote = second["text"].split("。")[0] + "。"
            items.extend(candidate(first, quote=quote, raw_value=quote))
        return items

    provider = Provider(propose)
    evaluation = evaluate_chronic_rule(rule(), CASE, loader=Loader(rows), provider=provider, config=config())
    assert len(provider.calls) == expected_calls < len(rows)
    assert all(sum(len(r["text"]) for r in p["records"]) <= 10000 for p in provider.calls)
    visited = [r for p in provider.calls for r in p["records"]]
    assert {r["source_locator"] for r in visited} == {f"notes:{i}" for i in range(26)}
    assert [r["text"] for r in visited if r["source_locator"] != "notes:25"] == [r["内容"] for r in rows[:25]]
    long_chunks = [r["text"] for r in visited if r["source_locator"] == "notes:25"]
    assert long_chunks[0] + long_chunks[1][300:] == rows[-1]["内容"]
    assert "CHRONIC_CANDIDATE_MATCHED" in evaluation.data_quality_flags
    assert "CANDIDATE_REJECTED" in evaluation.data_quality_flags
    observations = [f for n in leaves(evaluation.shadow_proof_tree) for f in n.assessment.normalized_facts]
    assert len(observations) == 1 and observations[0].source_row_key == "notes:25"


def test_scope_and_llm_failure_do_not_leak(capsys, caplog):
    evaluation = evaluate_chronic_rule(rule(), CASE,
        loader=Loader([note(), note(**{"住院号": "synthetic-other-case"})]),
        provider=Provider(fail=True), config=config())
    assert "PATIENT_SCOPE_REJECTED:notes" in evaluation.data_quality_flags
    assert "EXTRACTION_FAILED" in evaluation.data_quality_flags
    assert "EXTRACTION_FAILED_COUNT:1" in evaluation.data_quality_flags
    assert "EXTRACTION_INCOMPLETE" in evaluation.data_quality_flags
    assert evaluation.qualified is None
    assert "SYNTHETIC_PRIVATE_ERROR" not in evaluation.model_dump_json()
    assert "SYNTHETIC_PRIVATE_ERROR" not in caplog.text + capsys.readouterr().out


def test_on_stays_blocked_without_release():
    evaluation = evaluate_chronic_rule(rule(), CASE, loader=Loader([note()]),
                                      provider=Provider(candidate), config=config("on"))
    assert evaluation.qualified is None
    assert "AUTOMATIC_RELEASE_NOT_ENABLED" in evaluation.data_quality_flags


def test_asset_error_no_fallback_or_patient_reads(monkeypatch):
    monkeypatch.setattr("javert.chronic.runtime.load_criteria_asset", lambda *_args, **_kw: (_ for _ in ()).throw(ValueError("SYNTHETIC_PRIVATE_ERROR")))
    loader, provider = Loader([note()]), Provider(candidate)
    evaluation = evaluate_chronic_rule(rule(), CASE, loader=loader, provider=provider, config=config())
    assert "CRITERIA_ASSET_INVALID" in evaluation.data_quality_flags
    assert loader.calls == 0 and not provider.calls


def synthetic_node(**updates):
    return CriterionNode(node_id="CD03.TEST", node_type="LEAF", criterion_id="CD03.TEST",
        fact_type="clinical_assertion", summary="合成糖尿病条件", evidence_domains=["文书"],
        source_fragment_ids=["synthetic-fragment"], authoring_status="synthetic",
        **{"compilation_status": "compiled", "expected_condition": {"type": "presence", "terms": ["糖尿病"]}, **updates})


def test_explicit_presence_numeric_conflict_and_partial():
    node = synthetic_node()
    records, _ = collect_records(Loader([note()]), CASE)
    facts, _ = extract_candidates(records, [node], Provider(lambda p: candidate(p, node_id=node.node_id)))
    observation = facts[node.node_id][0]
    assert _leaf_assessment(node, [observation], "synthetic").state == "SATISFIED"
    assert _leaf_assessment(node.model_copy(update={"compilation_status": "partial"}), [observation], "synthetic").state == "UNKNOWN"
    numeric = synthetic_node(expected_condition={"type": "numeric", "terms": ["合成检验"], "operator": "lt", "value": "60", "unit": "ml/min"})
    records, _ = collect_records(Loader([note("合成检验59.999ml/min。")]), CASE)
    facts, _ = extract_candidates(records, [numeric], Provider(lambda p: candidate(p, node_id=numeric.node_id,
        quote="合成检验59.999ml/min。", raw_value="59.999", unit="ml/min")))
    observation = facts[numeric.node_id][0]
    assert _leaf_assessment(numeric, [observation], "synthetic").state == "SATISFIED"
    other = observation.model_copy(update={"raw_value": "60"})
    assert _leaf_assessment(numeric, [other], "synthetic").state == "NOT_SATISFIED"
    assert _leaf_assessment(numeric, [observation, other], "synthetic").state == "CONFLICT"
    assert _leaf_assessment(numeric, [observation.model_copy(update={"raw_unit": ""})], "synthetic").state == "UNKNOWN"


@pytest.mark.parametrize("operator,threshold,expected", [("AND", None, "UNKNOWN"), ("OR", None, "SATISFIED"), ("AT_LEAST_N", 2, "UNKNOWN")])
def test_complete_logic_aggregates_but_partial_root_never_qualifies(operator, threshold, expected):
    asset = load_criteria_asset(ROOT / "configs/chronic_disease_criteria.json")
    revision = next(r for p in asset.policy_sets for r in p.disease_revisions if r.rule_id == "CD03")
    first = synthetic_node(parent_node_id="CD03.ROOT")
    second = first.model_copy(update={"node_id": "CD03.SECOND", "criterion_id": "CD03.SECOND"})
    root = CriterionNode(node_id="CD03.ROOT", node_type="LOGIC", operator=operator, threshold=threshold,
        summary="合成组合", evidence_domains=["多域"], source_fragment_ids=["synthetic-fragment"],
        authoring_status="synthetic", compilation_status="compiled")
    revision = revision.model_copy(update={"nodes": [root, first, second]})
    records, _ = collect_records(Loader([note()]), CASE)
    facts, _ = extract_candidates(records, [first], Provider(lambda p: candidate(p, node_id=first.node_id)))
    assert build_shadow_proof(revision, facts).state == expected
    root.compilation_status = "partial"
    proof = build_shadow_proof(revision, facts)
    assert proof.state == "UNKNOWN" and len(proof.children) == 2
