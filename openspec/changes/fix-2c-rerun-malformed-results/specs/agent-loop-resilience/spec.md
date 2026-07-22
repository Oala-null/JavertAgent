## ADDED Requirements

### Requirement: length 截断触发有界恢复

Provider MUST 向 Runner 暴露模型响应的 `finish_reason`。当响应 `finish_reason=length` 且既无合法 tool_call 也无合法 verdict 时，Runner MUST NOT 把截断正文追加回 repair 上下文；Runner MUST 使用短恢复提示和不超过 512 completion tokens 的一次恢复调用。

#### Scenario: 首轮 tool_call 被长度截断

- **WHEN** 首轮响应达到 token 上限、只含不完整 tool_call 且没有成功工具调用
- **THEN** repair 请求不包含截断正文，只要求输出一个简短合法 tool_call，并将 `max_tokens` 限制在 512 以内

#### Scenario: 已有证据后的 verdict 被长度截断

- **WHEN** 已至少成功执行一个工具，最终 verdict 响应因 length 被截断
- **THEN** repair 请求不包含截断正文，只要求基于既有证据输出短 verdict JSON，并将 `max_tokens` 限制在 512 以内

#### Scenario: 有界恢复仍截断

- **WHEN** length 恢复响应仍无法解析
- **THEN** Runner MUST 保守返回 `INCONCLUSIVE/confidence=0`，reason 标识输出截断，不得升级为 CLEAN 或 VIOLATION

### Requirement: 非 length repair 行为保持兼容

普通 malformed 输出的既有一次 repair、repair tool_call 可续和成功工具门槛 MUST 保持不变。

#### Scenario: 普通 malformed 可被修复

- **WHEN** 非 length 的初始 verdict JSON 畸形，repair 返回合法 verdict 且此前已有成功工具
- **THEN** Runner 接受该 verdict，行为与变更前一致
