# -*- coding: utf-8 -*-
"""ToolExecutor — <tool_call> 文本标签解析 + 调度.

Source: zadig_agent/src/tools/tool_executor.py (snapshot @ 2026-05-08)
改动:
  - 命名空间从 zadig_agent.tool_executor 改为 javert.tools.tool_executor
  - 接口保持: register / parse_tool_calls / execute / execute_all
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Callable

logger = logging.getLogger("javert.tools.tool_executor")

TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)
TOOL_CALL_ALT_PATTERN = re.compile(
    r"```tool_call\s*\n(\{.*?\})\s*\n```",
    re.DOTALL,
)


class ToolExecutor:
    """工具注册器 + 文本协议解析器."""

    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._tool_descriptions: dict[str, str] = {}
        self._requires_patient_id: dict[str, bool] = {}
        self._cache: dict[str, str] = {}
        self._patient_context: str | None = None
        # 并发场景: audit-patient --concurrency N 会共享 executor, _cache 读写需互斥
        self._cache_lock = threading.Lock()

    def register(
        self,
        name: str,
        func: Callable,
        description: str = "",
        *,
        requires_patient_id: bool = False,
    ) -> None:
        self._tools[name] = func
        self._tool_descriptions[name] = description
        self._requires_patient_id[name] = requires_patient_id
        logger.info("工具已注册: %s (requires_patient_id=%s)", name, requires_patient_id)

    def set_patient_context(self, patient_id: str) -> None:
        """设置工具调用默认 patient_id (由 Runner.audit 边界管理)."""
        self._patient_context = patient_id

    def clear_patient_context(self) -> None:
        """清空 patient_context (audit 完成或异常路径)."""
        self._patient_context = None

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": d} for n, d in self._tool_descriptions.items()]

    def get_tools_prompt(self) -> str:
        lines = [
            "你可以使用以下工具进行调查. 用 <tool_call>{\"name\": \"工具名\", \"arguments\": {...}}</tool_call> 格式调用.",
            "",
        ]
        for name, desc in self._tool_descriptions.items():
            lines.append(f"- **{name}**: {desc}")
        return "\n".join(lines)

    def parse_tool_calls(self, text: str) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        matches = TOOL_CALL_PATTERN.findall(text)
        if not matches:
            matches = TOOL_CALL_ALT_PATTERN.findall(text)
        for match in matches:
            try:
                parsed = json.loads(match)
                name = parsed.get("name", "")
                arguments = parsed.get("arguments", {})
                if name:
                    calls.append({"name": name, "arguments": arguments})
            except json.JSONDecodeError as exc:
                logger.warning("tool_call JSON 解析失败: %s, 原文: %s", exc, match[:100])
        return calls

    def has_tool_calls(self, text: str) -> bool:
        return bool(TOOL_CALL_PATTERN.search(text) or TOOL_CALL_ALT_PATTERN.search(text))

    def execute(self, tool_call: dict[str, Any]) -> tuple[str, bool]:
        name = tool_call["name"]
        arguments = tool_call.get("arguments", {}) or {}
        if name not in self._tools:
            return f"错误: 未知工具 '{name}'. 可用工具: {list(self._tools.keys())}", False

        # patient_id 自动注入 (fix-tool-patient-id-default):
        # 若工具 requires_patient_id 且 LLM 没传 patient_id 且 Runner 已 set 上下文,
        # 用新 dict merge — 原 arguments 不动 (cache key 用 merged 算).
        if (
            self._requires_patient_id.get(name, False)
            and "patient_id" not in arguments
            and self._patient_context is not None
        ):
            arguments = {**arguments, "patient_id": self._patient_context}

        cache_key = f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
        # 整 execute 加锁保证 "同 key 至多执行一次" — 工具是 CPU/IO 短任务,
        # 锁开销 << LLM 调用耗时, 不会成瓶颈; 换来 "exactly-once" 语义
        with self._cache_lock:
            if cache_key in self._cache:
                logger.info("工具缓存命中: %s", name)
                return self._cache[cache_key], True

            try:
                result = self._tools[name](**arguments)
                if isinstance(result, str):
                    result_text = result
                elif isinstance(result, dict):
                    result_text = json.dumps(result, ensure_ascii=False, indent=2)
                else:
                    result_text = str(result)
                self._cache[cache_key] = result_text
                logger.info("工具执行完成: %s, 结果长度=%d", name, len(result_text))
                return result_text, False
            except Exception as exc:
                error_msg = f"工具 '{name}' 执行失败: {exc}"
                logger.error(error_msg)
                return error_msg, False

    def execute_all(self, text: str) -> list[dict[str, Any]]:
        calls = self.parse_tool_calls(text)
        results: list[dict[str, Any]] = []
        for call in calls:
            result_text, is_cached = self.execute(call)
            results.append({
                "name": call["name"],
                "arguments": call["arguments"],
                "result": result_text,
                "cached": is_cached,
            })
        return results

    def reset_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
