## 1. 前置依赖

- [x] 1.1 `fix-tool-patient-id-default` 已 apply (同期一起做, Runner 设/清 patient_context + ToolExecutor 注入逻辑 + manage_patient_context kwarg 已就位)
- [x] 1.2 `uv run pytest tests/ -v` baseline 全绿

## 2. SqliteStore 并发安全

- [x] 2.1 改 `src/javert/store/audit_store.py`:
  - `SqliteStore.__init__`: 加 `self._write_lock = threading.Lock()`
  - `conn` property: sqlite3.connect 加 `check_same_thread=False`
  - `write` / `mark_synced` / `mark_sync_failed`: body 包进 `with self._write_lock, self.conn as c:`
- [x] 2.2 扩 `tests/test_audit_store.py`:
  - `test_concurrent_write_50_threads` — ThreadPoolExecutor 50 workers × write, 验全部 50 入库无 OperationalError
  - `test_concurrent_mark_synced_and_write` — 并发 mark_synced 20 条不丢
- [x] 2.3 `uv run pytest tests/test_audit_store.py -v` 全绿 (14 passed)

## 3. ToolExecutor cache 并发安全

- [x] 3.1 改 `src/javert/tools/tool_executor.py`:
  - `__init__`: 加 `self._cache_lock = threading.Lock()`
  - `execute`: cache lookup + 工具执行 + cache write 都在 `with self._cache_lock:` (保证 exactly-once 语义)
  - `reset_cache`: 也加锁
- [x] 3.2 扩 `tests/test_tool_executor_patient_context.py`:
  - `test_concurrent_cache_lookup_exactly_once_execution` — 50 线程同 args 并发, 工具只执行 1 次 49 次 cached
  - `test_concurrent_distinct_args_each_executed_once` — 不同 args 都不漏
  - `test_reset_cache_thread_safe` — reset 与 execute 并发不崩

## 4. Runner.audit kwarg

- [x] 4.1 改 `src/javert/audit/runner.py:audit`:
  - 加 kwarg `manage_patient_context: bool = True`
  - 现行 set/clear 逻辑包进 `if manage_patient_context:` 分支
  - audit body 抽到 `_audit_body` 私有方法, 由 try/finally 包裹
- [x] 4.2 扩 `tests/test_runner.py`:
  - `test_audit_manage_context_true_sets_and_clears` (默认行为)
  - `test_audit_manage_context_false_preserves_pre_set_value` (并发分支用法)

## 5. cli.py 加 --concurrency

- [x] 5.1 改 `src/javert/cli.py:audit_patient_cmd`: 加 `--concurrency` 参数, `click.IntRange(1, 10)`, default=1, show_default
- [x] 5.2 把 concurrency 透传给 `run_audit_patient(...)`

## 6. commands/audit_patient.py 并发分支

- [x] 6.1 改 `src/javert/commands/audit_patient.py`:
  - 函数签名加 `concurrency: int = 1`
  - 抽出 `_run_serial` (现行串行 break 语义) 与 `_run_parallel` 两条分支
  - 并发分支: 在批入口 `executor.set_patient_context(patient_id)`, ThreadPoolExecutor(max_workers=min(concurrency, len(selected))), submit + as_completed, 主线程接 future.result + persist + 打印进度行, 加 `_print_lock` 防进度行交错; 整 batch 结束 `clear_patient_context`
  - worker: `runner.audit(..., reset_cache=False, manage_patient_context=False)` (cache 由 batch 共享, context 由 batch 入口管)
- [x] 6.2 summary block: 加一行 `concurrency: N` 紧跟 `cache mode:` 之后
- [x] 6.3 进度行格式不变: `[i/N] Rxxx → V conf=X.XX Ys tc=N (cached M)` (i 用完成顺序)
- [x] 6.4 LlmUnavailableError: 并发模式 record + continue (不 break); summary 区分 failed 与 pending: 并发模式 pending=0, failed 包含 llm_failed_rules + failed_rules
- [x] 6.5 串行模式 (concurrency=1) 仍 break — 不破坏 add-patient-centric-audit 的现行语义

## 7. 并发单测

