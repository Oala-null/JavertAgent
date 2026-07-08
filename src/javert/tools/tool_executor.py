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

# execute() 执行失败/未知工具时返回的错误串前缀. 生产 (execute) 与判定
# (is_error_result) 共用同一常量, 避免耦合漂移. 新增错误形态时在此同步.
# ponytail: 靠前缀区分「错误串 vs 真实工具输出」, 新错误形态需同步这里.
_ERR_UNKNOWN_PREFIX = "错误: 未知工具"
_ERR_EXEC_FAIL_PREFIX = "工具 '"
_ERR_EXEC_FAIL_MARK = "' 执行失败:"


class ToolExecutor:
    """工具注册器 + 文本协议解析器."""

    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._tool_descriptions: dict[str, str] = {}
        self._requires_patient_id: dict[str, bool] = {}
        self._cache: dict[str, str] = {}
        self._patient_context: str | None = None
        # 并发场景: audit-patient --concurrency N 会共享 executor, _cache 读写需互斥.
        # boost-llm-efficiency: 两层锁 — 外层短锁只保护 dict 操作, 内层 per-key 锁包工具执行,
        # 不同 key 完全并行 (LabLoader 首建不再挡住所有线程), 同 key 保持 compute-once.
        self._cache_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

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

    def parse_errors(self, text: str) -> list[str]:
        """收集 <tool_call> 标签内 JSON 解析失败的错误串 (针对性反馈用).

        parse_tool_calls 静默丢弃畸形 tool_call; 此方法专门把 json.loads 的
        错误信息暴露出来, 供 runner 回传给模型修正.
        """
        matches = TOOL_CALL_PATTERN.findall(text)
        if not matches:
            matches = TOOL_CALL_ALT_PATTERN.findall(text)
        errors: list[str] = []
        for match in matches:
            try:
                json.loads(match)
            except json.JSONDecodeError as exc:
                errors.append(str(exc))
        return errors

    @classmethod
    def is_error_result(cls, text: str) -> bool:
        """判断 execute() 返回的文本是否为错误串 (未知工具 / 执行失败) 而非真实工具输出.

        「至少 1 次成功 tool_call 才解锁裁决」的判定依据. 与 execute 生成错误串
        共用前缀常量.
        """
        return text.startswith(_ERR_UNKNOWN_PREFIX) or (
            text.startswith(_ERR_EXEC_FAIL_PREFIX) and _ERR_EXEC_FAIL_MARK in text
        )

    def execute(self, tool_call: dict[str, Any]) -> tuple[str, bool]:
        name = tool_call["name"]
        arguments = tool_call.get("arguments", {}) or {}
        if name not in self._tools:
            return f"{_ERR_UNKNOWN_PREFIX} '{name}'. 可用工具: {list(self._tools.keys())}", False

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
        # 外层短锁: 查缓存 + 取/建 per-key 锁 (纯 dict 操作, 微秒级)
        with self._cache_lock:
            if cache_key in self._cache:
                logger.info("工具缓存命中: %s", name)
                return self._cache[cache_key], True
            key_lock = self._key_locks.setdefault(cache_key, threading.Lock())

        # 内层 per-key 锁: 同 key 并发只算一次 (后到者等首个算完直接吃缓存),
        # 不同 key 互不阻塞
        with key_lock:
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
                with self._cache_lock:
                    self._cache[cache_key] = result_text
                logger.info("工具执行完成: %s, 结果长度=%d", name, len(result_text))
                return result_text, False
            except Exception as exc:
                error_msg = f"{_ERR_EXEC_FAIL_PREFIX}{name}{_ERR_EXEC_FAIL_MARK} {exc}"
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
            self._key_locks.clear()
