## Context

`add-patient-centric-audit` 组 A 41.5 min, `m1-rollout` 组 E 18.6 min — 串行单进程 audit-patient 在 sglang 单 GPU 上的实测耗时. CPU/IO 几乎全空闲, 时间几乎 100% 花在等待 LLM 返回. sglang 自身有 batch scheduling, 多请求会自动合并进 GPU 同一 batch, 所以客户端并发是直接提速手段.

操作者诉求 (docs/sample_audit_patient.md 组 E §结论): "15 条 M1 串行 18.6 min, sglang 单 GPU 应能 3-5 并发, 跑到 4-6 min".

约束:
- LLM provider 是 `httpx.Client` 同步 — 不要重写成 async (改动面大, 当前 httpx 已稳)
- ToolExecutor / SqliteStore / SqlServerStore 都不是天生线程安全 — 加锁解决
- sqlite3.Connection 默认 `check_same_thread=True`, 跨线程访问会 raise — 必须 `check_same_thread=False` + 自己加锁
- audit_patient 整 batch 都是同一 patient_id, set_patient_context 是 idempotent — 不引入跨 patient race
- 失败隔离: 一条 LLM 失败不能把其他线程的 audit 也取消; 要么全跑完, 要么记一条 failed 但 batch 继续

## Goals / Non-Goals

**Goals:**
- `javert audit-patient J66252 --share-tool-cache --concurrency 5 --rules ...` 跑 15 条 M1-set 在 4-6 min 内完成 (vs 串行 18.6 min, 提速 3-4×)
- `concurrency=1` 行为与现行串行完全一致 (回归保证)
- 失败隔离: 单条 LlmUnavailableError 不取消整 batch; 当前串行实现是 break, 并发实现是 record + continue
- 并发写 SqliteStore 不出 "database is locked" 或 INSERT 丢失
- ToolExecutor cache 在并发 read/write 下不出 KeyError / race condition
- 进度行 stderr 不交错 (不出现 "[1/15] R045[2/15] R047" 这种)

**Non-Goals:**
- ❌ async httpx / async sqlite — 太重, 不需要
- ❌ 多进程 (multiprocessing.Pool) — sglang 是 HTTP, 多进程毫无优势
- ❌ 跨 patient 并发 (Web UI / batch-audit 入口) — 后续 change
- ❌ 自动决定 concurrency (基于 GPU 负载) — 操作者显式传
- ❌ ToolExecutor 并发跨 patient_id (本期 patient 单一, set_patient_context 幂等)
- ❌ 把 Runner.audit 重写成 async — Runner 仍是同步 callable, 由 ThreadPoolExecutor.submit 包

## Decisions

### D1. 并发模型: ThreadPoolExecutor

**选项**:
- (A) `concurrent.futures.ThreadPoolExecutor` + `submit` + `as_completed`
- (B) `asyncio` + `httpx.AsyncClient` (重写 LLM provider)
- (C) `multiprocessing.Pool`
- (D) 协程 + `httpx.AsyncClient` + 同步 sqlite (混合)

**选 A**, 因为:
- 当前 LLM provider / Runner / ToolExecutor 都是同步代码; ThreadPoolExecutor 是 "把同步阻塞调用甩进线程池" 最直接的方式
- LLM 调用是 IO bound (HTTP 等), 不受 GIL 限制
- B/D 需要重写 LLM provider, 引入 await 链, 改动面大且本期不必要 (httpx sync 已稳)
- C 多进程对 sglang 客户端毫无优势 (后端 HTTP 一样), 还要处理进程间状态共享, 完全反向

### D2. ToolExecutor 共享 vs per-thread

**选项**:
- (i) 整 batch 共享 1 个 ToolExecutor (cache 共享, 加锁)
- (ii) 每个并发线程独立 ToolExecutor (无 cache 共享, 无锁)
- (iii) 线程池 size N → N 个独立 executor, 跑完一条放回池

**选 i**, 因为:
- 组 E 实测 cache hit 35%, 是 share-tool-cache 关键收益 — ii 直接放弃这 35% 提速
- iii 与 i 在 cache 共享上等价 (只要 N 个 executor 都看同一 dict), 但 iii 需要重构 ToolExecutor 为多实例; 不必
- i 的锁开销小 (cache 是字典, lookup 是 ns 级), GIL 也只让一个线程进 lock; 实测 cache lookup << LLM 调用, 锁不会是瓶颈

### D3. SqliteStore 线程安全

**选项**:
- (a) 每线程独立 SqliteStore 实例 (各自 connection)
- (b) 共享 SqliteStore + `check_same_thread=False` + `threading.Lock` 保护 write
- (c) Producer/Consumer 模式 — 主线程消费 audit result, 单线程 write

