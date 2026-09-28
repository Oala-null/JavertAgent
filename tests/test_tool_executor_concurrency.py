# -*- coding: utf-8 -*-
"""boost-llm-efficiency: ToolExecutor per-key 锁并发语义单测.

- 不同 key 不互斥 (慢工具首建不再挡住其他工具)
- 同 key 双并发只算一次 (compute-once 保留)
- 顺序重复调用命中缓存 (--share-tool-cache 语义回归)
"""

from __future__ import annotations

import threading

from javert.tools.tool_executor import ToolExecutor


def test_different_keys_do_not_block_each_other():
    """慢工具 (模拟 LabLoader 首建) 执行中, 另一个工具应能立即完成."""
    executor = ToolExecutor()
    slow_started = threading.Event()
    release_slow = threading.Event()

    def slow_tool(**_):
        slow_started.set()
        assert release_slow.wait(timeout=5), "slow_tool 未被释放"
        return "slow-done"

    def fast_tool(**_):
        return "fast-done"

    executor.register("slow", slow_tool)
    executor.register("fast", fast_tool)

    slow_result: list = []
    t_slow = threading.Thread(
        target=lambda: slow_result.append(executor.execute({"name": "slow", "arguments": {}}))
    )
    t_slow.start()
    assert slow_started.wait(timeout=5)

    # slow 持锁执行中 — fast (不同 key) 必须能不等它直接跑完
    fast_done = threading.Event()
    result_fast: list = []

    def run_fast():
        result_fast.append(executor.execute({"name": "fast", "arguments": {}}))
        fast_done.set()

    t_fast = threading.Thread(target=run_fast)
    t_fast.start()
    assert fast_done.wait(timeout=5), "fast 被 slow 的锁挡住 (全局锁语义回潮)"
    assert result_fast[0] == ("fast-done", False)

    release_slow.set()
    t_slow.join(timeout=5)
    t_fast.join(timeout=5)
    assert slow_result[0] == ("slow-done", False)


def test_same_key_concurrent_computes_once():
    """两线程同时发完全相同的调用 → 工具恰好算一次, 两边拿同一结果."""
    executor = ToolExecutor()
    call_count = 0
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()

    def counting_tool(**_):
        nonlocal call_count
        with lock:
            call_count += 1
        entered.set()
        assert release.wait(timeout=5)
        return "computed"

    executor.register("tool", counting_tool)

    results: list = []

    def run():
        results.append(executor.execute({"name": "tool", "arguments": {"k": "v"}}))

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    assert entered.wait(timeout=5)
    t2.start()  # t2 同 key, 应阻塞在 per-key 锁上
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert call_count == 1, f"同 key 并发算了 {call_count} 次 (应恰好 1 次)"
    texts = sorted(r[0] for r in results)
    assert texts == ["computed", "computed"]
    # 一个是首算 (cached=False), 一个吃缓存 (cached=True)
    assert sorted(r[1] for r in results) == [False, True]


def test_sequential_repeat_hits_cache():
    """--share-tool-cache 回归: 相同调用第二次命中缓存不重算."""
    executor = ToolExecutor()
    call_count = 0

    def tool(**_):
        nonlocal call_count
        call_count += 1
        return "r"

    executor.register("tool", tool)
    r1 = executor.execute({"name": "tool", "arguments": {"a": 1}})
    r2 = executor.execute({"name": "tool", "arguments": {"a": 1}})
    assert r1 == ("r", False)
    assert r2 == ("r", True)
    assert call_count == 1

    executor.reset_cache()
    r3 = executor.execute({"name": "tool", "arguments": {"a": 1}})
    assert r3 == ("r", False)
    assert call_count == 2
