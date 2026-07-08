# verdict-gate Specification

## Purpose
裁决落库前的确定性闸: 对 LLM 产出的 VIOLATION 只降不升, 用置信度底线与临床存在性判据消除假阳性, 同时不误伤"真实服务按次超收"这类计数型真违规。

## Requirements
### Requirement: conf 底线闸覆盖全部低置信 VIOLATION

verdict gate 的③闸 MUST 把 confidence 低于 `conf_ceiling` 的所有 VIOLATION 降为 INCONCLUSIVE (打「低置信降级」标签), 包括 confidence 字段缺失或非法而归 0 的情况. 系统 MUST NOT 落库 confidence 低于 `conf_ceiling` 的 VIOLATION 行.

#### Scenario: conf 0.5 的 V 被拦

- **WHEN** LLM 输出 verdict=VIOLATION, confidence=0.5 (低于 conf_floor 0.70)
- **THEN** gate 改判 INCONCLUSIVE, tag=低置信降级

#### Scenario: confidence 缺失的 V 被拦

- **WHEN** LLM 输出 verdict=VIOLATION 且无 confidence 字段 (解析归 0.0)
- **THEN** gate 改判 INCONCLUSIVE, 不再出现 "VIOLATION conf 0.00" 落库行

#### Scenario: 高置信 V 不受影响

- **WHEN** LLM 输出 verdict=VIOLATION, confidence=0.90 (≥ conf_ceiling 0.85)
- **THEN** ③闸不改判 (其余闸照常评估)

### Requirement: 计数类规则不适用存在性闸

对违规形态为"真实服务按次超收"的计数类规则 (当前为 R205), ⑥麻醉存在性闸 MUST NOT 将其 VIOLATION 改判——服务真实存在恰是此类规则的违规前提, 存在性证据不构成反证.

#### Scenario: R205 真超收不再被闸放走

- **WHEN** R205 判 VIOLATION (1 台手术收取多次全麻费用), 且患者麻醉记录真实存在
- **THEN** ⑥闸不改判, verdict 保持 VIOLATION 进入落库 (仍受其他闸如③conf 闸约束)

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
