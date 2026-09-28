## Context

2C 任务状态目前只存在进程内 `_2c_tasks[SYXH]`，`status` 只有 `running/done`。实际入队重跑会覆盖同患者旧任务并清空 `run_ids`；患者级异常虽然写入 `error`，但 `finally` 仍置 `done`。这对忽略 `error` 的调用方表现为“成功完成、零规则、空结果”。本次生产异常还证明 62 生效代码使用了错误表名 `TB_OPRATION_DETAIL`，而 142 规范表为 `TB_OPERATION_DETAIL`。

Runner 使用文本 `<tool_call>` 与 fenced JSON 协议。生产复现显示初始响应与 repair 都以 `finish_reason=length`、8192 completion tokens 截断；现有 repair 把约 3 万字符的失败正文追加回 messages，放大了第二次失败。2C 又把内部 reasoning 经简单字符串替换后直接返回，造成半中半英错误文本。

约束包括：2C/SSE 契约只加不删不改名；malformed 必须安全降级，不能升级为 CLEAN/VIOLATION；不得记录患者原文或完整 LLM 失败正文；本地工作区已有大量无关未提交修改；62 部署需另行授权。

## Goals / Non-Goals

**Goals:**

- 让 2C 在保持现有 `status/results/summary/error` 字段兼容的同时，可靠区分成功、部分成功和患者级失败。
- 给每次真正入队的重跑一个稳定 attempt 关联标识；运行中幂等提交复用原 attempt。
- 检测 length 截断并用短、有限 token 的恢复调用代替超长上下文 repair。
- 对外保留 `INCONCLUSIVE` 安全语义，但输出中文可读理由和稳定诊断码。
- 固化 Hub 手术表规范拼写并覆盖回归测试。

**Non-Goals:**

- 本 change 不持久化 `_2c_tasks`，服务重启后的旧结果回放继续沿用当前 SQLite 逻辑。
- 不迁移到原生 OpenAI tool calling，不引入新依赖，不改变三态 verdict。
- 不自动部署、重启或重提 62 任务。
- 不在本轮批量给 R020/R232 增加规则 precheck；该优化需要另行验证费用完整性。

## Decisions

### D1：保留 `status`，追加 attempt 与 outcome

`status` 继续返回 `unknown/running/done`，避免破坏既有客户端。每次实际入队生成 `attempt_id=att_<随机串>`；运行中重复提交返回当前 attempt，不新增队列项。查询追加：

- `outcome`: `unknown | running | succeeded | partial | failed`
- `error_code`: 空串或稳定机器码
- `retryable`: 布尔值
- `progress`: `{total, completed, failed}`

患者级异常或所有入选规则均失败时映射为 `status=done, outcome=failed`；至少一条规则成功、至少一条失败时映射为 `partial`。替代方案是扩展 `status=failed`，但会改变旧枚举，因此不采用。运行中幂等检查先于本地/Hub 数据探测，避免瞬时数据源故障把已在执行的 attempt 错误驳回；真正入队前仍在锁内二次检查竞态。

### D2：按执行阶段给患者级失败编码

任务在 Hub 取数、Router 和规则审计前更新内部 `stage`。worker 捕获异常时只对外暴露稳定码（`HUB_FETCH_FAILED`、`ROUTING_FAILED`、`AUDIT_FAILED`）及现有脱敏 `error`，不返回 SQL、表名、凭据或患者原文。基础设施类患者失败均标记 `retryable=true`。

### D3：length repair 不携带截断正文

Provider 从原始 choice 提取并直接返回 `finish_reason`。Runner 若发现 `finish_reason=length` 且当前输出不可解析：

1. 不把截断 assistant content 追加进 messages；
2. 只追加一条短 user 指令；
3. 使用 `min(512, llm_max_tokens)` 进行一次恢复；
4. 无成功工具时只允许返回一个简短 tool_call；已有成功工具时允许只返回 verdict JSON；
5. 恢复仍截断/无效时保持 `INCONCLUSIVE/confidence=0`，内部 reason 标记 truncated。

非 length 的既有 malformed repair 保持原行为，避免扩大变更面。简单增加 max tokens 或重复次数会让确定性失控输出消耗更多 GPU，故不采用。

### D4：2C 展示层翻译诊断，不改历史存储

SQLite 继续保存 Runner 原始、可检索的内部 reason。2C payload 根据已知 reason 派生 `diagnostic_code`、`retryable` 和中文 reasoning；普通裁决继续走 `humanize_reasoning`。任一规则级诊断可重试时，顶层 `retryable` 同步为 true，方便只读汇总字段的调用方采取动作。这样不需要迁移历史行，也不改变工作台追溯内容。

### D5：规范 Hub 表名只有一个

`hub_source.py` 查询和文档统一使用 `TB_OPERATION_DETAIL`；stub 测试必须断言 SQL 不含 `TB_OPRATION_DETAIL`。不增加双表 fallback，因为错误拼写不是合法兼容源，静默 fallback 会掩盖数据模型漂移。

## Risks / Trade-offs

- [客户端严格反序列化未知字段] → 2C 契约再次明确忽略未知字段，并先由 BFF 联调验证。
- [attempt 状态仍会随进程重启丢失] → 历史回放保持可用，`attempt_id=null` 表示 legacy/restart replay；持久任务表留后续 change。
- [512 token 恢复不足] → 恢复提示只允许一个 tool_call 或短 JSON；测试覆盖边界，失败仍安全落 I。
- [单规则 partial 被调用方误当全成功] → outcome 与 progress 同时返回，文档要求调用方显式处理 `partial/failed`。
- [本地已有用户修改重叠] → 只做最小 hunk，提交前逐文件检查 diff，不回滚或覆盖用户内容。

## Migration Plan

1. 本地运行定向单测、相关组合测试及 OpenSpec strict validate。
2. 后续获授权部署时，仅打包本 change 涉及文件；先核对 62 当前 SQL/Hub 环境实值和备份。
3. 部署代码后重启服务，验证 active、HTTP、进程环境和 Hub 表探针。
4. 重新提交失败患者，确认同一 attempt 从 running 进入 succeeded/partial，且结果非患者级空失败。
5. 回滚时恢复部署前代码备份；本 change 无数据库迁移。

## Open Questions

- attempt 跨重启持久化与按 attempt 查询历史由后续任务状态持久化 change 处理。
- 原生 tool calling/JSON schema constrained decoding 需基于部署 SGLang 版本单独评估，本 change 先完成最小止血。
