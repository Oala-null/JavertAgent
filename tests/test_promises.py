# -*- coding: utf-8 -*-
"""Promise schema、治理、CLI 与离线 harness。"""

from __future__ import annotations

import socket

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from javert.cli import main
from javert.promises.harness import offline_guard, run_harness
from javert.promises.loader import (
    _validate_registry_and_scope,
    _validate_version_chains,
    load_repository,
    validate_behavior_mapping,
)
from javert.promises.models import (
    DriftCase,
    PromiseDefinition,
    PromiseTrace,
    definition_content_digest,
)
from javert.promises.registry import validate_definition_registry


def _definition(
    version: int = 1,
    status: str = "active",
    supersedes: int | None = None,
    *,
    promise_id: str = "PR-T001",
) -> PromiseDefinition:
    raw = {
        "asset_type": "promise_definition",
        "promise_id": promise_id,
        "version": version,
        "status": status,
        "phase": "decision_pre_llm",
        "kind": "refund-net-single-clean",
        "scope": {"rule_ids": ["R151"], "semantic_profile": "去标识测试边界"},
        "params": {
            "target_keywords": ["测试项目"],
            "excluded_keywords": [],
            "max_net_qty": 1,
        },
        "source_cases": ["DRIFT-T001"],
        "cases": ["CASE-T001-P", "CASE-T001-N"],
        "guarantee": "CLEAN",
        "finality": "LOCKED",
        "reason_code": "TEST_LOCKED_CLEAN",
        "confirmation": {
            "source_type": "test_reproduction",
            "reference": "去标识单元测试确认",
        },
        "version_note": "去标识测试版本",
        "supersedes": supersedes,
        "content_sha256": "sha256:" + "0" * 64,
    }
    raw["content_sha256"] = definition_content_digest(raw)
    return PromiseDefinition.model_validate(raw)


def test_strict_schema_accepts_safe_assets_and_rejects_extra_fields():
    definition = _definition()
    assert definition.finality == "LOCKED"
    raw = definition.model_dump(mode="json") | {"python_expression": "danger()"}
    with pytest.raises(ValidationError):
        PromiseDefinition.model_validate(raw)


@pytest.mark.parametrize("key", ["patient_id", "SYXH", "run_id", "password"])
def test_trace_rejects_sensitive_fact_types(key: str):
    with pytest.raises(ValidationError):
        PromiseTrace(
            promise_id="PR-T001",
            version=1,
            kind="refund-net-single-clean",
            finality="LOCKED",
            reason_code="TEST_LOCKED_CLEAN",
            facts={key: "sensitive-value"},
        )


def test_drift_case_status_is_closed_enum():
    with pytest.raises(ValidationError):
        DriftCase(
            case_id="DRIFT-T001",
            status="approved",
            title="去标识漂移",
            observed_behavior="观察到去标识错误输出",
            expected_behavior="应返回去标识正确输出",
            confirmation={
                "source_type": "test_reproduction",
                "reference": "去标识测试复现",
            },
        )


def test_registry_rejects_unknown_kind_and_unknown_params():
    with pytest.raises(ValueError, match="UNKNOWN_PROMISE_KIND"):
        validate_definition_registry(_definition().model_copy(update={"kind": "unknown"}))
    with pytest.raises(ValidationError):
        validate_definition_registry(
            _definition().model_copy(update={"params": {"target_keywords": ["x"], "code": "x"}})
        )


def test_unknown_rule_scope_fails_closed(tmp_path):
    definition = _definition().model_copy(
        update={"scope": _definition().scope.model_copy(update={"rule_ids": ["R999"]})}
    )
    issues = _validate_registry_and_scope([definition], tmp_path)
    assert {issue.code for issue in issues} == {"UNKNOWN_RULE_SCOPE"}


def test_version_chain_legal_replacement():
    issues = _validate_version_chains(
        [_definition(status="superseded"), _definition(2, "active", 1)]
    )
    assert issues == []


def test_version_chain_detects_fork_missing_version_and_cycle():
    fork = _validate_version_chains(
        [
            _definition(status="superseded"),
            _definition(2, "superseded", 1),
            _definition(3, "active", 1),
        ]
    )
    assert "PROMISE_VERSION_FORK" in {issue.code for issue in fork}

    missing = _validate_version_chains(
        [_definition(status="superseded"), _definition(3, "active", 1)]
    )
    assert "MISSING_PROMISE_VERSION" in {issue.code for issue in missing}

    cyclic_v1 = _definition(status="superseded").model_copy(update={"supersedes": 2})
    cyclic_v2 = _definition(2, "active", 1)
    cycle = _validate_version_chains([cyclic_v1, cyclic_v2])
    assert "PROMISE_VERSION_CYCLE" in {issue.code for issue in cycle}


def test_active_content_mutation_requires_new_digest():
    raw = _definition().model_dump(mode="json")
    raw["version_note"] = "被就地改写"
    with pytest.raises(ValidationError, match="内容摘要不匹配"):
        PromiseDefinition.model_validate(raw)


def test_repository_and_behavior_source_mapping_validate():
    repository = load_repository()
    assert len(repository.active_definitions) == 1
    assert validate_behavior_mapping() == []


def test_behavior_source_snapshot_fails_closed_when_unavailable(tmp_path):
    issues = validate_behavior_mapping(source_path=tmp_path / "missing.yaml")
    assert [(issue.code, issue.asset_id) for issue in issues] == [
        ("BEHAVIOR_SOURCE_UNAVAILABLE", "behavior_mapping")
    ]


def test_harness_is_repeatable_and_offline():
    first = run_harness(load_repository())
    second = run_harness(load_repository())
    assert first["status"] == second["status"] == "ok"
    assert [item["reason_code"] for item in first["results"]] == [
        item["reason_code"] for item in second["results"]
    ]
    with offline_guard(), pytest.raises(RuntimeError, match="OFFLINE"):
        socket.create_connection(("127.0.0.1", 1))


def test_promise_cli_human_and_json_contracts():
    runner = CliRunner()
    validate = runner.invoke(main, ["promise", "validate", "--json"])
    run = runner.invoke(main, ["promise", "run", "--json"])
    assert validate.exit_code == 0
    assert '"status":"ok"' in validate.output
    assert run.exit_code == 0
    assert '"failed":0' in run.output
