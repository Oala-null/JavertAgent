# -*- coding: utf-8 -*-
"""CD 规则命名空间、受控发现和 Router 元数据回归。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from javert.audit.result import AuditResult
from javert.audit.rule import Rule, RuleValidationError
from javert.audit.rule_loader import load_all, load_rule
from javert.audit.rule_writer import dump_rule_to_string, write_rule
from javert.commands.audit_patient import _resolve_selection
from scripts import build_rule_mapping


ROOT = Path(__file__).resolve().parents[1]


def _rule(rule_id: str, *, status: str = "drafting") -> Rule:
    values = {
        "rule_id": rule_id,
        "domain": "测试",
        "violation_type": "测试",
        "question": "测试问题",
        "status": status,
        "priority": "P3",
    }
    if rule_id.startswith("CD"):
        values.update(
            rule_kind="chronic_disease_qualification",
            clinical_criteria_ref=f"hlj-outpatient-chronic-2025/{rule_id}",
        )
    return Rule(**values)


def _result(rule_id: str) -> AuditResult:
    return AuditResult(
        run_id="aud_cdregistry01",
        rule_id=rule_id,
        patient_id="SYNTH-CD-001",
        verdict="CLEAN",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("rule_id", ["CD01", "CD20", "CD999"])
def test_rule_and_audit_result_accept_valid_cd_ids(rule_id: str):
    assert _rule(rule_id).rule_id == rule_id
    assert _result(rule_id).rule_id == rule_id


@pytest.mark.parametrize("rule_id", ["C01", "CD1", "CD0000", "CD-01"])
def test_rule_and_audit_result_reject_invalid_cd_ids(rule_id: str):
    with pytest.raises(ValidationError):
        _rule(rule_id)
    with pytest.raises(ValidationError):
        _result(rule_id)


def test_cd_rule_kind_and_reference_are_paired():
    with pytest.raises(ValidationError, match="clinical_criteria_ref"):
        Rule(
            rule_id="CD01",
            rule_kind="chronic_disease_qualification",
            domain="测试",
            violation_type="测试",
            question="测试问题",
        )
    values = _rule("CD01").model_dump()
    values["clinical_criteria_ref"] = "hlj-outpatient-chronic-2025/CD02"
    with pytest.raises(ValidationError, match="尾部必须与 rule_id 一致"):
        Rule(**values)


def test_filename_must_match_declared_rule_id(tmp_path: Path):
    write_rule(_rule("CD01"), tmp_path / "CD02.yaml")
    with pytest.raises(RuleValidationError, match="文件名与 rule_id 不一致"):
        load_rule(tmp_path / "CD02.yaml")


def test_loader_discovers_all_namespaces_and_legacy_defaults(tmp_path: Path):
    for rule_id in ("R191", "RD04", "CD01"):
        write_rule(_rule(rule_id), tmp_path / f"{rule_id}.yaml")
    rules = load_all(tmp_path)
    assert set(rules) == {"R191", "RD04", "CD01"}
    assert rules["R191"].rule_kind == "audit"
    assert rules["RD04"].clinical_criteria_ref is None
    assert rules["CD01"].clinical_criteria_ref.endswith("/CD01")


def test_rule_writer_orders_new_optional_fields_after_id():
    text = dump_rule_to_string(_rule("CD01"))
    assert text.index("rule_id") < text.index("rule_kind")
    assert text.index("rule_kind") < text.index("clinical_criteria_ref") < text.index("domain")


def test_cd_is_explicitly_selectable_but_excluded_by_default():
    rules = {
        "R191": _rule("R191", status="ready"),
        "RD04": _rule("RD04", status="ready"),
        "CD01": _rule("CD01", status="drafting"),
    }
    selected, _, _ = _resolve_selection(rules, "P3", None)
    assert [rule.rule_id for rule in selected] == ["R191", "RD04"]
    selected_all, _, _ = _resolve_selection(rules, "all", None)
    assert [rule.rule_id for rule in selected_all] == ["R191", "RD04"]
    explicit, label, _ = _resolve_selection(rules, "P3", "CD01")
    assert [rule.rule_id for rule in explicit] == ["CD01"]
    assert "explicit" in label


def test_repository_contains_exactly_twenty_drafting_reference_only_cd_rules():
    rules = load_all(ROOT / "configs/rules")
    cd_rules = {rid: rule for rid, rule in rules.items() if rid.startswith("CD")}
    assert set(cd_rules) == {f"CD{number:02d}" for number in range(1, 21)}
    for rule_id, rule in cd_rules.items():
        assert rule.status == "drafting"
        assert rule.rule_kind == "chronic_disease_qualification"
        assert rule.clinical_criteria_ref == f"hlj-outpatient-chronic-2025/{rule_id}"
        assert rule.prompt_addon == ""
        assert rule.trigger_keywords
        assert rule.trigger_codes == []


def test_mapping_scan_keeps_cd_in_index_metadata_but_out_of_violation_mapping(
    tmp_path: Path, monkeypatch,
):
    for rule_id in ("R191", "CD01"):
        write_rule(_rule(rule_id), tmp_path / f"{rule_id}.yaml")
    monkeypatch.setattr(build_rule_mapping, "YAML_DIR", tmp_path)
    records = build_rule_mapping.scan_javert_yamls()
    by_id = {item["rule_id"]: item for item in records}
    assert by_id["CD01"]["rule_kind"] == "chronic_disease_qualification"
    assert by_id["CD01"]["clinical_criteria_ref"].endswith("/CD01")

    mapping = build_rule_mapping.build_mapping(
        records,
        {"entries": [], "keyword_index": {}},
    )
    assert mapping["summary"]["excluded_non_audit_rules"] == 1
    assert "CD01" not in mapping["case_B_javert_only"]["rules"]
    assert "CD01" not in mapping["reverse_index_javert_to_java"]


def test_headline_gate_rejects_cd_internal_id():
    from javert.audit.headline import validate_headline

    accepted, reason = validate_headline(
        "CD01 再生障碍性贫血资料不足，待人工复核",
        "INCONCLUSIVE",
    )
    assert accepted == ""
    assert reason == "internal_term"