**选 b**, 因为:
- a 每线程开 conn, 但 sqlite 文件层仍只能 1 writer (sqlite 是 single-writer); 没解决问题, 还多 5 个 conn
- c 加 Queue + 消费者线程, 复杂度上升, 收益对 ~15 条 write 不显著
- b 一把锁简单粗暴, 写完即放; sqlite write 是 ms 级, 锁竞争微不足道

### D4. 进度行 stderr 防交错

**选项**:
- (α) 全局 `threading.Lock`, 进度行整行 print 都包在锁里
- (β) 每条 audit 完成后 push 进 Queue, 主线程 dequeue 打印
- (γ) loguru / structlog 之类带线程安全的日志库

**选 α**, 因为:
- click.echo 内部是 print, print 在 CPython 里 atomic per call 但多行不 atomic — 显式锁保险
- β 引入 Queue + 主线程轮询, 解耦但增加复杂度
- γ 引入新依赖

### D5. submit / as_completed 流程

```python
with ThreadPoolExecutor(max_workers=concurrency) as pool:
    future_to_rule = {pool.submit(_run_one, rule, pid, ...): rule for rule in selected}
    for future in as_completed(future_to_rule):
        rule = future_to_rule[future]
        try:
            result = future.result()
            results.append(result)
            _persist_and_print(result, ...)
        except LlmUnavailableError:
            llm_failed.append(rule.rule_id)
        except Exception as exc:
            failed_rules.append(rule.rule_id)
```

注意:
- `_run_one` 内部已是 `runner.audit(...)`, 自带 try/except + result return
- 主线程负责 persist 与打印 — 不让 worker 线程接触 SqliteStore (虽然加锁也可, 但单点写更可控)
- 实际上 persist 也可以下放给 worker (反正 SqliteStore 已加锁), 但**让主线程做能保证进度行的 [i/N] 计数稳定**

### D6. failure 处理: 并发不 break, 整 batch 完成

**串行行为** (当前):
```python
try: result = runner.audit(...)
except LlmUnavailableError: break  # 整 batch 终止
```

**并发行为** (改动):
```python
# worker 内: 不捕获 LlmUnavailableError, 让 future 携带
# 主线程 as_completed: future.result() 触发 raise → record llm_failed, 继续下一 future
```

这样 sglang 偶发抖动 (一次失败) 不再取消整 batch. 但若 sglang 完全挂 (5 retry 全失败), 后续提交的 future 会一个个 raise — 总会跑完 (不阻塞).

边界情况: 若 sglang 挂在 batch 中间, M 条已成功 N-M 条全 LlmUnavailableError. summary 应该清晰报 "failed: M 条". 这与现行串行的"中断, 剩余 pending"语义不同 — 但**并发场景下"中断"概念不成立**(已 submit 的 future 不能取消, 除非 cancel 但 LLM 调用不响应 cancel signal). 妥协: 并发时所有 future 全部跑完, 失败的标 failed 不标 pending. summary 中 `pending=0, failed=N`. summary 同时计算 LLM unavailable 次数, 若 >50% → 提示用户考虑 sglang 健康.

### D7. concurrency 上限 10

ThreadPoolExecutor max_workers 设上限 10, 防止操作者输 100 导致 sglang 排队抖动 / OOM. click 用 `IntRange(1, 10)` 校验, 超过 click 自己 reject + exit 2.

最佳实测预计区间: 3-5. 留余量到 10 给后续 LLM 升级 (例如 Qwen-72B / sglang 多机).

### D8. 与 `fix-tool-patient-id-default` 的关系

`fix-tool-patient-id-default` 在 ToolExecutor 加 patient_context 状态. 并发场景下, audit_patient 整 batch 同 patient_id, set_patient_context 在主线程进入 batch 时调一次, batch 结束 clear; 中间所有线程的 audit 共享同一 patient_context. Runner.audit 内部的 set/clear 在并发场景是 idempotent (反复 set 同一值), clear 之后 batch 内其他线程读到 None 会出问题.

**修补**: 并发模式 (concurrency > 1) 下, Runner.audit 不要 set/clear — 由 audit_patient 命令层在 batch 边界做. Runner.audit 加一个 kwarg `manage_patient_context: bool = True`, 串行场景默认 True (兼容 dry-run / run --pilot), 并发场景由 commands/audit_patient.py 传 False.

或者更干净: Runner.audit 的 set/clear 用 `_set_if_not_set + _clear_if_owner` 模式 — 但太花哨, 不如显式 kwarg.

**简化决策**: D8 用 kwarg `manage_patient_context: bool = True`. 并发分支调 `set_patient_context(patient_id)` 一次, 全 batch 共用, 结束 clear; Runner.audit(manage_patient_context=False) 不动 context.

### D9. SqlServerStore (142 双写) 并发

`persist_one` 内部调 `get_sqlserver_store().write_audit(...)`. SQLAlchemy engine pool 自带线程安全 (sql_pool_size=5, sql_max_overflow=5). 142 双写不需要额外加锁. 若 142 不可达, mark_sync_failed 走 SqliteStore 已锁的路径.

