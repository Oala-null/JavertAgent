# -*- coding: utf-8 -*-
"""LLM 兜底起草 personalization vars (Qwen3.5 via Qwen35Provider).

调用形态:
    drafter = LlmDrafter(provider)
    vars_dict = drafter.draft_vars(rule, template)

LLM 系统 prompt 让 Qwen 输出 fenced JSON, 仅含模板声明的 fields keys.
失败 (LLM 报错 / 输出非 JSON / 字段类型错) 抛 DrafterError, 由调用方决定降级路径.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from javert.audit.rule import Rule
from javert.tools.llm_provider import LlmUnavailableError, Qwen35Provider

from .template_model import Template
from .vars_validator import VarsValidationError, validate_vars

logger = logging.getLogger("javert.templating.llm_drafter")


class DrafterError(RuntimeError):
    """LLM 起草失败 (响应非 JSON / 校验失败 / LLM 端不可达)."""


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_payload(text: str) -> dict[str, Any]:
    """从 LLM 输出抓 fenced JSON, 失败时尝试整段直 parse."""
    m = _FENCED_JSON_RE.search(text)
    candidate = m.group(1) if m else text.strip()
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise DrafterError(f"LLM 输出无法解析为 JSON: {exc}; 原文摘要: {text[:200]!r}") from exc
    if not isinstance(obj, dict):
        raise DrafterError(f"LLM JSON 顶层不是对象, 实为 {type(obj).__name__}")
    return obj


class LlmDrafter:
    """把 rule + template 喂给 Qwen, 让它起草 personalization vars."""

    def __init__(self, provider: Qwen35Provider | None = None):
        self._provider = provider or Qwen35Provider()

    def _build_system_prompt(self, template: Template) -> str:
        fields_desc_lines: list[str] = []
        for f in template.fields:
            line = f"- {f.name} ({f.type}): {f.desc or '(无说明)'}"
            if f.options:
                line += f"; 必须取值 ∈ {f.options}"
            if f.default is not None:
                line += f"; 默认 {f.default!r}"
            if not f.required:
                line += " [可选]"
            fields_desc_lines.append(line)
        fields_doc = "\n".join(fields_desc_lines) or "(无 fields 声明)"
        return (
            "你是一位医保审计规则的填表助手. 我会给你:\n"
            "(1) 一条规则的 question 与 example;\n"
            "(2) 一个模板的 fields 字段表 (名字 + 类型 + 说明).\n"
            "请基于规则文本, 推断模板里每个 field 应填的中文值. 仅输出一个 fenced JSON 块, "
            "形如 ```json\\n{\\n  \"field_a\": \"...\",\\n  \"field_b\": [\\\"...\\\"]\\n}\\n```. "
            "不要写任何说明文字. 必填字段必须给值; 可选字段若不确定可省略. "
            "list[str] 类型用 JSON 数组. enum 类型必须从给定 options 选一个. bool 用 true/false. "
            f"\n\n# 模板 {template.template_id} ({template.name}) 的 fields:\n{fields_doc}"
        )

    def _build_user_prompt(self, rule: Rule) -> str:
        return (
            f"# 规则 {rule.rule_id} ({rule.violation_type} / {rule.domain})\n"
            f"## question\n{rule.question}\n\n"
            f"## example\n{rule.example or '(无示例)'}\n"
        )

    def draft_vars(
        self,
        rule: Rule,
        template: Template,
        *,
        max_tokens: int = 2048,
        temperature: float | None = 0.2,
    ) -> dict[str, Any]:
        """起草 vars dict; 经 validate_vars 校验后返回. 失败抛 DrafterError."""
        messages = [
            {"role": "system", "content": self._build_system_prompt(template)},
            {"role": "user", "content": self._build_user_prompt(rule)},
        ]
        try:
            resp = self._provider.chat(
                messages, max_tokens=max_tokens, temperature=temperature
            )
        except LlmUnavailableError as exc:
            raise DrafterError(f"LLM 端不可达: {exc}") from exc
        content = resp.get("content") or ""
        if not content.strip():
            raise DrafterError("LLM 返回空内容")
        raw_vars = _extract_json_payload(content)
        try:
            validated = validate_vars(template, raw_vars)
        except VarsValidationError as exc:
            raise DrafterError(
                f"LLM 起草 vars 未通过校验 ({exc}); 起草原文: {raw_vars}"
            ) from exc
        logger.info("LLM 起草 %s/%s 成功, fields=%d", rule.rule_id, template.template_id, len(validated))
        return validated
