# -*- coding: utf-8 -*-
"""CLI 集成测试 — 用 Click CliRunner."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from javert.audit.rule import Rule
from javert.audit.rule_writer import write_rule
from javert.cli import main


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    """造一个临时项目根, 有 rules + audit.sqlite."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    db_path = tmp_path / "audit.sqlite"

    # 写一个 rule
    rule = Rule(
        rule_id="R191",
        domain="肿瘤",
        violation_type="重复收费",
        question="开展肿瘤全身断层显像, 重复收取人工报告费用.",
        example="",
    )
    write_rule(rule, rules_dir / "R191.yaml")

    # 重定向 config 路径
    monkeypatch.setenv("JAVERT_RULES_DIR", str(rules_dir))
    monkeypatch.setenv("JAVERT_AUDIT_DB", str(db_path))
    monkeypatch.setenv("JAVERT_DATA_DIR", str(tmp_path / "data"))

    from javert.config import reset_config_cache
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


def test_help_lists_all_subcommands():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for sub in ["init", "list", "dry-run", "run", "mark", "report", "show"]:
        assert sub in result.output


def test_unknown_subcommand_exits_2():
    runner = CliRunner()
    result = runner.invoke(main, ["frobnicate"])
    assert result.exit_code == 2
    # click 自带 "No such command"
    assert "frobnicate" in result.output or "No such command" in result.output


def test_list_command(isolated_project):
    runner = CliRunner()
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 0, result.output
    assert "R191" in result.output
    assert "priority" in result.output  # 表头列存在
    assert "P3" in result.output  # 默认 priority
    # 汇总行含优先级与状态分布
    assert "P0:" in result.output and "P3:" in result.output
    assert "drafting:" in result.output


def test_list_command_discovers_cd_rule(isolated_project):
    rules_dir = isolated_project / "rules"
    write_rule(
        Rule(
            rule_id="CD01",
            rule_kind="chronic_disease_qualification",
            clinical_criteria_ref="hlj-outpatient-chronic-2025/CD01",
            domain="门诊慢性病",
            violation_type="门诊慢性病认定条件评估",
            question="评估慢病认定条件。",
        ),
        rules_dir / "CD01.yaml",
    )
    result = CliRunner().invoke(main, ["list"])
    assert result.exit_code == 0, result.output
    assert "CD01" in result.output


def test_mark_forward_ok(isolated_project):
    runner = CliRunner()
    result = runner.invoke(main, ["mark", "R191", "--status", "ready"])
    assert result.exit_code == 0
    assert "drafting → ready" in result.output


def test_mark_backward_blocked(isolated_project):
    runner = CliRunner()
    runner.invoke(main, ["mark", "R191", "--status", "ready"])
    runner.invoke(main, ["mark", "R191", "--status", "validated"])
    result = runner.invoke(main, ["mark", "R191", "--status", "drafting"])
    assert result.exit_code == 2
    assert "force" in result.output.lower()


def test_mark_backward_with_force_ok(isolated_project):
    runner = CliRunner()
    runner.invoke(main, ["mark", "R191", "--status", "ready"])
    result = runner.invoke(main, ["mark", "R191", "--status", "drafting", "--force"])
    assert result.exit_code == 0


def test_mark_to_abandoned_no_force(isolated_project):
    runner = CliRunner()
    result = runner.invoke(main, ["mark", "R191", "--status", "abandoned"])
    assert result.exit_code == 0


def test_run_requires_patient_or_pilot(isolated_project):
    runner = CliRunner()
    result = runner.invoke(main, ["run", "R191"])
    assert result.exit_code == 2


def test_report_no_runs(isolated_project):
    runner = CliRunner()
    result = runner.invoke(main, ["report"])
    assert result.exit_code == 0
    assert "无审计记录" in result.output


def test_show_unknown_run_id(isolated_project):
    runner = CliRunner()
    result = runner.invoke(main, ["show", "aud_doesnotexist1"])
    assert result.exit_code == 1
    assert "未找到" in result.output


def test_subcommand_help_pages():
    runner = CliRunner()
    for sub in ["init", "list", "dry-run", "run", "mark", "report", "show"]:
        result = runner.invoke(main, [sub, "--help"])
        assert result.exit_code == 0, f"{sub} --help 失败: {result.output}"
