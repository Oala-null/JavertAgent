# -*- coding: utf-8 -*-
"""ToolExecutor patient_context 注入 + 并发 cache 安全 单测."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from javert.tools.tool_executor import ToolExecutor


# ============================================================
# patient_context 注入 (fix-tool-patient-id-default)
# ============================================================


def _make_search_fees_stub():
    """模拟 search_fees 函数: 返回 args 用于断言注入."""
    calls: list[dict] = []

    def stub(**kwargs) -> str:
        calls.append(kwargs)
        return f"patient_id={kwargs.get('patient_id')}, category={kwargs.get('category')}"

    return stub, calls


def _make_drug_stub():
    """模拟 drug_indication: 不要求 patient_id."""
    calls: list[dict] = []

    def stub(**kwargs) -> str:
        calls.append(kwargs)
        return f"drug={kwargs.get('drug_name')}"

    return stub, calls


def test_register_records_requires_patient_id():
    """register 时记下 requires_patient_id 标记."""
    exec_ = ToolExecutor()
    stub, _ = _make_search_fees_stub()
    exec_.register("search_fees", stub, "desc", requires_patient_id=True)
    assert exec_._requires_patient_id["search_fees"] is True

    drug, _ = _make_drug_stub()
    exec_.register("drug_indication", drug, "desc", requires_patient_id=False)
    assert exec_._requires_patient_id["drug_indication"] is False


def test_execute_injects_patient_id_when_missing():
    """set_patient_context + execute(无 patient_id) → 工具收到 patient_id."""
    exec_ = ToolExecutor()
    stub, calls = _make_search_fees_stub()
    exec_.register("search_fees", stub, "desc", requires_patient_id=True)

    exec_.set_patient_context("J66252")
    result, cached = exec_.execute({"name": "search_fees", "arguments": {"category": "手术类"}})
    assert cached is False
    assert "patient_id=J66252" in result
    assert calls[0] == {"patient_id": "J66252", "category": "手术类"}


def test_execute_does_not_override_explicit_patient_id():
    """LLM 显式传 patient_id 时, 不被 context 覆盖."""
    exec_ = ToolExecutor()
    stub, calls = _make_search_fees_stub()
    exec_.register("search_fees", stub, "desc", requires_patient_id=True)

    exec_.set_patient_context("J66252")
    exec_.execute(
        {"name": "search_fees", "arguments": {"patient_id": "OTHER", "category": "X"}}
    )
    assert calls[0]["patient_id"] == "OTHER"


def test_execute_no_inject_for_tools_not_requiring_patient_id():
    """drug_indication requires=False, 不注入."""
    exec_ = ToolExecutor()
    drug, calls = _make_drug_stub()
    exec_.register("drug_indication", drug, "desc", requires_patient_id=False)

    exec_.set_patient_context("J66252")
    exec_.execute({"name": "drug_indication", "arguments": {"drug_name": "顺铂"}})
    assert "patient_id" not in calls[0]
    assert calls[0] == {"drug_name": "顺铂"}


def test_cache_key_unified_across_injection_modes():
    """显式传 patient_id 与 default 注入的 cache key 一致 — 第二次命中."""
    exec_ = ToolExecutor()
    stub, calls = _make_search_fees_stub()
    exec_.register("search_fees", stub, "desc", requires_patient_id=True)
    exec_.set_patient_context("J66252")

    # 第 1 次: 注入路径
    r1, c1 = exec_.execute({"name": "search_fees", "arguments": {"category": "X"}})
    assert c1 is False
    # 第 2 次: 显式传, 与注入后 args 相同
    r2, c2 = exec_.execute(
        {"name": "search_fees", "arguments": {"patient_id": "J66252", "category": "X"}}
    )
    assert c2 is True
    assert r1 == r2
    assert len(calls) == 1  # 工具只被实际调用 1 次


def test_clear_patient_context_disables_injection():
    """clear 之后 patient_id 缺失会让工具收不到该参数."""
    exec_ = ToolExecutor()
    stub, calls = _make_search_fees_stub()
    exec_.register("search_fees", stub, "desc", requires_patient_id=True)

    exec_.set_patient_context("J66252")
    exec_.clear_patient_context()
    exec_.execute({"name": "search_fees", "arguments": {"category": "X"}})
    assert "patient_id" not in calls[0]


def test_set_patient_context_idempotent():
    """重复 set 同值幂等, 不抛错."""
    exec_ = ToolExecutor()
    exec_.set_patient_context("J66252")
    exec_.set_patient_context("J66252")
    assert exec_._patient_context == "J66252"


# ============================================================
# 并发 cache 安全 (add-parallel-audit)
# ============================================================


def test_concurrent_cache_lookup_exactly_once_execution():
    """50 线程同 args 并发, 工具只被实际执行 1 次, 49+ 次 cached=True."""
    exec_ = ToolExecutor()
    counter_lock = threading.Lock()
    counter = {"n": 0}

    def slow_stub(**kwargs) -> str:
        # 模拟工具有一些工作量
        with counter_lock:
            counter["n"] += 1
        return f"called {counter['n']} times"

    exec_.register("slow_tool", slow_stub, "desc", requires_patient_id=False)

    def worker():
        return exec_.execute({"name": "slow_tool", "arguments": {"x": 1}})

    cached_count = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(worker) for _ in range(50)]
        for f in futures:
            _, cached = f.result()
            if cached:
                cached_count += 1

    assert counter["n"] == 1, f"工具被执行了 {counter['n']} 次, 期望 1 次"
    assert cached_count == 49, f"cached 数 {cached_count}, 期望 49"


def test_concurrent_distinct_args_each_executed_once():
    """每个不同 args 应执行一次, 不会因并发漏 / 重复."""
    exec_ = ToolExecutor()
    call_lock = threading.Lock()
    calls: list[dict] = []

    def stub(**kwargs) -> str:
        with call_lock:
            calls.append(kwargs)
        return f"x={kwargs.get('x')}"

    exec_.register("tool", stub, "desc", requires_patient_id=False)

    def worker(i):
        return exec_.execute({"name": "tool", "arguments": {"x": i}})

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(worker, range(20)))

    seen = sorted([c["x"] for c in calls])
    assert seen == list(range(20))


def test_reset_cache_thread_safe():
    """reset_cache 与 execute 并发不崩."""
    exec_ = ToolExecutor()
    stub, _ = _make_drug_stub()
    exec_.register("drug_indication", stub, "desc", requires_patient_id=False)

    def execute_worker(i):
        return exec_.execute({"name": "drug_indication", "arguments": {"drug_name": f"d{i}"}})

    def reset_worker():
        exec_.reset_cache()

    with ThreadPoolExecutor(max_workers=10) as pool:
        for i in range(50):
            pool.submit(execute_worker, i)
            if i % 10 == 0:
                pool.submit(reset_worker)
    # 不崩即通过
