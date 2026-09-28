# audit-engine Specification

## Purpose
TBD - created by archiving change pilot-deterministic-precheck. Update Purpose after archive.
## Requirements
### Requirement: 带 precheck 字段规则的 precheck-first 执行路径

`Runner.audit` 对声明了 `precheck` 字段且 `precheck` 开关未关的规则, MUST 在 LLM agent loop **之前**先跑确定性预检:
- 预检 `clean` → 直接构造 CLEAN 结果 (0 次 LLM 调用、0 次工具调用), 结果 MUST 记录 precheck 降级标签与可解释 reason。
- 预检 `facts` → 把事实块注入初始用户消息后进入既有 LLM loop; LLM 判 VIOLATION 时 MUST 并入预检给定的费用行 evidence。
- 预检 `skip` 或规则无 `precheck` 字段或开关关闭 → 走既有 LLM loop, 行为与本 change 之前逐字一致。

裁决后确定性 gate (`apply_gate`) 逻辑 MUST NOT 因本 change 改动。

#### Scenario: 预检短路 CLEAN 零 LLM 调用

- **WHEN** 某带 precheck 的 M1 规则对某患者预检返回 `clean`
- **THEN** `Runner.audit` 返回 verdict=CLEAN、`tool_calls` 为空、未调用 LLM provider, 且结果携带 precheck 标签

#### Scenario: 预检事实成立仍走 LLM 且结果合并锚点

- **WHEN** 预检返回 `facts` 且 LLM 最终裁决 VIOLATION
- **THEN** 结果 verdict=VIOLATION, 其 evidence 含预检并入的费用行锚点条目 (与 LLM 自报 evidence 合并去重)

#### Scenario: 无 precheck 字段规则零行为变化

- **WHEN** 对一条未声明 `precheck` 字段的规则 (含 M2/M3…及被跳过的 M1) 跑 audit
- **THEN** 执行路径与本 change 之前完全一致 (先 LLM 自由探索后过 gate), 不发生短路或事实注入

