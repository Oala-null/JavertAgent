# -*- coding: utf-8 -*-
"""prompt-fit 集成测试: --vars / --interactive / --auto 三模式."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from javert.audit.rule import Rule
from javert.audit.rule_loader import load_rule
from javert.audit.rule_writer import compute_render_hash, write_rule
from javert.cli import main
from javert.templating.template_model import Template, TemplateField


def _write_dummy_template(templates_dir: Path) -> Path:
    """造一个 M9 模板, 含 master_prompt + 一个 str field + 一个 list field + aux 模板."""
    p = templates_dir / "M9.yaml"
    body = (
        "template_id: M9\n"
        "name: 测试模板\n"
        "description: 简单 personalize\n"
        "master_prompt: |\n"
        "  关注: {{noun}}\n"
        "  关键词: {% for k in kws %}{{k}}{% if not loop.last %},{% endif %}{% endfor %}\n"
        "keywords_template: |\n"
        "  {% for k in kws %}- {{k}}\n"
        "  {% endfor %}\n"
        "tools_template: |\n"
        "  - search_fees\n"
        "  - search_notes\n"
        "signal_template: |\n"
        "  signal-{{noun}}\n"
        "fields:\n"
        "  - name: noun\n"
        "    type: str\n"
        "    desc: 主语\n"
        "    required: true\n"
        "  - name: kws\n"
        "    type: list[str]\n"
        "    desc: 关键词\n"
        "    required: true\n"
    )
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()

    rule = Rule(
        rule_id="R045",
        domain="骨科",
        violation_type="重复收费",
        question="开展 X, 重复收取 Y 费用.",
        example="",
    )
    write_rule(rule, rules_dir / "R045.yaml")
    _write_dummy_template(templates_dir)

    monkeypatch.setenv("JAVERT_RULES_DIR", str(rules_dir))
    monkeypatch.setenv("JAVERT_TEMPLATES_DIR", str(templates_dir))
    monkeypatch.setenv("JAVERT_AUDIT_DB", str(tmp_path / "audit.sqlite"))
    monkeypatch.setenv("JAVERT_DATA_DIR", str(tmp_path / "data"))

    from javert.config import reset_config_cache
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


# ---- happy path: --vars 写回 ----

def test_vars_mode_writes_prompt_addon(isolated_project):
    runner = CliRunner()
    vars_path = isolated_project / "vars.json"
    vars_path.write_text(
        json.dumps({"noun": "肿瘤", "kws": ["a", "b"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = runner.invoke(
        main,
        ["prompt-fit", "R045", "--template", "M9", "--vars", str(vars_path)],
    )
    assert result.exit_code == 0, result.output
    assert "✓ R045 written from M9" in result.output

    rule_path = isolated_project / "rules" / "R045.yaml"
    rule = load_rule(rule_path)
    assert "关注: 肿瘤" in rule.prompt_addon
    assert "关键词: a,b" in rule.prompt_addon
    assert rule.trigger_keywords == ["a", "b"]
    assert rule.suggested_tools == ["search_fees", "search_notes"]
    assert "signal-肿瘤" in rule.expected_signal
    assert rule.derived_from_template == "M9"


# ---- --dry-run 不写盘 ----

def test_dry_run_does_not_write_to_yaml(isolated_project):
    runner = CliRunner()
    rule_path = isolated_project / "rules" / "R045.yaml"
    original_text = rule_path.read_text(encoding="utf-8")

    vars_path = isolated_project / "vars.json"
    vars_path.write_text(
        json.dumps({"noun": "x", "kws": ["y"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = runner.invoke(
        main,
        [
            "prompt-fit", "R045",
            "--template", "M9",
            "--vars", str(vars_path),
            "--dry-run", "--output", "-",
        ],
    )
    assert result.exit_code == 0
    assert "关注: x" in result.output
    # 文件未改
    assert rule_path.read_text(encoding="utf-8") == original_text


# ---- 三模式互斥 (vars + interactive) ----

def test_modes_mutually_exclusive(isolated_project):
    runner = CliRunner()
    vars_path = isolated_project / "vars.json"
    vars_path.write_text("{}", encoding="utf-8")
    result = runner.invoke(
        main,
        [
            "prompt-fit", "R045",
            "--template", "M9",
            "--vars", str(vars_path),
            "--interactive",
        ],
    )
    assert result.exit_code == 2
    assert "互斥" in result.output or "mutually" in result.output.lower()


# ---- 缺所有 mode 提示 ----

def test_no_mode_specified_exits_2(isolated_project):
    runner = CliRunner()
    result = runner.invoke(
        main, ["prompt-fit", "R045", "--template", "M9"],
    )
    assert result.exit_code == 2


# ---- 未知模板 ----

def test_unknown_template_exits_2(isolated_project):
    runner = CliRunner()
    vars_path = isolated_project / "vars.json"
    vars_path.write_text("{}", encoding="utf-8")
    result = runner.invoke(
        main,
        ["prompt-fit", "R045", "--template", "M99", "--vars", str(vars_path)],
    )
    assert result.exit_code == 2
    assert "M99" in result.output


# ---- vars 缺 required 字段 → 1 ----

def test_missing_required_field_exits_1(isolated_project):
    runner = CliRunner()
    vars_path = isolated_project / "vars.json"
    vars_path.write_text(
        json.dumps({"noun": "x"}, ensure_ascii=False),  # 缺 kws
        encoding="utf-8",
    )
    result = runner.invoke(
        main,
        ["prompt-fit", "R045", "--template", "M9", "--vars", str(vars_path)],
    )
    assert result.exit_code == 1
    assert "kws" in result.output


# ---- 未知 rule_id → 1 ----

def test_unknown_rule_id_exits_1(isolated_project):
    runner = CliRunner()
    vars_path = isolated_project / "vars.json"
    vars_path.write_text(
        json.dumps({"noun": "x", "kws": ["a"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = runner.invoke(
        main,
        ["prompt-fit", "R999", "--template", "M9", "--vars", str(vars_path)],
    )
    assert result.exit_code == 1


# ---- --auto + 用户拒绝 → 0 (aborted) ----

class _FakeDrafter:
    def __init__(self, vars_to_return: dict):
        self._vars = vars_to_return

    def draft_vars(self, rule, template, **kwargs):
        return dict(self._vars)


def test_auto_mode_user_rejects(isolated_project, monkeypatch):
    from javert.templating import prompt_fit_runner as pfr

    fake = _FakeDrafter({"noun": "auto-drafted", "kws": ["x"]})
    rule_path = isolated_project / "rules" / "R045.yaml"
    original = rule_path.read_text(encoding="utf-8")

    runner = CliRunner()
    monkeypatch.setattr(pfr, "LlmDrafter", lambda: fake)
    # 用 stdin 喂 'N\n'
    result = runner.invoke(
        main,
        ["prompt-fit", "R045", "--template", "M9", "--auto"],
        input="N\n",
    )
    assert result.exit_code == 0
    assert "aborted by user" in result.output
    assert rule_path.read_text(encoding="utf-8") == original  # 未改


def test_auto_mode_user_accepts(isolated_project, monkeypatch):
    from javert.templating import prompt_fit_runner as pfr

    fake = _FakeDrafter({"noun": "approved", "kws": ["k1"]})
    runner = CliRunner()
    monkeypatch.setattr(pfr, "LlmDrafter", lambda: fake)
    result = runner.invoke(
        main,
        ["prompt-fit", "R045", "--template", "M9", "--auto"],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert "✓ R045 written from M9" in result.output

    rule = load_rule(isolated_project / "rules" / "R045.yaml")
    assert "关注: approved" in rule.prompt_addon
    assert rule.derived_from_template == "M9"


# ---- --save-vars 写盘 ----

def test_save_vars_writes_json(isolated_project):
    runner = CliRunner()
    vars_path = isolated_project / "vars.json"
    save_path = isolated_project / "saved.json"
    vars_path.write_text(
        json.dumps({"noun": "x", "kws": ["k1"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = runner.invoke(
        main,
        [
            "prompt-fit", "R045",
            "--template", "M9",
            "--vars", str(vars_path),
            "--save-vars", str(save_path),
        ],
    )
    assert result.exit_code == 0
    assert save_path.exists()
    saved = json.loads(save_path.read_text(encoding="utf-8"))
    assert saved == {"noun": "x", "kws": ["k1"]}


# ---- R191 反推 byte-equal (使用真实 configs/) ----

def test_r191_round_trip_byte_equal():
    """对比真实 R191.yaml 与 M1.yaml + m1_r191_vars.json 渲染产物."""
    project_root = Path(__file__).resolve().parents[1]
    r191_path = project_root / "configs" / "rules" / "R191.yaml"
    if not r191_path.exists():
        pytest.skip("R191.yaml 不存在")
    original = yaml.safe_load(r191_path.read_text(encoding="utf-8"))["prompt_addon"]

    from javert.templating import load_template, render_template
    template = load_template(project_root / "configs" / "templates" / "M1.yaml")
    vars_dict = json.loads(
        (project_root / "docs" / "m1_r191_vars.json").read_text(encoding="utf-8")
    )
    rendered = render_template(template, vars_dict)["prompt_addon"]
    assert rendered == original, (
        f"R191 round-trip 失败: rendered={len(rendered)} chars, original={len(original)} chars"
    )


# ==== harden-agent-loop: prompt-fit 覆盖护栏 ====

def _tamper_prompt_addon(rule_path: Path, extra: str) -> None:
    """人工手改 prompt_addon (保留旧 render_hash → 造成 hash 不符)."""
    from ruamel.yaml import YAML
    y = YAML()
    with open(rule_path, encoding="utf-8") as f:
        data = y.load(f)
    data["prompt_addon"] = str(data["prompt_addon"]) + extra
    with open(rule_path, "w", encoding="utf-8") as f:
        y.dump(data, f)


def _vars(isolated_project) -> Path:
    p = isolated_project / "vars.json"
    p.write_text(json.dumps({"noun": "肿瘤", "kws": ["a", "b"]}, ensure_ascii=False), encoding="utf-8")
    return p


def test_prompt_fit_refuses_after_manual_edit(isolated_project):
    """手改 prompt_addon 后再 prompt-fit → hash 不符, 拒绝覆盖 (exit 1)."""
    runner = CliRunner()
    vars_path = _vars(isolated_project)
    rule_path = isolated_project / "rules" / "R045.yaml"
    r1 = runner.invoke(main, ["prompt-fit", "R045", "--template", "M9", "--vars", str(vars_path)])
    assert r1.exit_code == 0, r1.output
    _tamper_prompt_addon(rule_path, "\n【专家手改】额外指令")
    r2 = runner.invoke(main, ["prompt-fit", "R045", "--template", "M9", "--vars", str(vars_path)])
    assert r2.exit_code == 1
    assert "拒绝覆盖" in r2.output
    assert "专家手改" in load_rule(rule_path).prompt_addon  # 手改未被覆盖


def test_prompt_fit_force_overrides_manual_edit(isolated_project):
    """--force → 无视 hash 不符强制覆盖 + 更新 render_hash."""
    runner = CliRunner()
    vars_path = _vars(isolated_project)
    rule_path = isolated_project / "rules" / "R045.yaml"
    runner.invoke(main, ["prompt-fit", "R045", "--template", "M9", "--vars", str(vars_path)])
    _tamper_prompt_addon(rule_path, "\n【专家手改】额外指令")
    r = runner.invoke(main, ["prompt-fit", "R045", "--template", "M9", "--vars", str(vars_path), "--force"])
    assert r.exit_code == 0, r.output
    assert "✓ R045 written from M9" in r.output
    rule = load_rule(rule_path)
    assert "专家手改" not in rule.prompt_addon                       # 被覆盖
    assert rule.render_hash == compute_render_hash(rule.prompt_addon)  # hash 已更新


def test_prompt_fit_missing_hash_warns_only(isolated_project):
    """旧规则无 render_hash 但 prompt_addon 非空 → 仅警告不拦截, 覆盖并补齐 hash."""
    runner = CliRunner()
    rule_path = isolated_project / "rules" / "R045.yaml"
    write_rule(
        Rule(rule_id="R045", domain="骨科", violation_type="重复收费",
             question="q", prompt_addon="旧的手写内容"),
        rule_path,
    )
    assert load_rule(rule_path).render_hash is None
    vars_path = _vars(isolated_project)
    r = runner.invoke(main, ["prompt-fit", "R045", "--template", "M9", "--vars", str(vars_path)])
    assert r.exit_code == 0, r.output
    assert "来源未知" in r.output
    assert load_rule(rule_path).render_hash is not None  # 首次补齐


def test_prompt_fit_consistent_hash_silent_overwrite(isolated_project):
    """连续两次同 vars 渲染 → hash 一致 (round-trip 稳定), 静默覆盖, 无警告无拒绝."""
    runner = CliRunner()
    vars_path = _vars(isolated_project)
    r1 = runner.invoke(main, ["prompt-fit", "R045", "--template", "M9", "--vars", str(vars_path)])
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(main, ["prompt-fit", "R045", "--template", "M9", "--vars", str(vars_path)])
    assert r2.exit_code == 0, r2.output
    assert "来源未知" not in r2.output
    assert "拒绝覆盖" not in r2.output
    assert "✓ R045 written from M9" in r2.output
