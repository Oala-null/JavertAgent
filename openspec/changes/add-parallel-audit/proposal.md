## Why

`add-patient-centric-audit` 组 A 跑 J66252 串行 30 P0 规则 **41.5 min**, m1-rollout 组 E (15 条 M1-set) **18.6 min**, avg 75s/rule. 整个时间几乎完全消耗在 LLM 等待 (sglang 单 GPU, Qwen3.5-35B), 而不是本地 CPU/IO. **同一时刻只占用 sglang 一个请求 slot**, GPU 闲置.

sglang 服务支持多 in-flight 请求 (内部 batch scheduling), 并发 N 路实际上是 "把 N 个 prompt 一起塞进 GPU 同一 batch". 经验估算: 3-5 并发对 Qwen3.5-35B-A3B-GPTQ-Int4 单 GPU 应能提速 2-4×, 不会线性扩展但比串行省一大半. 目标: **单病人 15 条 M1-set 跑到 4-6 min**.

## What Changes

- `audit-patient` CLI 加 `--concurrency N` flag (默认 1, 与现行串行行为完全等价), 上限 10
- `commands/audit_patient.py` 重写跑批逻辑: `--concurrency > 1` 时用 `concurrent.futures.ThreadPoolExecutor` + `submit` + `as_completed` 模型
- `SqliteStore` 增 `_write_lock: threading.Lock`, 把 `write` / `mark_synced` / `mark_sync_failed` 全部加锁, 保护并发写
- `ToolExecutor` 增 `_cache_lock: threading.Lock`, 把 `_cache` 读写都加锁 (D2 决定 share executor + 单 patient 上下文, set_patient_context 是幂等的, 不用锁)
- 进度行 stderr 加 `[i/N] Rxxx → V conf 75.3s tc=4 (cached 1)` 现行格式不变; **完成顺序输出**, 不保证规则 ID 顺序; 加 `threading.Lock` 保护 stderr 行写, 避免交错
- summary block 维持 stdout 输出, 按 rule_id 排序汇总 (内部累积, 与完成顺序解耦)
- `Runner` 不强制并发安全 — Runner 内部状态是 stateless, executor 与 store 加锁后就够

不在本期范围:
- ❌ asyncio / async httpx 改造 (LLM provider 同步 httpx 已经稳, 不动)
- ❌ 多进程 / multiprocessing (sglang 本就 HTTP, 多进程优势=0)
- ❌ 跨 audit_patient 调用的并发 (例如同时审 N 个 patient) — 那是后续 batch-audit 的事
- ❌ 改 audit_runs schema (并发写靠 SqliteStore lock, schema 不变)
- ❌ 路由优化 (`add-rule-routing`) — 留给后续

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `cli`: `audit-patient` 命令加 `--concurrency` 参数 (默认 1; 1=串行, ≥2=ThreadPoolExecutor 并发)
- `audit-engine`: `SqliteStore` 写入加锁; `ToolExecutor._cache` 加锁; 整体并发安全 invariant 维护在编排层 (commands/audit_patient.py) 与持久化层 (SqliteStore)

## Impact

- **代码改动 (~120 行 + ~40 行测试)**:
  - `src/javert/commands/audit_patient.py`: 主重写, 串行路径保留 (concurrency=1 走旧分支), 并发路径走 ThreadPoolExecutor
  - `src/javert/tools/tool_executor.py`: `_cache_lock`, `execute()` 内 `with self._cache_lock:` 包裹 cache 读写
  - `src/javert/store/audit_store.py`: `_write_lock`, `write/mark_synced/mark_sync_failed` 加锁
  - `src/javert/cli.py`: audit-patient 加 `--concurrency` 选项
- **测试**:
  - 新 `tests/test_audit_patient_concurrency.py`:
    - 并发 3, 5 条 mocked rules, 验全部产 result; 顺序保留
    - 与串行结果集合相等 (mock provider 给固定输入 → 输出确定)
    - share-tool-cache + concurrency=3 cache 命中率 > 0
    - LlmUnavailableError 中断某条但其他条不受影响, summary 报 failed
  - 扩 `tests/test_audit_store.py`: 多线程并发 write 50 条, 验全部入库
- **CLI 文档**: `--help` 输出更新, CLAUDE.md 与 README 路线图更新阶段
- **回滚**: 单文件 git revert; 并发路径在 `concurrency=1` 时不进入, 默认行为不变
- **下游解锁**: `add-parallel-audit` 完成 + `fix-tool-patient-id-default` 完成后, 单病人 audit 可控在 4-6 min, 为后续 batch-N-patients / web UI 实时审计提供性能基础
