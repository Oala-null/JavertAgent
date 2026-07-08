# harden-agent-loop

## Why

2026-07 系统扫描 (主线二配套表) 确认 agent loop 仍有 5 处「静默吞 / 静默丢」路径: repair turn 丢弃模型的 tool_call 请求、裸 JSON 回退可能拼废或误取示例、畸形 tool_call 无针对性反馈导致模型反复犯同样格式错、全失败的工具调用仍解锁 verdict、prompt-fit 重跑静默覆盖人工手改 (R151 的 v1.5/v1.6 专家共识块处于裸奔状态). 与 fix-scan-residuals 分开成 change: 本组**改的是 LLM 对话行为**, 按项目协议须 5 患者 dry-run 对照后再批跑.

## What Changes

- **repair turn 支持 tool_call**: repair prompt 明说「若需证据可发 tool_call」但代码只解析 verdict——模型回 tool_call 被丢弃并直接 INCONCLUSIVE conf 0 且提前 break (budget 还有剩). 改为 repair 响应同样走 tool_call 分支, 回到主循环继续
- **裸 JSON 回退收紧**: 现取首 `{` 到末 `}` 整段 (reasoning 含花括号即拼出不可解析 blob), 且多候选取最后一个可能误取模型从 prompt 回显的示例 JSON. 改为平衡括号扫描逐个候选, 仍从后往前取首个含合法 verdict 字段的块
- **畸形 tool_call 针对性反馈**: `json.loads` 失败现只 logger.warning 静默丢弃, 模型收到的是泛化 repair 提示. 改为把解析错误回传给模型 (「你的 tool_call JSON 非法: <err>, 请修正重发」), 一次机会, 与既有 repair 预算合并不新增轮数上限
- **verdict 放行判定收紧**: 「至少 1 次 tool_call」改「至少 1 次**成功** tool_call」——工具全部执行失败 (返回错误串) 时不解锁裁决, 引导模型换参数重查或走 INCONCLUSIVE
- **prompt-fit 覆盖护栏**: `rule_writer` 渲染写盘时同时记录渲染产物 hash (yaml 内嵌字段); 下次 prompt-fit 前对比——`prompt_addon` 与上次渲染产物不一致 (被人工改过) 则拒绝覆盖并提示 diff, `--force` 显式绕过. 保护 R151 类专家手改不被静默清除
- **对照验证**: 按协议 5 患者 dry-run 对照 (含 1 个曾触发 repair/deadline 的病例), 确认无 V 级系统性漂移 + malformed 率不升

## Capabilities

### New Capabilities
- `agent-loop-resilience`: repair/deadline 路径的 tool_call 可续、裸 JSON 候选解析的括号平衡语义、畸形调用可感知反馈、成功调用才解锁裁决

### Modified Capabilities
- `rule-templating`: 模板渲染的手改保护 (渲染 hash 对比 + 拒绝静默覆盖)

## Impact

- 代码: `src/javert/audit/runner.py` (`_parse_verdict_block` / repair / 放行判定), `src/javert/tools/tool_executor.py` (解析错误回传), `src/javert/audit/rule.py` + `src/javert/templating/{rule_writer,prompt_fit_runner}.py` (渲染 hash 字段与护栏), 对应测试
- 行为面: repair/边界病例的裁决可能变化 (预期方向: malformed→有效裁决、错误证据裁决→INCONCLUSIVE), dry-run 对照留档
- rule yaml: 新增一个渲染 hash 字段 (向后兼容, 缺失视为「未知来源」仅警告不拦截, 首次重渲后补齐)
- 不影响: 工具语义、路由、gate、工作台
