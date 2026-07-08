# tool-cache-concurrency Specification

## Purpose
TBD - created by syncing change boost-llm-efficiency. Update Purpose after archive.
## Requirements
### Requirement: 工具缓存 per-key 锁

工具执行缓存 MUST 采用 per-key 锁: 不同缓存键的工具调用 MUST NOT 互相阻塞; 相同缓存键的并发调用 MUST 只执行一次计算, 其余调用等待并复用结果 (compute-once). 缓存命中/结果语义 MUST 与现有全局锁实现一致.

#### Scenario: 不同工具不排队

- **WHEN** `--concurrency 5` 下线程 A 执行耗时的 `search_lab_results` (触发 LabLoader 首建), 线程 B 同时发起 `search_fees`
- **THEN** 线程 B 不等待 A, `search_fees` 立即执行

#### Scenario: 同 key 只算一次

- **WHEN** 两个线程同时发起完全相同的工具调用 (同工具同参数)
- **THEN** 工具实际计算恰好一次, 两线程拿到同一结果

#### Scenario: 共享缓存行为回归

- **WHEN** `audit-patient --share-tool-cache` 跨规则复用缓存
- **THEN** 缓存命中语义与本 change 之前一致 (命中即不重算)
