# -*- coding: utf-8 -*-
"""javert template list/show/validate CLI 集成测试."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from javert.cli import main


@pytest.fixture
def isolated_templates(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    tdir.mkdir()

    # M1 placeholder (empty)
    (tdir / "M1.yaml").write_text(
        "template_id: M1\nname: t1\ndescription: empty\n"
        "master_prompt: ''\nfields: []\n",
        encoding="utf-8",
    )
    # M2 ready (has master_prompt + fields)
    (tdir / "M2.yaml").write_text(
        "template_id: M2\nname: t2\ndescription: ready\n"
        "master_prompt: |\n  hi {{x}}\n"
        "fields:\n  - name: x\n    type: str\n    desc: var\n    required: true\n",
        encoding="utf-8",
    )
    # M3 partial (master_prompt 有, fields 空)
    (tdir / "M3.yaml").write_text(
        "template_id: M3\nname: t3\ndescription: partial\n"
        "master_prompt: |\n  hello\nfields: []\n",
        encoding="utf-8",
    )
    # M_bad: enum 缺 options (loader 抛错; 仅在 validate 单独读时遇到, list 全部加载会失败)
    # 为不污染 list 测试, 不放此文件

    monkeypatch.setenv("JAVERT_TEMPLATES_DIR", str(tdir))
    monkeypatch.setenv("JAVERT_RULES_DIR", str(tmp_path / "rules"))
    monkeypatch.setenv("JAVERT_AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("JAVERT_DATA_DIR", str(tmp_path / "data"))

    from javert.config import reset_config_cache
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


def test_template_list_shows_all(isolated_templates):
    runner = CliRunner()
    result = runner.invoke(main, ["template", "list"])
    assert result.exit_code == 0, result.output
    assert "M1" in result.output
    assert "M2" in result.output
    assert "M3" in result.output
    # 状态列
    assert "empty" in result.output
    assert "ready" in result.output
    assert "partial" in result.output


def test_template_show_dumps_master_prompt(isolated_templates):
    runner = CliRunner()
    result = runner.invoke(main, ["template", "show", "M2"])
    assert result.exit_code == 0
    assert "master_prompt" in result.output
    assert "hi {{x}}" in result.output
    assert "fields" in result.output
    # 字段行
    assert "x" in result.output and "str" in result.output


def test_template_show_unknown_exits_1(isolated_templates):
    runner = CliRunner()
    result = runner.invoke(main, ["template", "show", "M999"])
    assert result.exit_code == 1


def test_template_validate_empty(isolated_templates):
    runner = CliRunner()
    result = runner.invoke(main, ["template", "validate", "M1"])
    assert result.exit_code == 0
    assert "empty (not yet filled)" in result.output


def test_template_validate_ready(isolated_templates):
    runner = CliRunner()
    result = runner.invoke(main, ["template", "validate", "M2"])
    assert result.exit_code == 0
    assert "ready" in result.output


def test_template_validate_partial(isolated_templates):
    runner = CliRunner()
    result = runner.invoke(main, ["template", "validate", "M3"])
    assert result.exit_code == 0
    assert "partial" in result.output


def test_template_validate_schema_error(isolated_templates, tmp_path):
    """enum 缺 options 时 validate 报 1."""
    tdir = Path(__import__("os").environ["JAVERT_TEMPLATES_DIR"])
    (tdir / "M_bad.yaml").write_text(
        "template_id: M9\nname: bad\nmaster_prompt: x\n"
        "fields:\n  - name: f\n    type: enum\n    desc: bad\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["template", "validate", "M_bad"])
    assert result.exit_code == 1
    assert "enum" in result.output.lower()
