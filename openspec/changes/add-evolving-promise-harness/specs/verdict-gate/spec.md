## ADDED Requirements

### Requirement: Verdict Gate 必须尊重 Promise 终局锁
当 AuditResult 携带合法 active Promise 的 `finality=LOCKED` trace 时，verdict gate MUST 原样返回 verdict、confidence 和 trace，不得再执行低置信降级、计数存在性闸、规则专用后置闸或其他普通降级逻辑。

#### Scenario: 锁定 CLEAN 置信度低于普通阈值
- **WHEN** terminal Promise 生成 LOCKED CLEAN，且兼容 confidence 值低于普通 confidence floor
- **THEN** verdict gate 保持 CLEAN
- **AND** 不将结果改为 INCONCLUSIVE

#### Scenario: 锁定结果触发普通规则专用闸
- **WHEN** LOCKED 结果的事实也满足某个普通 verdict gate 的降级条件
- **THEN** gate 不执行该降级
- **AND** Promise trace 原样传递给持久化层

### Requirement: 未锁定结果必须保持既有 Gate 语义
没有合法 LOCKED trace 的结果 MUST 继续执行现有 verdict gate 全部规则；本 change MUST NOT 让 gate 创建、提升、猜测或修复 Promise 锁。

#### Scenario: 普通低置信违规
- **WHEN** VIOLATION 结果没有 LOCKED trace 且 confidence 低于既有阈值
- **THEN** 继续按现有 confidence floor 规则降级

#### Scenario: 伪造或不完整 trace
- **WHEN** 结果携带未知 Promise、非 active 版本、缺失必填字段或非 LOCKED finality
- **THEN** verdict gate 不把它视为终局锁
- **AND** 继续既有 gate 逻辑并生成安全内部诊断

### Requirement: Gate 不得改变 Promise 证据
verdict gate MUST 将合法 Promise trace 作为不可变审计证据传递；它 MUST NOT 修改 Promise ID、版本、kind、reason code 或事实摘要，也 MUST NOT 向 trace 注入 LLM reasoning 或患者原文。

#### Scenario: 锁定结果通过 Gate
- **WHEN** 合法 LOCKED 结果进入 verdict gate
- **THEN** gate 前后的规范化 Promise trace 完全一致
- **AND** 输出日志仅包含安全的 Promise ID、版本和处理状态