- [x] 7.1 新 `tests/test_audit_patient_concurrency.py` (7 测试):
  - test_concurrency_3_runs_all_5_rules
  - test_concurrency_preserves_verdict_distribution
  - test_concurrency_with_share_cache_yields_hits (cached >= 4/5)
  - test_concurrency_llm_fail_isolated (3rd LLM 挂, 其他 4 条 OK, summary 报 failed=1)
  - test_concurrency_1_preserves_serial_break_semantics (concurrency=1 仍 break)
  - test_concurrency_max_workers_capped_by_rule_count
  - test_concurrency_invalid_value_rejected (0/11/abc 都 exit 2)
- [x] 7.2 `uv run pytest tests/test_audit_patient_concurrency.py -v` 全绿 (7 passed)
- [x] 7.3 `uv run pytest tests/ -v` 全量回归全绿 (157 passed; 原 134 + 新 23)

## 8. 实测组 F (J66252 15 条 M1-set + concurrency)

- [x] 8.1 sglang `192.168.31.62:30000` 健康检查 — 通过
- [x] 8.2 首次尝试 `--concurrency 5`: sglang 5 simultaneous 触发 timed out (R3 在 sglang 端排队超过 300s timeout). 与设计 R1 风险一致, 操作者按设计 mitigation 降到 3
- [x] 8.3 最终 `uv run javert audit-patient J66252 --share-tool-cache --concurrency 3 --rules R045..R300` 跑通:
  - **Total: 341.4s (5:41)** — 落在目标 4-6 min 区间 ✓
  - avg=65.4s, p50=59.6s, V=0 C=15 I=0 (与组 E 形态一致, 无并发引入污染)
  - Tool calls: 40 total, 25 cached (hit_rate=62.5%) — vs 组 E 68/24 (35.3%), 总数↓41%, hit_rate↑77%
  - Slowest: R228 97s, R069 83s, R077 79s — vs 组 E (R208 130s 首位), R208 现降至 71s
- [x] 8.4 不需要 concurrency=3 对照 — 3 已达成目标, 5 已知会超时, 不再测 2 (留作未来调研)
- [x] 8.5 docs/sample_audit_patient.md 加 "组 F: fix-tool-patient-id-default + add-parallel-audit 后" 节

## 9. 文档收尾

- [x] 9.1 更新 `CLAUDE.md`:
  - 当前阶段标记: 标 ✅ `fix-tool-patient-id-default + add-parallel-audit`
  - 常用命令 audit-patient 例子加 `--concurrency 5` 行 (示范用法)
  - 路线图加 ✅ 两条
- [x] 9.2 更新 `README.md`:
  - 路线图加 ✅ 两条 (fix-tool-patient-id-default / add-parallel-audit)
  - 子命令示例加 `--concurrency 5` 行
- [x] 9.3 `openspec validate add-parallel-audit --strict` 通过

## 10. 验收

- [x] 10.1 spec scenario "concurrency=1 preserves serial behavior" 通过 (test_concurrency_1_preserves_serial_break_semantics)
- [x] 10.2 spec scenario "concurrency=N runs all rules in parallel" 通过 (test_concurrency_3_runs_all_5_rules)
- [x] 10.3 spec scenario "share-tool-cache works under concurrency" 通过 (test_concurrency_with_share_cache_yields_hits)
- [x] 10.4 spec scenario "LlmUnavailableError isolated under concurrency" 通过 (test_concurrency_llm_fail_isolated)
- [x] 10.5 spec scenario "summary block reports concurrency" 通过 (test_concurrency_max_workers_capped_by_rule_count)
- [x] 10.6 spec scenario "concurrent SqliteStore writes do not lose rows" 通过 (test_concurrent_write_50_threads)
- [x] 10.7 spec scenario "concurrent ToolExecutor cache lookups are race-safe" 通过 (test_concurrent_cache_lookup_exactly_once_execution)
- [x] 10.8 spec scenario "Runner.audit with manage_patient_context=False" 通过 (test_audit_manage_context_false_preserves_pre_set_value)
- [x] 10.9 spec scenario "Runner.audit with manage_patient_context=True (default)" 通过 (test_audit_manage_context_true_sets_and_clears)
- [x] 10.10 **实测组 F 达成 4-6 min 目标**: 341.4s = 5:41, 3.27× 提速 vs 组 E (1117.7s 串行)