唯一隐患: SqliteStore.write 与 mark_synced 是 2 步; 并发时若 thread A write 完 thread B 来 mark_synced 同一 run_id, 因为 run_id 是 audit_run 级唯一 (UUID), 实际不会冲突. 但保险起见, mark_synced/mark_sync_failed 都加同一把 _write_lock.

## Risks / Trade-offs

**[R1] sglang 并发=5 实际抖动 / 排队** → Qwen3.5-35B-A3B-GPTQ-Int4 GPTQ-Int4 量化模型在 5880 Ada 48GB 上, batch=5 经验 OK; 但实际 batch_size 由 sglang 调度决定. Mitigation: 实测组 F 跑 concurrency=3 / 5, 看耗时是否真 3-4× 加速; 若 5 反不如 3, 在 docs 记下并把默认值或推荐值调到 3

**[R2] ToolExecutor cache lock 成 hotspot** → ~15 条 audit × 4-6 tool calls = ~80 次 cache lookup, 锁 ns 级 << LLM 秒级. 不会是瓶颈

**[R3] SqliteStore single-writer 排队** → 15 次 write, 每次 ms 级, 总开销 < 50ms, 完全可接受

**[R4] 进度行 [i/N] 计数与 rule_id 顺序解耦** → 并发完成顺序与提交顺序不一致, summary 仍按 rule_id 排序; 进度行用提交顺序的 i? 还是完成顺序的 i? 选**完成顺序** — 每条出 verdict 时打印 `[完成数/总数] rule_id`, 操作者能感受进度. 但 rule_id 顺序在 summary 里恢复

**[R5] LlmUnavailableError 主线程 record vs 立即停** → 并发不停 = 浪费配额? 是; 但停了的副作用 (要 cancel future) 更脏. 折衷: 跑完, summary 报 failed; 若 sglang 挂大半 batch, 操作者一目了然

**[R6] manage_patient_context=False 路径漏 set** → 并发分支必须在 ThreadPoolExecutor 启动前显式 `executor.set_patient_context(pid)`; 否则线程内 LLM 漏 patient_id 又会触发 fix-tool-patient-id-default 的注入 fallback. Mitigation: commands/audit_patient.py 内并发分支显式 set, 测试覆盖 "并发 + missing patient_id arg → 自动注入正确"

**[R7] CTRL-C 终止 ThreadPoolExecutor** → as_completed 循环在主线程, KeyboardInterrupt 会 raise; with 块退出会 cancel pending future (但 running future 跑完). 操作者按 ^C 一次会等 "in-flight" 的 N 条跑完才退. Mitigation: 不解决, docs 提示 "并发模式 ^C 不立即停, 等 in-flight 完成"

## Migration Plan

按 5 阶段:

1. **W1 (依赖准备)**: 装 `fix-tool-patient-id-default` (前置 change). 跑 `pytest tests/` 全绿
2. **W2 (lock 改造)**: SqliteStore 加 `_write_lock`, ToolExecutor 加 `_cache_lock`. 单测 `test_sqlite_concurrent_write`, `test_tool_executor_concurrent_cache`. `pytest tests/test_audit_store.py tests/test_tools.py` 全绿
3. **W3 (Runner kwarg)**: Runner.audit 加 `manage_patient_context: bool = True`, 默认行为不变; 加测试 "manage=False 路径 Runner 不动 context"
4. **W4 (audit-patient 重写)**: commands/audit_patient.py 拆出 `_run_one(rule, pid, ...)` worker, 加 ThreadPoolExecutor 分支 (concurrency=1 走旧串行路径); cli.py 加 `--concurrency` 选项. 新测 `tests/test_audit_patient_concurrency.py` 4 个场景
5. **W5 (实测组 F)**: 跑 `audit-patient J66252 --share-tool-cache --rules R045,...,R300 --concurrency 5`, 写组 F 到 docs/sample_audit_patient.md

回退: W4 单独 revert (audit-patient 重写); W2-W3 可一起或单独 revert.

## Open Questions

- **Q1**: 并发模式下 `--share-tool-cache` 是否应改为默认 True? 现行默认 False (干净 baseline); 并发模式下 cache miss 浪费明显. 倾向 **保留默认 False, 但 docs 推荐组 F 跑加 --share-tool-cache**
- **Q2**: 是否在 summary 中加 "concurrency=N" 字段? 是, 让 docs 与回放都能看到本次跑的并发配置. summary block 加一行 `concurrency: N`
- **Q3**: 若 concurrency=5 但 priority=P0 只有 3 条 ready, ThreadPoolExecutor max_workers 应该 min(N, len(selected)) 还是 N? **选 min(N, len(selected))**, 避免开多余线程
- **Q4**: `_run_one` 是否需要传 SqliteStore? 不传 — 由主线程在 as_completed 后 persist. 这降低 worker 线程的责任面积, 也避免 _run_one 内部异常时 store 状态不可控
