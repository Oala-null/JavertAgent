## Context

Javert 已有的相关现状(已核对代码):

- **单规则 Web SSE**:`src/javert/web/api/routes_audit.py` 暴露 `GET /api/audit/run?rule_id&patient_id`,流式 `start` / `trace` / `result` / `fail` 事件。
- **CLI 已有批量能力**:`src/javert/commands/audit_patient.py` 的 `_run_serial`(L199)/ `_run_parallel`(L261,`ThreadPoolExecutor` + `as_completed` + 失败隔离,L298),由 `audit-patient --rules --concurrency` 驱动。**但只在 CLI,未上 Web。**
- **LLM 已返回 token**:`src/javert/tools/llm_provider.py::chat()`(L55)返回 `{content, reasoning_content, usage, raw_response}`,其中 `usage.completion_tokens`(L96)已解析。
- **runner 未累加**:`src/javert/audit/runner.py::audit()`(L111)跑 agent 多轮 LLM,构造 `AuditResult`(L364)时**没有**汇总 token。
- **AuditResult 无 token 字段**:`src/javert/audit/result.py::AuditResult`(L37)有 `duration_ms/model/started_at/gate_tag`,无 token。
- **持久化**:`src/javert/store/audit_store.py` 写本地 SQLite + 试推 MSSQL 142;读回在 `audit_store.py:182` 重建 `AuditResult`。

## Goals / Non-Goals

**Goals:**
- 给 2c 平台一个「患者 × 多规则」SSE 批量端点,行为/事件格式与单规则 `/run` 一致
- 让每条 `AuditResult` 带上该次审核消耗的 output token(`completion_tokens`),并落库
- CLI 与 Web 共享同一套批量审核逻辑,不重复实现

**Non-Goals:**
- ❌ 改审核判定 / verdict / gate 逻辑(本 change 只加计量与端点)
- ❌ 改单规则 `/api/audit/run` 的行为
- ❌ router 预筛(点菜是显式规则列表,绕过 router —— 与 `audit-patient --rules` 一致)
- ❌ 病案 ingestion(2c 用预置 demo,Javert 端不变)
- ❌ prompt/input token 计费(平台按 output token 计,只采 `completion_tokens`;`prompt_tokens` 可选记录但不计费)

## Decisions

### D1. `/run-batch` 用 GET SSE,不用 POST 同步
**选项**:(a) `GET /api/audit/run-batch?patient_id=&rules=R191,R161`(SSE,逐条推)/ (b) `POST /api/audit/run-batch`(body 带 rules 数组,同步返回全部)。**选 a**:与现有 `GET /api/audit/run` SSE 风格一致,前端已会消费 SSE;批量耗时长(多规则),流式逐条出比同步等全部更适合「迅速检查」体验。rules 用逗号分隔 query 参数(与 CLI `--rules` 一致)。

### D2. 抽取 `batch_runner` 共享,不在 Web 复制一份
**选项**:(a) Web 路由里复制 `_run_parallel` / (b) 抽 `audit/batch_runner.py`,CLI 与 Web 同调。**选 b**:避免两处并发/失败隔离逻辑漂移;`audit_patient.py` 改为调用共享模块,CLI 行为不变(回归测试保证)。

### D3. token 采集 = 在 runner 累加 provider 已返回的 `completion_tokens`
**选项**:(a) 在 `llm_provider` 层做累加 / (b) 在 `runner.audit` 的 agent 循环累加每次 `chat_with_retry` 的 `usage.completion_tokens`。**选 b**:provider 是无状态单次调用,累加的自然边界是「一次审核 = 一个 agent 循环」,正好在 runner;循环结束把累加值写进 `AuditResult(completion_tokens=...)`。

### D4. `AuditResult.completion_tokens` 默认 0,向后兼容
老数据/老缓存没有 token,默认 0。2c 预计算消费 token=0 的结果时应视为「未采集」并触发重跑采集(由 2c `precompute` 侧处理,见 `bootstrap-2c-platform`)。Javert 侧只保证新审核带真实值。

### D5. 持久化加列,幂等 ALTER
本地 SQLite + MSSQL 142 镜像表各加 `completion_tokens INT DEFAULT 0`;建表脚本幂等(`ADD COLUMN IF NOT EXISTS` / try-add 容错)。读回时缺列 → 0。

### D6. 只采 `completion_tokens`(output),`prompt_tokens` 可选旁路
平台按 output token 计费(2c D14)。`completion_tokens` 是计费口径,必采;`prompt_tokens`/`total_tokens` 可选一并记录(便于成本分析),但不参与计费,本 change 默认只落 `completion_tokens`,留扩展位。

## Risks / Trade-offs

- **[R1] 抽取 `batch_runner` 可能回归 CLI 行为** → Mitigation:抽取后 `audit-patient` 走同一函数,跑现有 `tests/test_audit_patient.py` 全套(abandoned 跳过 / `--rules` 强制纳入 / 缓存共享 / 失败隔离 / exit code)确认 bit 级不变。
- **[R2] `/run-batch` 并发打爆 sglang** → Mitigation:沿用 `concurrency` 上限(默认与 CLI 一致,提示 3-5);Web 端可加默认上限保护。
- **[R3] token 累加遗漏某些 LLM 调用路径**(如 verdict 修复重试、gate 不调 LLM) → Mitigation:在 runner 唯一的 `chat_with_retry` 调用点累加,单测用 mock 计数多次调用验证总和;gate 是确定性层不调 LLM,不计 token。
- **[R4] 老 L1/L2 / SQLite 行 token=0** → Mitigation:DDL 默认 0;文档说明 0 = 未采集;2c 预计算重跑后即有真值。

## Open Questions

- **Q1**:`/run-batch` 是否要支持 `priority` 过滤模式(像 CLI `--priority P0`)而不仅是显式 `rules`? 倾向先只做显式 `rules`(2c 点菜就是显式列表),priority 模式后续按需加。
- **Q2**:是否一并暴露一个 `GET /api/audit/cost/{run_id}` 之类的纯 token 查询? 倾向不单开,`completion_tokens` 直接进 `result` 事件与 `runs/{run_id}` 详情即可。
