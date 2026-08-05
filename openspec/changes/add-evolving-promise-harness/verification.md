# 验证记录

日期：2026-08-04

## 失败基线与范围

- 修复前用四个去标识案例固定退费净量、假命中、公开类别重复分组和医生页面暴露内部字段，
  基线命令为 `.venv/bin/pytest -q tests/test_promise_drift_baseline.py`，结果为 4 failed。
- 运行 `.venv/bin/javert list` 并逐条核对当前 ready 规则后，`PR-D001` 仅显式纳入 R151 的
  抗体类重复检查边界；一次即违规、组合项目、串换、虚构、限定支付和 M1 主附项目均由
  near-negative 保护。本文不复制当前规则库存数量。
- 程序化读取本机静态参考 `docs/templates/260611医保基金监管规则框架总表.xlsx` 的“两库汇总”
  H/I 列，将带来源摘要的最小 pair 快照固化为 `configs/behavior_source_pairs.yaml`，再对账
  `configs/behavior_names.yaml`。生产 harness 仅依赖该版本化快照；当前 ready 映射均有正式
  pair 或合法显式例外，串换使用 `interchange-explicit-unmapped` 例外键并保留来源说明。
- 本地慢查询、断连接、失败不缓存和恢复重试故障注入已通过；62 回环、`--noproxy` 与浏览器
  三段采集未获授权，任务 1.4 保持 pending。

## 定向门禁

```text
.venv/bin/pytest -q tests/test_promises.py tests/test_promise_runtime.py \
  tests/test_promise_drift_baseline.py tests/test_public_presenter.py \
  tests/test_hub_raw_source.py
48 passed / 0 skipped / 0 failed / 0 errors

.venv/bin/javert promise validate
definitions=1 / drift_cases=4 / cases=12 / explicit_exceptions=1 / issues=0

.venv/bin/javert promise run
cases=15 / passed=15 / failed=0 / historical=1
```

`--json` 两个 CLI 变体也已验证成功，输出只包含安全标识、状态、原因码、计数和耗时分桶。

## 受影响模块组合门禁

覆盖 Runner、verdict gate、SQLite、SQL Server schema fake、drift guard、fee netting、肿瘤
结果兼容、工作台、命中解析、2C v1/v2/v3、SSE/event bus、anchor backfill 与导出：

```text
286 passed / 1 skipped / 0 failed / 0 errors
```

skip 为既有显式条件：`tests/test_runner.py:308` 的 for-else + 空 tool_records 分支在当前
Runner 结构下不可达；未排除测试文件，也没有新增债务排除清单。

去标识端到端命令：

```text
.venv/bin/pytest -q \
  tests/test_promise_drift_baseline.py::test_refund_net_single_is_locked_clean_before_llm
1 passed / 0 skipped / 0 failed / 0 errors
```

该案例走真实 Rule loader → Runner → fee netting → active Promise → LOCKED CLEAN，provider 被
设置为一旦调用就失败，因而同时证明零 LLM。

## 完整套件与静态门禁

```text
.venv/bin/pytest -q
1101 collected / 1100 passed / 1 skipped / 0 failed / 0 errors

node --check src/javert/web/static/app.js
passed

git diff --check
passed

openspec validate add-evolving-promise-harness --strict
passed
```

本 change 没有修改 `configs/rules/`、`configs/templates/` 或 `data/router/`；仅新增 Promise
资产并修改 `configs/behavior_names.yaml` 的显式公开类别例外。因此
`scripts/build_rule_mapping.py` 对任务 8.4 不适用，未重建 Router index。

## 发布状态

本地实现、文档、契约与测试门禁已完成。没有连接或写入 142 `sh_yb_platform`，没有执行 62
artifact/install、schema、restart 或生产冒烟。任务 8.6 保持 pending；获得上线授权后必须按
`docs/deployment_192_62.md` §10.12 完成受控 HEAD、schema、重启、2C v3、Promise 锁和原文
三段路径验收。
