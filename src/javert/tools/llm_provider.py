# -*- coding: utf-8 -*-
"""Qwen3.5 sglang LLM Provider.

Source: zadig_agent/src/llm_provider.py (snapshot @ 2026-05-08)
改动:
  - 移除 zadig_agent 自身 config import, 改用 javert.config
  - 接口保持 chat / verify_model / wait_for_sglang
  - 新增 LlmUnavailableError 异常类
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from javert.config import JavertConfig, get_config

logger = logging.getLogger("javert.tools.llm_provider")


def _native_tool_calls(message: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """OpenAI message.tool_calls → Runner 既有 {name, arguments} 契约。"""
    calls: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(message.get("tool_calls") or []):
        function = item.get("function") or {}
        name = str(function.get("name") or "").strip()
        raw_arguments = function.get("arguments", {})
        try:
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str) and raw_arguments.strip()
                else (raw_arguments or {})
            )
        except json.JSONDecodeError as exc:
            errors.append(f"{name or 'unknown'} arguments JSON 非法: {exc}")
            continue
        if not name or not isinstance(arguments, dict):
            errors.append(f"{name or 'unknown'} 缺少函数名或 arguments 不是对象")
            continue
        calls.append({
            "id": str(item.get("id") or f"call_{index}"),
            "name": name,
            "arguments": arguments,
        })
    return calls, errors


class LlmUnavailableError(RuntimeError):
    """LLM 端点不可达, 重试预算耗尽."""


class LlmClientError(RuntimeError):
    """HTTP 4xx (非 429): 请求本身有问题 (如 400 上下文超长), 重试无意义 — 立即失败.

    刻意不继承 LlmUnavailableError: chat_with_retry 不重试它, audit-patient
    串行/并发都按普通异常标 failed 继续, 不中断整患者.
    """


class Qwen35Provider:
    """Qwen3.5-35B-A3B sglang Provider (httpx 直连)."""

    def __init__(self, config: JavertConfig | None = None):
        self._config = config or get_config()
        self._http_client = None

    @property
    def model_name(self) -> str:
        return self._config.llm_model

    @property
    def base_url(self) -> str:
        return self._config.llm_endpoint

    @property
    def max_tokens(self) -> int:
        return self._config.llm_max_tokens

    def _get_http_client(self):
        if self._http_client is None:
            import httpx
            transport = httpx.HTTPTransport(local_address="0.0.0.0")
            self._http_client = httpx.Client(
                transport=transport,
                timeout=self._config.llm_timeout,
            )
        return self._http_client

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        """同步 chat completion，返回正文、usage、finish_reason 和原始响应。"""
        client = self._get_http_client()
        url = f"{self.base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature if temperature is not None else self._config.llm_temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if not self._config.llm_enable_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            body["tools"] = tools

        try:
            resp = client.post(url, json=body)
        except Exception as exc:
            raise LlmUnavailableError(f"sglang 请求失败: {exc}") from exc
        if 400 <= resp.status_code < 500 and resp.status_code != 429:
            # 4xx (非 429) 是请求自身的问题 (R103 实证: 400 上下文超长被当 503 重试 3 次)
            # → 快速失败, 错误带 status + body 摘要可直接诊断
            raise LlmClientError(
                f"sglang HTTP {resp.status_code} (不重试): {resp.text[:300]}"
            )
        try:
            resp.raise_for_status()
        except Exception as exc:
            raise LlmUnavailableError(f"sglang 请求失败: {exc}") from exc

        data = resp.json()
        choice = data["choices"][0]
        msg = choice.get("message", {})
        tool_calls, tool_call_errors = _native_tool_calls(msg)
        content = msg.get("content") or ""
        reasoning_content = msg.get("reasoning_content") or ""
        if not content and reasoning_content and not tool_calls:
            logger.info("[Qwen35Provider] content 为空, fallback 到 reasoning_content")
            content = reasoning_content

        usage = None
        if data.get("usage"):
            u = data["usage"]
            usage = {
                "prompt_tokens": u.get("prompt_tokens", 0) or 0,
                "completion_tokens": u.get("completion_tokens", 0) or 0,
                "total_tokens": u.get("total_tokens", 0) or 0,
            }

        return {
            "content": content,
            "reasoning_content": reasoning_content,
            "tool_calls": tool_calls,
            "tool_call_errors": tool_call_errors,
            "usage": usage,
            "finish_reason": choice.get("finish_reason"),
            "raw_response": data,
        }

    def chat_with_retry(
        self,
        messages: list[dict[str, str]],
        retries: int | None = None,
        backoff: float = 2.0,
        **kwargs,
    ) -> dict[str, Any]:
        """带重试的 chat. 重试次数耗尽抛 LlmUnavailableError."""
        attempts = retries if retries is not None else self._config.retry_budget
        last_exc: Exception | None = None
        for i in range(max(attempts, 1)):
            try:
                return self.chat(messages, **kwargs)
            except LlmUnavailableError as exc:
                last_exc = exc
                wait = backoff * (i + 1)
                logger.warning("LLM 调用失败 (尝试 %d/%d), %ds 后重试: %s", i + 1, attempts, wait, exc)
                time.sleep(wait)
        raise LlmUnavailableError(
            f"LLM 在 {attempts} 次重试后仍不可用: {last_exc}"
        )

    def verify_model(self) -> bool:
        """探活 sglang. 至少有一个模型 id 含 'qwen' 视为通过."""
        import requests
        try:
            url = f"{self.base_url}/models"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            for model in data.get("data", []):
                if "qwen" in model.get("id", "").lower():
                    logger.info("模型验证通过: %s", model.get("id"))
                    return True
            ids = [m.get("id") for m in data.get("data", [])]
            logger.error("未找到 Qwen 模型, 当前 sglang 加载: %s", ids)
            return False
        except Exception as exc:
            logger.error("无法连接 sglang: %s", exc)
            return False
