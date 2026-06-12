## Why

2c 平台(`/Users/shane/26er/2c平台原型`,见其 openspec `bootstrap-2c-platform`)要把 Javert 作为引擎接入,有两个对接缺口:

1. **点菜批量审核没有 Web 端点**:Javert Web API 当前只有单规则 SSE `GET /api/audit/run?rule_id&patient_id`,而平台的「点菜模式」需要「一个患者 + 一组规则」一次跑完并流式回传。CLI 已有这个能力(`audit-patient --rules R191,R161 --concurrency N`,走 `_run_parallel`),但没暴露到 Web。
2. **审核结果不带 token 计量**:平台按 output token 计费(预计算时记录每条 `cost_tokens`,消费时扣)。`tools/llm_provider.py::chat()` 已经返回 `usage.completion_tokens`,但 `runner.audit()` 没有把 agent 循环里多次 LLM 调用的 `completion_tokens` **累加**进 `AuditResult` —— `AuditResult` 目前根本没有 token 字段。

本 change 补这两个缺口,**不动**审核判定逻辑、不动单规则 `/run`、不动 router。

## What Changes

- **新增 `GET /api/audit/run-batch?patient_id=&rules=R191,R161,...`**:SSE 流式跑「一个患者 × 多规则」,复用 CLI 已验证的 `_run_parallel`(ThreadPoolExecutor + 失败隔离 + `concurrency` 上限,sglang 端实际承受 3-5)。每条规则完成即推一个 `result` 事件,与单规则 `/run` 的事件格式一致。
- **抽取共享批量审核逻辑**:把 `commands/audit_patient.py` 的 `_run_serial` / `_run_parallel` 提到一个可被 CLI 与 Web 同时调用的模块(如 `audit/batch_runner.py`),避免重复实现。
- **`AuditResult` 加 `completion_tokens: int = 0` 字段**(默认 0,向后兼容老数据)。
- **`runner.audit()` 累加 token**:agent 循环里每次 `chat_with_retry` 返回的 `usage.completion_tokens` 累加,构造 `AuditResult` 时写入。
- **持久化 token**:`audit_store` 的本地 SQLite DDL + insert + 读回(`audit_store.py` 的 `AuditResult` 重建)加 `completion_tokens` 列;MSSQL 142 镜像表同步加列(幂等 ALTER)。
- **API 暴露**:`/api/audit/run`(单规则)与 `/run-batch` 的 `result` 事件、`/api/audit/runs/{run_id}` 详情都带上 `completion_tokens`。

## Capabilities

### New Capabilities

- `batch-audit-api` —— 患者级多规则 SSE 批量审核端点

### Modified Capabilities

- `audit-engine` —— 新增「每次审核的 output token 累加进 AuditResult 并持久化」需求(以 `## ADDED Requirements` 形式追加到该 capability,不改既有审核/判定行为)

## Impact

- **代码**(约 150-200 行新增 + 少量 patch):
  - 改 `src/javert/audit/result.py`:`AuditResult` +`completion_tokens` 字段
  - 改 `src/javert/audit/runner.py`:agent 循环累加 `usage.completion_tokens`(`llm_provider.chat()` 已返回),写入 `AuditResult`(构造点约 `runner.py:364`)
  - 新 `src/javert/audit/batch_runner.py`:从 `commands/audit_patient.py` 抽出 `_run_serial`/`_run_parallel` 共享逻辑
  - 改 `src/javert/commands/audit_patient.py`:改为调用共享 `batch_runner`(行为不变)
  - 新 `GET /api/audit/run-batch` 于 `src/javert/web/api/routes_audit.py`
  - 改 `src/javert/store/audit_store.py`:DDL + insert + 读回带 `completion_tokens`
- **数据/存储**:`audit_runs` 本地 SQLite + MSSQL 142 镜像表各加一列 `completion_tokens INT DEFAULT 0`(幂等 ALTER,老行为 0)
- **测试**:`/run-batch` SSE 多规则 happy path + 并发 + 失败隔离;token 累加单测(mock 多次 LLM 调用,验 `AuditResult.completion_tokens` = 各次之和);老数据读回 token=0 兼容
- **零破坏**:单规则 `/api/audit/run` 行为不变;审核判定/verdict/gate 完全不动;LLM 网关、router、规则 yaml 不动
- **下游解锁**:2c 平台 `bootstrap-2c-platform` 的 Javert 适配器(`engine-adapter` capability)与预计算(`precompute` capability,记录 `cost_tokens`)依赖本 change
