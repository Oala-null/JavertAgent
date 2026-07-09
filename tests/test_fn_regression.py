# -*- coding: utf-8 -*-
"""tests for scripts/fn_regression.py — FnCase schema 校验 + grade() 三档逻辑.

runner 本体 (真 LLM/hub 取数) 不进 pytest; 只测离线可验证的 schema 与判定逻辑.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent

# 从脚本文件加载 (scripts 非包)
_spec = importlib.util.spec_from_file_location(
    "fn_regression", ROOT / "scripts" / "fn_regression.py"
)
fn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fn)


def _result(verdict, reasoning="", evidence=None):
    ev = [SimpleNamespace(locator=l, text=t) for l, t in (evidence or [])]
    return SimpleNamespace(verdict=verdict, reasoning=reasoning, evidence=ev)


# ─── schema ─────────────────────────────────────────────────────────

def test_registered_cases_load_and_validate():
    """首批 4 例真 yaml 全部合法, 且不含 FN-004 (待专家裁定)."""
    cases = fn.load_cases()
    ids = {c.case_id for c in cases}
    assert ids == {"FN-001", "FN-002", "FN-003", "FN-005"}
    assert "FN-004" not in ids


def test_fn005_anchor_shape():
    (c,) = [c for c in fn.load_cases() if c.case_id == "FN-005"]
    assert c.rule_id == "R225"
    assert c.expected_verdict == "VIOLATION"
    assert "关节松动训练" in c.expected_evidence_keywords
    assert "颈椎" in c.expected_evidence_keywords


def test_invalid_case_id_rejected():
    with pytest.raises(ValidationError):
        fn.FnCase(case_id="FN-5", patient_id="1", rule_id="R225",
                  expected_verdict="VIOLATION")


def test_clean_expected_allowed():
    """误判修正锚: expected_verdict=CLEAN 合法."""
    c = fn.FnCase(case_id="FN-999", patient_id="1", rule_id="R063",
                  expected_verdict="CLEAN")
    assert c.expected_verdict == "CLEAN"


# ─── grade() 三档 ───────────────────────────────────────────────────

def test_grade_full_when_verdict_and_keywords_hit():
    case = fn.FnCase(case_id="FN-005", patient_id="1", rule_id="R225",
                     expected_verdict="VIOLATION",
                     expected_evidence_keywords=["关节松动训练", "颈椎"])
    r = _result("VIOLATION", reasoning="插管期间收关节松动训练(颈椎) 属禁忌")
    assert fn.grade(case, r)[0] == "full"


def test_grade_partial_when_keyword_missing():
    case = fn.FnCase(case_id="FN-005", patient_id="1", rule_id="R225",
                     expected_verdict="VIOLATION",
                     expected_evidence_keywords=["关节松动训练", "颈椎"])
    r = _result("VIOLATION", reasoning="收了关节松动训练但没点名部位")
    g, note = fn.grade(case, r)
    assert g == "partial" and "颈椎" in note


def test_grade_partial_when_only_inconclusive():
    case = fn.FnCase(case_id="FN-003", patient_id="1", rule_id="R155",
                     expected_verdict="VIOLATION",
                     expected_evidence_keywords=["干扰素测定"])
    r = _result("INCONCLUSIVE", reasoning="干扰素测定 证据不足")
    assert fn.grade(case, r)[0] == "partial"


def test_grade_miss_when_clean():
    case = fn.FnCase(case_id="FN-003", patient_id="1", rule_id="R155",
                     expected_verdict="VIOLATION",
                     expected_evidence_keywords=["干扰素测定"])
    assert fn.grade(case, _result("CLEAN"))[0] == "miss"


def test_grade_miss_when_no_rule():
    case = fn.FnCase(case_id="FN-001", patient_id="1", rule_id="TBD-溶栓虚构",
                     expected_verdict="VIOLATION")
    assert fn.grade(case, None) == ("miss", "无规则")


def test_grade_clean_anchor_strict_equality():
    """期望 CLEAN: 实得 V/I 判 miss (假阳性复发), 不走下限语义."""
    case = fn.FnCase(case_id="FN-004", patient_id="1", rule_id="R063",
                     expected_verdict="CLEAN")
    assert fn.grade(case, _result("CLEAN"))[0] == "full"
    assert fn.grade(case, _result("VIOLATION"))[0] == "miss"
    assert fn.grade(case, _result("INCONCLUSIVE"))[0] == "miss"
