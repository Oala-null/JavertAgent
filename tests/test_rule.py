# -*- coding: utf-8 -*-
"""Rule 加载 / 写入 / 状态机测试."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from javert.audit.rule import Rule, RuleValidationError
from javert.audit.rule_loader import load_all, load_rule
from javert.audit.rule_writer import dump_rule_to_string, update_status, write_rule
from javert.audit.state_machine import StatusTransitionError, validate_transition


def _good_rule_dict() -> dict:
    return {
        "rule_id": "R191",
        "domain": "肿瘤",
        "violation_type": "重复收费",
        "question": "开展肿瘤全身断层显像, 重复收取人工报告费用.",
        "example": "示例: ...",
        "status": "drafting",
        "prompt_addon": "",
        "trigger_keywords": [],
        "suggested_tools": [],
        "expected_signal": "",
        "notes": "",
    }


def test_load_good_rule(tmp_path: Path):
    p = tmp_path / "R191.yaml"
    p.write_text(yaml.safe_dump(_good_rule_dict(), allow_unicode=True), encoding="utf-8")
    rule = load_rule(p)
    assert rule.rule_id == "R191"
    assert rule.status == "drafting"


def test_load_missing_required_field(tmp_path: Path):
    bad = _good_rule_dict()
    bad.pop("rule_id")
    p = tmp_path / "Rxxx.yaml"
    p.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuleValidationError) as exc:
        load_rule(p)
    assert "rule_id" in str(exc.value)


def test_load_invalid_status(tmp_path: Path):
    bad = _good_rule_dict()
    bad["status"] = "in_progress"
    p = tmp_path / "R191.yaml"
    p.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuleValidationError) as exc:
        load_rule(p)
    assert "status" in str(exc.value)


def test_load_all_dir(tmp_path: Path):
    for rid in ["R001", "R191", "R193"]:
        d = _good_rule_dict()
        d["rule_id"] = rid
        (tmp_path / f"{rid}.yaml").write_text(
            yaml.safe_dump(d, allow_unicode=True), encoding="utf-8"
        )
    rules = load_all(tmp_path)
    assert set(rules.keys()) == {"R001", "R191", "R193"}


def test_round_trip_preserves_field_order(tmp_path: Path):
    rule = Rule(**_good_rule_dict())
    p = tmp_path / "R191.yaml"
    write_rule(rule, p)
    text = p.read_text(encoding="utf-8")
    # 字段顺序: rule_id 先于 domain 先于 status
    assert text.index("rule_id") < text.index("domain") < text.index("status")
    # 重新加载得到等价 Rule
    again = load_rule(p)
    assert again.model_dump() == rule.model_dump()


def test_update_status_preserves_other_fields(tmp_path: Path):
    rule = Rule(**{**_good_rule_dict(), "prompt_addon": "测试 addon", "notes": "保留我"})
    p = tmp_path / "R191.yaml"
    write_rule(rule, p)
    update_status(p, "ready")
    again = load_rule(p)
    assert again.status == "ready"
    assert again.prompt_addon == "测试 addon"
    assert again.notes == "保留我"


def test_dump_rule_to_string_keeps_order():
    rule = Rule(**_good_rule_dict())
    text = dump_rule_to_string(rule)
    assert text.index("rule_id") < text.index("status") < text.index("notes")


# rule_id 命名段 (R\d{3} 0325 序号 + RD\d{2,3} 药品类规则)
def test_load_rd_namespaced_rule(tmp_path: Path):
    """药品类规则 RD01 加载成功 (rule_id pattern 已放宽支持 RD 段)."""
    d = _good_rule_dict()
    d["rule_id"] = "RD01"
    p = tmp_path / "RD01.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    rule = load_rule(p)
    assert rule.rule_id == "RD01"


def test_load_r_numbered_rule_still_valid(tmp_path: Path):
    """既有 R007 风格仍加载成功 (backwards compat)."""
    d = _good_rule_dict()
    d["rule_id"] = "R007"
    p = tmp_path / "R007.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    rule = load_rule(p)
    assert rule.rule_id == "R007"


@pytest.mark.parametrize("bad_id", ["RD", "RD1234", "RDx"])
def test_load_malformed_drug_rule_id_rejected(tmp_path: Path, bad_id: str):
    """RD / RD1234 / RDx 非法 rule_id 被拒 (RD 后必须是 2-3 位纯数字)."""
    d = _good_rule_dict()
    d["rule_id"] = bad_id
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuleValidationError) as exc:
        load_rule(p)
    assert "rule_id" in str(exc.value)


def test_load_all_discovers_rd_via_glob(tmp_path: Path):
    """load_all 的 glob("R*.yaml") 天然收 RD*.yaml; 重复 rule_id 抛错."""
    for rid in ["R191", "RD01", "RD42"]:
        d = _good_rule_dict()
        d["rule_id"] = rid
        (tmp_path / f"{rid}.yaml").write_text(
            yaml.safe_dump(d, allow_unicode=True), encoding="utf-8"
        )
    rules = load_all(tmp_path)
    assert set(rules.keys()) == {"R191", "RD01", "RD42"}


# 状态机
def test_forward_transition_ok():
    validate_transition("drafting", "ready")
    validate_transition("ready", "validated")


def test_backward_blocked_without_force():
    with pytest.raises(StatusTransitionError):
        validate_transition("validated", "ready")
    with pytest.raises(StatusTransitionError):
        validate_transition("ready", "drafting")


def test_backward_with_force_ok():
    validate_transition("validated", "ready", force=True)


def test_abandoned_reachable_from_anywhere():
    for current in ["drafting", "ready", "validated"]:
        validate_transition(current, "abandoned")  # 不抛


def test_skip_state_transition_blocked():
    # drafting → validated (跳级) 不允许
    with pytest.raises(StatusTransitionError):
        validate_transition("drafting", "validated")


# priority 字段
def test_load_rule_with_priority(tmp_path: Path):
    d = _good_rule_dict()
    d["priority"] = "P0"
    p = tmp_path / "R191.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    rule = load_rule(p)
    assert rule.priority == "P0"


def test_load_rule_without_priority_defaults_p3(tmp_path: Path):
    """旧 yaml (无 priority 字段) 加载时应默认 P3, 不报错."""
    d = _good_rule_dict()
    # 不写 priority
    p = tmp_path / "R191.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    rule = load_rule(p)
    assert rule.priority == "P3"


def test_load_rule_invalid_priority(tmp_path: Path):
    d = _good_rule_dict()
    d["priority"] = "P9"
    p = tmp_path / "R191.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuleValidationError) as exc:
        load_rule(p)
    assert "priority" in str(exc.value)


def test_priority_written_after_status(tmp_path: Path):
    rule = Rule(**{**_good_rule_dict(), "priority": "P0"})
    p = tmp_path / "R191.yaml"
    write_rule(rule, p)
    text = p.read_text(encoding="utf-8")
    assert text.index("status") < text.index("priority") < text.index("prompt_addon")


# handling_level 字段
def test_handling_level_load_default_validation_and_order(tmp_path: Path):
    assert Rule(**_good_rule_dict()).handling_level == "可疑（警告）"

    rule = Rule(**{**_good_rule_dict(), "handling_level": "违规（阻断）"})
    p = tmp_path / "R191.yaml"
    write_rule(rule, p)
    text = p.read_text(encoding="utf-8")
    assert text.index("priority") < text.index("handling_level") < text.index("prompt_addon")
    assert load_rule(p).handling_level == "违规（阻断）"

    bad = {**_good_rule_dict(), "handling_level": "合规（已检测）"}
    p.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuleValidationError) as exc:
        load_rule(p)
    assert "handling_level" in str(exc.value)


# derived_from_template 字段
def test_load_rule_with_derived_from_template(tmp_path: Path):
    d = _good_rule_dict()
    d["derived_from_template"] = "M1"
    p = tmp_path / "R191.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    rule = load_rule(p)
    assert rule.derived_from_template == "M1"


def test_load_rule_without_derived_from_template_defaults_none(tmp_path: Path):
    """既有 yaml (无该字段) 加载默认 None, 不报错."""
    d = _good_rule_dict()
    p = tmp_path / "R191.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    rule = load_rule(p)
    assert rule.derived_from_template is None


def test_derived_from_template_written_after_notes(tmp_path: Path):
    rule = Rule(**{**_good_rule_dict(), "notes": "v0.2", "derived_from_template": "M1"})
    p = tmp_path / "R045.yaml"
    write_rule(rule, p)
    text = p.read_text(encoding="utf-8")
    assert text.index("notes") < text.index("derived_from_template")


# drug_rule_type 字段 (药品类 M8 规则)
def test_load_rule_with_drug_rule_type(tmp_path: Path):
    d = _good_rule_dict()
    d["rule_id"] = "RD01"
    d["drug_rule_type"] = "禁忌症"
    p = tmp_path / "RD01.yaml"
    p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    rule = load_rule(p)
    assert rule.drug_rule_type == "禁忌症"


def test_drug_rule_type_defaults_none_and_round_trips(tmp_path: Path):
    """非药品规则默认 None; 药品规则 write_rule 后能从 yaml 读回 (不被 model_dump 丢)."""
    plain = Rule(**_good_rule_dict())
    assert plain.drug_rule_type is None
    rule = Rule(**{**_good_rule_dict(), "rule_id": "RD03",
                   "drug_rule_type": "限适应症"})
    p = tmp_path / "RD03.yaml"
    write_rule(rule, p)
    text = p.read_text(encoding="utf-8")
    assert "drug_rule_type: 限适应症" in text
    assert load_rule(p).drug_rule_type == "限适应症"
