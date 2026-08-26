## 1. 模型输出与最终 headline 门控

- [x] 1.1 为 `AuditResult` 增加向后兼容的 headline 字段，并同步严格 verdict JSON Schema、base prompt、repair、deadline 和 tool-budget 收敛提示
- [x] 1.2 实现 headline 结构、公开安全和最终 verdict 一致性门控；门控失败只做确定性回退，不修改主裁决或再次调用模型
- [x] 1.3 在 verdict gate、结构化肿瘤求值、Promise 和技术质量隔离全部完成后接入 headline finalizer
- [x] 1.4 为 precheck 短路、Promise、肿瘤结构化求值、无有效 verdict 和技术故障实现不解析 reasoning 的确定性 headline
- [x] 1.5 增加普通、多项目、超长/多行、禁词、患者标识、V→I 降级、CLEAN 和技术失败的 headline gate 测试

## 2. 持久化与历史兼容

- [x] 2.1 给 SQLite schema 和幂等 migration 增加 nullable headline，并更新 write/read/replay/sync 路径与旧行测试
- [x] 2.2 给 SQL Server `javert_audit_runs` 幂等 schema 增加 nullable headline，并更新 INSERT、完整查询、历史回放和同步映射
- [x] 2.3 增加 SQLite/SQL Server 双写一致性、NULL 旧行和重复 replay_key 路径回归测试
- [x] 2.4 审计所有按位置读取 `javert_audit_runs` 的查询，避免新增列导致索引错位或 OCR/Workbench 丢字段

## 3. 公开投影与 2C additive 契约

- [x] 3.1 扩展 public presenter：新行返回已门控 headline，旧行使用规则元数据与最终 verdict 确定性回退，不从 reasoning 猜事实
- [x] 3.2 在 v1、v2、v3、SSE 和 Workbench 完整结果 additive 返回顶层 headline 与 `public_explanation.headline`
- [x] 3.3 保持 reasoning/evidence/narrative 原字段、matched_items 和端点语义不变，更新严格字段集合与旧客户端兼容测试
- [x] 3.4 更新 v1/v2/v3 2C 对接文档，提供 headline/reasoning/evidence 示例、长度语义和下游部署顺序

## 4. 质量评估与可观测性

- [x] 4.1 增加 headline 生成、门控回退和原因枚举计数，验证日志不包含 headline、reasoning、患者或运行标识
- [x] 4.2 建立至少 20–30 条去标识 golden 结果，覆盖三种 verdict、多药品、gate 降级、precheck、Promise、肿瘤结构化和技术故障
- [x] 4.3 验证格式/单行/禁词/最终 verdict 一致率 100%，并人工检查 headline 未新增无证据对象或事实

## 5. 验证与发布准备

- [x] 5.1 运行 runner/result/store/public presenter/2C routes/SSE/Workbench 定向测试与受影响组合测试
- [x] 5.2 运行仓库全量门禁，按 AGENTS.md 原样记录 collected/pass/skip/fail/error 和既有债务排除情况
- [x] 5.3 运行 OpenSpec strict validation，确认 proposal/design/spec/tasks 与真实验证命令一致
- [x] 5.4 按 62 runbook 准备“代码 → ensure-mssql-schema → 重启 → v3 additive 合同”发布清单
- [x] 5.5 向 2C 交付 nullable schema 已就绪证据和去标识 v3 fixture，作为 `add-progressive-audit-disclosure` 开始 OCR 查询升级的门禁
- [x] 5.6 经单独授权将 Javert upstream 发布到 62，验证 schema、进程环境、健康状态、unknown GET 与隔离合成 v3 completed-card 合同；未触发真实患者

## 验证记录（2026-08-26，本地与 62）

- 定向/组合：`.venv/bin/pytest -q tests/test_headline.py tests/test_runner.py tests/test_drift_guard.py tests/test_audit_store.py tests/test_headline_sqlserver_store.py tests/test_public_presenter.py tests/test_routes_2c.py tests/test_run_batch_integrity.py tests/test_event_bus.py tests/test_workbench_routes.py tests/test_workbench_templating.py tests/test_web_api.py tests/test_oncology_result.py tests/test_promise_runtime.py tests/test_heartbeat.py` → `206 passed, 1 skipped`。
- 全量：`.venv/bin/pytest -q` → `1200 collected / 1199 passed / 1 skipped / 0 failed / 0 errors`；唯一 skip 为既有 `test_deadline_skipped_when_no_tool_called`（当前 runner 结构下不可达的理论分支），未排除任何测试。
- 端到端/fixture：`.venv/bin/javert list` 成功；`python -m json.tool` 校验 `tests/fixtures/headline_golden.json` 与 `docs/fixtures/v3_headline_contract_fixture.json` 成功。
- OpenSpec：`openspec validate add-public-audit-headline-contract --strict` → `Change 'add-public-audit-headline-contract' is valid`。
- 生产：Javert runtime `f24071e338c65b82369a71ee947795e928f35cc5` 已按
  `docs/deployment_192_62.md` §10.14 完成 artifact/install、SQL Server 与 SQLite nullable schema、
  重拉、环境逐键一致、登录/健康/Hub、unknown GET 和隔离合成 v3 completed-card 合同验证；官方
  sync check 通过。未调用业务 submit、未触发真实患者，生产新行双写仍待已授权去标识 case。
