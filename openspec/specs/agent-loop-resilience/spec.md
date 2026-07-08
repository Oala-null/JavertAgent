# agent-loop-resilience Specification

## Purpose
TBD - created by syncing change harden-agent-loop. Update Purpose after archive.
## Requirements
### Requirement: repair 响应的 tool_call 可续

repair turn 的提示明说「若需证据可发 tool_call」, 因此 repair 响应中若含合法 `<tool_call>`, runner MUST 执行这些工具、把结果回灌对话并回到主循环继续调查, 不得丢弃 tool_call 直接判 INCONCLUSIVE. 该续跑消耗既有 tool budget (主循环轮数上限), 不新增独立重试计数.

#### Scenario: repair 回 tool_call 被执行并续跑

- **WHEN** 某轮输出既非合法 verdict 也非 tool_call, 触发 repair, 且 repair 响应含 1 个合法 `<tool_call>`
- **THEN** 该工具被执行, 结果进入对话, 主循环继续下一轮 (而非直接落 INCONCLUSIVE conf 0)

#### Scenario: repair 回合法 verdict 仍收敛

- **WHEN** repair 响应是合法 verdict JSON 且此前已有成功 tool_call
- **THEN** 接受该 verdict 并收敛 (行为不变)

### Requirement: 裸 JSON 回退用括号平衡扫描

无 fenced ```` ```json ```` 围栏时的裸 JSON 回退 MUST 用括号平衡扫描 (识别字符串字面量与转义) 逐个提取顶层 `{...}` 对象候选, 再从后往前取首个可 `json.loads` 且含合法 `verdict` 字段的块; 不得再用「首 `{` 到末 `}`」贪婪切片 (reasoning 内含花括号即拼出不可解析 blob).

#### Scenario: reasoning 含花括号不再拼废

- **WHEN** 裸输出为 `思考{中间有}花括号\n{"verdict":"CLEAN","confidence":0.8}`
- **THEN** 提取出末尾 `{"verdict":"CLEAN",...}` 并解析成功 (而非首`{`到末`}`整段解析失败)

#### Scenario: 无合法 verdict 仍返回 None

- **WHEN** 裸输出的所有平衡对象均无合法 verdict 字段
- **THEN** 返回 None (与既有语义一致)

### Requirement: 畸形 tool_call 针对性反馈

当模型输出含 `<tool_call>` 标签但其 JSON 无法解析 (既非合法 verdict 又无可执行 tool_call) 时, runner MUST 把具体的 JSON 解析错误回传给模型 (「你的 tool_call JSON 非法: <err>, 请修正后重发」), 给一次修正机会; 该反馈复用既有 repair 预算, 不新增轮数上限. 无 tool_call 标签的泛化解析失败仍走原通用 repair 提示.

#### Scenario: 畸形 tool_call 得到具体错误

- **WHEN** 模型发出 `<tool_call>{"name":"x","arguments":{}</tool_call>` (缺右括号)
- **THEN** 下一轮提示包含该 JSON 的解析错误信息, 引导模型修正重发

### Requirement: 至少一次成功 tool_call 才解锁裁决

runner 接受最终 verdict 的前置条件 MUST 从「至少 1 次 tool_call」收紧为「至少 1 次**成功** tool_call」(工具执行未返回错误串). 当所有 tool_call 均执行失败 (未知工具 / 抛异常) 时, runner MUST 拒绝该 verdict 并提示模型换参数重查或走 INCONCLUSIVE. 工具「成功与否」的判定由 ToolExecutor 依据其自身生成的错误串前缀给出 (生产与判定共用同一常量, 避免耦合漂移).

#### Scenario: 工具全失败不解锁 V

- **WHEN** 模型仅发出的 tool_call 全部返回错误串, 随后输出 VIOLATION verdict
- **THEN** runner 拒绝该 verdict 并要求重查 (不落 VIOLATION)

#### Scenario: 有一次成功即可裁决

- **WHEN** 模型的多次 tool_call 中至少 1 次成功, 随后输出合法 verdict
- **THEN** 接受该 verdict (行为与改前一致)
