# -*- coding: utf-8 -*-
"""prompt-fit 总编排: vars/interactive/auto 三模式 → render → 写盘.

run_prompt_fit() 返回 exit code:
    0 — 成功写盘 OR --auto 用户拒绝 OR --dry-run
    1 — render / load 失败
    2 — 调用方参数错误 (mode 互斥失败等)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

import click
import yaml

from javert.audit.rule import Rule
from javert.audit.rule_loader import load_rule
from javert.audit.rule_writer import update_from_template_render
from javert.config import JavertConfig, get_config

from .llm_drafter import DrafterError, LlmDrafter
from .renderer import RenderError, render_template
from .template_loader import TemplateValidationError, load_template
from .template_model import Template, TemplateField

logger = logging.getLogger("javert.templating.prompt_fit")

Mode = str  # "vars" | "interactive" | "auto"


def _load_vars_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"vars 文件不存在: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"vars 文件顶层必须是 mapping, 实为 {type(data).__name__}")
    return data


def _coerce_value(field: TemplateField, raw: str) -> Any:
    """interactive 模式: 把 stdin 输入字符串转为字段声明类型."""
    s = raw.strip()
    if field.type == "str" or field.type == "enum":
        return s
    if field.type == "int":
        return int(s)
    if field.type == "bool":
        lower = s.lower()
        if lower in ("y", "yes", "true", "t", "1"):
            return True
        if lower in ("n", "no", "false", "f", "0"):
            return False
        raise ValueError(f"无法解析为 bool: {raw!r}")
    if field.type == "list[str]":
        # 逗号分隔
        return [x.strip() for x in s.split(",") if x.strip()]
    raise ValueError(f"未知类型 {field.type}")


def _interactive_collect(
    template: Template,
    *,
    seed: dict[str, Any] | None = None,
    echo: Callable[[str], None] = click.echo,
    prompt_fn: Callable[..., str] = click.prompt,
) -> dict[str, Any]:
    """逐字段问操作者; 回车接受 default (若有). seed 提供初值用于 'auto 后微调'."""
    out: dict[str, Any] = {}
    seed = seed or {}
    for field in template.fields:
        echo(f"\n[{field.name}] {field.desc or '(no desc)'}")
        if field.options:
            echo(f"  类型 enum, options={field.options}")
        else:
            echo(f"  类型 {field.type}")
        existing = seed.get(field.name)
        if existing is not None:
            echo(f"  当前值: {existing!r} (回车保留)")
        default_disp: str | None = None
        if existing is not None:
            default_disp = (
                ",".join(existing)
                if isinstance(existing, list)
                else str(existing)
            )
        elif field.default is not None:
            default_disp = (
                ",".join(field.default)
                if isinstance(field.default, list)
                else str(field.default)
            )
        raw = prompt_fn(
            f"  填值",
            default=default_disp if default_disp is not None else "",
            show_default=default_disp is not None,
        )
        if not raw.strip() and not field.required and existing is None and field.default is None:
            continue
        if not raw.strip() and existing is not None:
            out[field.name] = existing
            continue
        try:
            out[field.name] = _coerce_value(field, raw)
        except ValueError as exc:
            raise click.UsageError(f"字段 {field.name} 输入无效: {exc}")
    return out


def _write_dry_run_output(
    rendered: dict[str, Any],
    output_path: str | None,
    stdout: TextIO,
) -> None:
    body = rendered["prompt_addon"]
    if output_path in (None, "-"):
        stdout.write(body)
        if not body.endswith("\n"):
            stdout.write("\n")
    else:
        Path(output_path).write_text(body, encoding="utf-8")


def run_prompt_fit(
    rule_id: str,
    template_id: str,
    *,
    mode: Mode,
    vars_file: str | None = None,
    dry_run: bool = False,
    output_path: str | None = None,
    save_vars: str | None = None,
    config: JavertConfig | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    drafter: LlmDrafter | None = None,
    confirm_input: Callable[[], str] | None = None,
) -> int:
    """把模板套规则, 渲染 prompt_addon 写回 Rule yaml."""
    cfg = config or get_config()
    so = stdout or sys.stdout
    se = stderr or sys.stderr

    # --- 1. 加载 rule + template ---
    rule_path = cfg.rules_path / f"{rule_id}.yaml"
    try:
        rule = load_rule(rule_path)
    except Exception as exc:
        se.write(f"✗ 加载规则失败: {exc}\n")
        return 1

    template_path = cfg.templates_path / f"{template_id}.yaml"
    if not template_path.exists():
        se.write(f"✗ 未知 template_id: {template_id}\n")
        return 2
    try:
        template = load_template(template_path)
    except TemplateValidationError as exc:
        se.write(f"✗ 加载模板失败: {exc}\n")
        return 1

    # --- 2. 取得 vars dict ---
    raw_vars: dict[str, Any] = {}
    if mode == "vars":
        if not vars_file:
            se.write("✗ --vars 需要文件路径\n")
            return 2
        try:
            raw_vars = _load_vars_file(Path(vars_file))
        except (FileNotFoundError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            se.write(f"✗ 读取 vars 失败: {exc}\n")
            return 1
    elif mode == "interactive":
        so.write(f"=== prompt-fit {rule_id} / {template_id} (interactive) ===\n")
        try:
            raw_vars = _interactive_collect(template)
        except click.UsageError as exc:
            se.write(f"✗ {exc}\n")
            return 1
    elif mode == "auto":
        so.write(f"=== prompt-fit {rule_id} / {template_id} (auto) ===\n")
        drafter = drafter or LlmDrafter()
        try:
            raw_vars = drafter.draft_vars(rule, template)
        except DrafterError as exc:
            se.write(f"✗ LLM 起草失败: {exc}\n")
            return 1
        so.write("\n--- LLM 起草 vars ---\n")
        so.write(json.dumps(raw_vars, ensure_ascii=False, indent=2) + "\n")
    else:
        se.write(f"✗ 未知 mode: {mode}\n")
        return 2

    # --- 3. 渲染 ---
    try:
        rendered = render_template(template, raw_vars)
    except RenderError as exc:
        se.write(f"✗ 渲染失败: {exc}\n")
        return 1
    except Exception as exc:
        # validate_vars 错也会到这里
        se.write(f"✗ vars 校验失败: {exc}\n")
        return 1

    # --- 4. dry-run 早返 ---
    if dry_run:
        _write_dry_run_output(rendered, output_path, so)
        return 0

    # --- 5. auto 模式: 给用户看一眼再确认 ---
    if mode == "auto":
        so.write("\n--- 渲染后的 prompt_addon (预览) ---\n")
        so.write(rendered["prompt_addon"])
        if not rendered["prompt_addon"].endswith("\n"):
            so.write("\n")
        so.write("\n是否写入 R045.yaml 等规则文件? [y/N]: ".replace("R045", rule.rule_id))
        so.flush()
        answer = (confirm_input() if confirm_input else (stdin or sys.stdin).readline()).strip().lower()
        if answer != "y":
            so.write("aborted by user\n")
            return 0

    # --- 6. 写盘 ---
    update_from_template_render(
        rule_path,
        prompt_addon=rendered["prompt_addon"],
        template_id=template.template_id,
        trigger_keywords=rendered.get("trigger_keywords"),
        suggested_tools=rendered.get("suggested_tools"),
        expected_signal=rendered.get("expected_signal"),
    )
    so.write(f"✓ {rule.rule_id} written from {template.template_id}\n")

    # --- 7. save-vars (interactive / auto 之后留底) ---
    if save_vars:
        save_path = Path(save_vars)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(
            json.dumps(raw_vars, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        so.write(f"  vars saved → {save_path}\n")

    return 0
