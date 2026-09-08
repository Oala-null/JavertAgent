# 慢病结构化结果交付 QA（2026-09-08）

范围：AuditResult、SQLite/SQL Server、Workbench、API/SSE 的合成验收快照。
真实部署和单病例发布结果以 chronic_disease_criteria_qa.md 后续记录为准。

## 完成内容

- `clinical_criteria_evaluation` 使用现有 `ClinicalCriteriaEvaluation`；校验规则 ID、
  兼容 verdict 与肿瘤 payload 互斥，CD 不能产生 VIOLATION。
- 双库独立 nullable `clinical_criteria_json`，幂等补列、save/read/write、pending 精确
  回读与重试保持完整 proof；旧行不回填，SQLite 旧表缺列也可读取。
- Workbench 显示资格与 shadow 证据、中文状态/运算符、条件摘要、数值/单位/日期、
  决定分支和计数边界。原始节点/JSON 收进技术明细，缺失项关联条件摘要。
- `CHRONIC_CANDIDATE_MATCHED` 才显示“慢病命中（待复核）”；无候选单独提示。
  OCR 未人工核对、抽取失败/截断、不完整和费用完整性未核验均显式提示。
- “慢病”与 `Chronic_Disease` 入口使用 `filter=all`，导航保持批次；不改普通筛选 cookie，
  不沿用其他批次的旧前端 facet。CD 排除于普通 V/I/C badge、Dashboard 违规率与普通队列。
- v1/v2/v3、审计详情/列表、SSE 只新增字段；v2 文案清理不改慢病 proof 内容。
  全部三份 2C 对接文档已同步。
- 2C 静态核查：`ThirdPartyApiClient` 配置忽略未知字段；`ResExternalAuditItem` 无慢病
  字段，因此能兼容响应但尚不转发慢病结构。本次没有修改 2C。

## 验证

所有 pytest 均使用 `PYTHONPATH=src .venv/bin/python -m pytest`，已检查 import 路径
确实位于当前 worktree；测试使用临时 SQLite、合成记录和 SQL Server mock。

最终受影响组合：collected **271**，passed **271**，skipped **0**，failed **0**，errors **0**。

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_chronic_result_delivery.py tests/test_run_batch_integrity.py tests/test_audit_store.py tests/test_oncology_result.py tests/test_headline_sqlserver_store.py tests/test_workbench_templating.py tests/test_workbench_routes.py tests/test_sqlserver_ocr_visibility.py tests/test_routes_2c.py tests/test_web_api.py tests/test_web_app.py tests/test_drift_guard.py tests/test_chronic_runtime.py tests/test_precheck.py tests/test_verdict_gate.py -m 'not slow' -q --disable-warnings --tb=short
node --check src/javert/web/static/app.js
openspec validate add-chronic-disease-criteria --strict
git diff --check
```

以上检查均通过。两项新相关失败（肿瘤混用测试夹具非法、批量 SSE 字段集合未更新）已修正。
这里报告的是受影响组合，不代表全量全绿；全量非慢测试及既有 XLSX/fixture 债务见主 QA 报告。

未连接真实 SQL Server；SQL Server 迁移验证为 DDL 幂等/nullable 契约和 mock 写读。
Workbench 验证为模板/路由回归与 JS 语法检查，没有执行线上浏览器验收。
证据跳转只在当前患者页内按 source locator 定位并精确匹配；原始记录重排/缺失或当前
原文页不支持的来源（独立诊断、手术）会提示未能精确定位，不做模糊高亮。
本组合测试未读取真实患者或凭据；它不代表生产 SQL 验收，change 未归档。
