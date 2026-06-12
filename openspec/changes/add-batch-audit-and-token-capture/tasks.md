## 1. AuditResult 加 completion_tokens 字段

- [ ] 1.1 改 `src/javert/audit/result.py`:`AuditResult` +`completion_tokens: int = Field(default=0, ge=0)`(放在 `duration_ms` 附近)
- [ ] 1.2 扩 `tests/test_result.py`(或对应):构造带/不带 token 的 AuditResult,默认 0 兼容

## 2. runner 累加 token

- [ ] 2.1 改 `src/javert/audit/runner.py::audit`:agent 循环里累加每次 `chat_with_retry` 返回的 `usage.completion_tokens`(`llm_provider.chat()` 已返回 usage)
- [ ] 2.2 构造 `AuditResult`(约 `runner.py:364`)时传入累加值 `completion_tokens=...`
- [ ] 2.3 单测:mock LLM 三次调用各返回 completion_tokens=100/200/300 → `AuditResult.completion_tokens == 600`
- [ ] 2.4 边界:LLM 失败/早退/malformed 时 token 累加到失败前的值(不丢)

## 3. 持久化 token

- [ ] 3.1 改 `src/javert/store/audit_store.py` 本地 SQLite DDL:加 `completion_tokens INTEGER DEFAULT 0`(幂等)
- [ ] 3.2 改 insert:写入 `completion_tokens`
- [ ] 3.3 改读回(`audit_store.py:182` 附近重建 AuditResult):带 `completion_tokens`,缺列/老行 → 0
- [ ] 3.4 MSSQL 142 镜像表:建表脚本加列 `completion_tokens INT DEFAULT 0`(幂等 ALTER);sync 写入带该列
- [ ] 3.5 单测:写一条带 token 的结果 → 读回 token 一致;读老行(无列)→ 0

## 4. 抽取共享 batch_runner

- [ ] 4.1 新 `src/javert/audit/batch_runner.py`:从 `commands/audit_patient.py` 抽 `_run_serial` / `_run_parallel`(ThreadPoolExecutor + 失败隔离 + 进度回调)
- [ ] 4.2 进度回调抽象成参数(CLI 走 stderr 打印,Web 走 SSE 推送),核心并发逻辑共享
- [ ] 4.3 改 `commands/audit_patient.py`:改为调用 `batch_runner`,删除本地副本
- [ ] 4.4 回归:`uv run pytest tests/test_audit_patient.py -v` 全绿(abandoned 跳过 / `--rules` 强制纳入 / 缓存共享 / 失败隔离 / exit code 全部不变)

## 5. GET /api/audit/run-batch SSE 端点

- [ ] 5.1 新端点 `GET /api/audit/run-batch?patient_id=&rules=R191,R161&concurrency=` 于 `src/javert/web/api/routes_audit.py`
- [ ] 5.2 解析 + 校验 rules（逗号分隔；未知 rule_id 报错；空报 400）
- [ ] 5.3 调共享 `batch_runner`,每条规则完成 → 推 `result` 事件（含 `completion_tokens`），与单规则 `/run` 事件格式一致；批次结束推 `done` 事件（含汇总：规则数 / 各 verdict 计数 / Σ completion_tokens）
- [ ] 5.4 并发上限保护（默认与 CLI 一致，sglang 3-5）
- [ ] 5.5 失败隔离：单条规则 LLM 失败推 `fail` 事件但不中断整批

## 6. API 暴露 token

- [ ] 6.1 单规则 `/api/audit/run` 的 `result` 事件加 `completion_tokens`
- [ ] 6.2 `/api/audit/runs/{run_id}` 详情加 `completion_tokens`
- [ ] 6.3 `web/api/schemas.py` 对应响应模型加字段

## 7. 测试 / 验收

- [ ] 7.1 `/run-batch` SSE 多规则 happy path（mock LLM）：N 规则 → N 个 result 事件 + 1 个 done 汇总
- [ ] 7.2 `/run-batch` 失败隔离：第 2 条 fail，其余照常 + done 标记 failed 数
- [ ] 7.3 token 端到端：跑一条真实规则，验 `completion_tokens > 0` 且落库
- [ ] 7.4 `openspec validate add-batch-audit-and-token-capture --strict` 通过
- [ ] 7.5 全套 `uv run pytest tests/` 全绿（含 audit_patient 回归）

## 8. 文档

- [ ] 8.1 `CLAUDE.md` / `README.md`：新增 `/api/audit/run-batch` 端点说明 + `AuditResult.completion_tokens` 字段
- [ ] 8.2 标注与 2c 平台 `bootstrap-2c-platform` 的对接关系（本 change 是其 Javert 适配器 + 预计算的前置依赖）
