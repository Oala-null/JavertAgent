# 验证记录

日期：2026-08-05

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
- 本地慢查询、断连接、失败不缓存和恢复重试故障注入已通过；获授权后完成 62 回环、客户端
  `--noproxy` 与浏览器三段采集。三条路径均可到达服务，未复现代理 502；真实 404 与合成
  Hub 不可用的可重试 503 可明确区分，未通过放大 SQL 或代理 timeout 掩盖问题。

## 定向门禁

```text
.venv/bin/pytest -q tests/test_promises.py tests/test_promise_runtime.py \
  tests/test_promise_drift_baseline.py tests/test_public_presenter.py \
  tests/test_hub_raw_source.py tests/test_deployment_sync.py
53 passed / 0 skipped / 0 failed / 0 errors

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
1103 collected / 1102 passed / 1 skipped / 0 failed / 0 errors

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

用户明确授权后按 §10.12 完成受控发布，功能运行基线提交为
`ec90ce0f1baafe2c355f8b86251b6b6a3ed15da1`：本地与 62 HEAD 相等，远端分支
`production-62`、受控工作树 clean；systemd `active/running`、登录页 HTTP 200，连续检查
restart 计数稳定。安装前后 27 个 `JAVERT_*` 进程环境逐值一致，SQL/Hub/肿瘤关键开关符合
既有生产口径。

schema 先于重启幂等执行，`zadig.javert_audit_runs.promise_trace_json` 为可空列且既有 null 行
仍存在；SQL 健康、同步线程和只读 `sh_yb_platform` `SELECT 1` 均通过，没有修改 Hub schema
或患者数据。生产 `promise validate` 为 definitions=1、drift_cases=4、cases=12、issues=0，
`promise run` 为 15/15；positive 得到锁定 CLEAN，全部 near-negative 保持不适用。

62 回环 v3 空数组 submit 返回 HTTP 202，不存在的去标识号 results 返回 HTTP 200/unknown；
客户端 `--noproxy` 的登录、健康和 v3 路径均成功。浏览器现有登录态下，去标识测试号的
notes/fees/labs 三页签均成功，合成不存在号返回 `RAW_TAB_NOT_FOUND`；62 已安装代码的独立
进程故障注入返回 HTTP 503、`retryable=true`，未修改在线进程、数据库或 Hub。部署过程中
生产 Promise 验证先暴露稀疏范围漏带案例及本机工作簿不可版本化两个缺口；最终以部署
`tests/promise_cases` 和带来源摘要的 `configs/behavior_source_pairs.yaml` 修复，并在最终 HEAD
重新完成全部验收，未把中间失败冒充成功。

## 公开审核说明语义保真纠偏（2026-08-05）

- 62 生产反馈显示：数据库旧行 reasoning 仍完整，但工作台只渲染结构化空态，丢失收费、诊断、
  证据缺口和降级理由。只读对比确认问题位于展示投影，未修改或回填数据库 reasoning。
- 新增去标识失败基线，修复前在缺少 `public_explanation.narrative` 处失败；修复后 presenter 将
  reasoning 仅做中文化和内部术语清洗，并保持结构化数组不从散文猜测。
- 工作台默认展开“审核说明”；2C v1/v2/v3 以 additive 字段共享同一 `narrative`，既有字段名和
  原始 evidence JSON 的隐藏边界不变。
- 修复前定向测试在 `public_explanation.narrative` 缺失处 1 failed；修复后 Web/2C/presenter
  组合门禁为 82 passed，Promise validate 为 issues=0，Promise run 为 15/15，完整套件为
  1104 collected / 1103 passed / 1 skipped / 0 failed / 0 errors，`git diff --check` 与
  `openspec validate add-evolving-promise-harness --strict` 均通过。
