# verdict-gate — delta spec

## ADDED Requirements

### Requirement: 降级裁决 confidence 归一

verdict gate 降级一条 VIOLATION (改判 INCONCLUSIVE 或 CLEAN) 时, 系统 MUST 把落库的
confidence 归一到 0.5, 并 MUST 在 reasoning 的 gate 注记中保留原始 confidence 值.
未被 gate 降级的裁决 (changed=False 或非 VIOLATION) 系统 MUST NOT 修改其 confidence.

#### Scenario: 降级后 conf 归一并留档原值

- **WHEN** 一条 conf=0.90 的 VIOLATION 被 gate 降为 INCONCLUSIVE
- **THEN** 落库 confidence=0.5, 且 reasoning 的 `[gate: ...]` 注记含原值 0.90

#### Scenario: 未降级裁决 conf 不变

- **WHEN** 一条 VIOLATION 未被 gate 降级 (changed=False), 或裁决本身不是 VIOLATION
- **THEN** confidence 保持 LLM 原值, 不归一
