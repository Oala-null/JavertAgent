## Why

2C 重复提交患者后，新轮次会先清空当前结果；若 Hub 取数在进入 Router 前失败，现有状态机仍把任务标成 `done`，形成“成功完成但结果为空”的误导响应。与此同时，LLM 输出达到 token 上限时，Runner 会把数万字符的截断内容原样放入 repair 上下文，导致第二次生成再次截断并向 2C 暴露半中半英的内部 malformed 原因。

## What Changes

- 修正并固化 Hub 手术明细唯一表名 `TB_OPERATION_DETAIL`，用离线测试防止部署代码再次漂移成 `TB_OPRATION_DETAIL`。
- 为 2C 每次实际入队的审计生成 `attempt_id`，在提交与查询响应中回显；运行中重复提交继续幂等复用同一 attempt。
- 在不删除或改名现有字段的前提下，为查询结果追加 `outcome`、`error_code`、`retryable` 和规则进度；患者级异常明确返回失败 outcome，不再仅靠 `done + results=[]` 表达。
- Runner 识别 LLM `finish_reason=length`；截断输出不得原样进入 repair 上下文，改走短提示、受限 token 的一次恢复。
- malformed 最终仍保守投影为 `INCONCLUSIVE`，但使用可对外展示的中文理由，并携带稳定诊断码供 2C 和运维区分格式失败。
- 补齐重复提交、患者级失败、Hub 表名、length 截断恢复及既有 repair 行为的回归测试，并同步 2C 契约与变更记录。

## Capabilities

### New Capabilities

- `2c-audit-attempt-status`: 定义 2C 审计 attempt 关联、兼容追加的完成结果、错误码和进度语义。

### Modified Capabilities

- `agent-loop-resilience`: 增加 token 截断感知和有界 repair，禁止把超长截断正文重新喂给模型。
- `hub-ba-fallback`: 固化手术明细规范表名及离线防漂移验证。

## Impact

- 代码：`src/javert/web/api/routes_audit.py`、`src/javert/audit/runner.py`、`src/javert/tools/llm_provider.py`、`src/javert/data/hub_source.py`。
- 契约：`POST /api/audit/submit` 和 `GET /api/audit/results/{SYXH}` 只追加字段；旧字段、路径和三态 verdict 保持兼容。
- 测试：`tests/test_routes_2c.py`、`tests/test_runner.py`、`tests/test_hub_source_ba.py`。
- 运维：后续部署 62 时只发布本 change 相关文件，并按 runbook 验证 Hub、服务状态和 2C 重提；本 change 本身不授权自动部署。
