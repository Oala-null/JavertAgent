# workbench-raw-source — delta spec

## ADDED Requirements

### Requirement: 链式取数次序与未命中语义

工作台患者原文取数 (`/api/patient/{pid}/raw` 的 notes/fees) SHALL 按固定次序链式查找: 先查本机 CSV (base + `data_import/` overlay), notes 与 fees 双空且 hub 源开启时再按患者号查 hub SQL。系统 MUST 仅在 CSV 与 hub 均未命中时返回 404; CSV 命中的患者 MUST NOT 触发 hub 查询 (现有患者展示行为与延迟零变化)。

#### Scenario: hub 患者不再 404

- **WHEN** 患者 211318013 不在 62 任何 CSV 中, 但存在于 `TP_data_hub`, 且 `JAVERT_HUB_RAW_ENABLED=true`
- **THEN** `/api/patient/211318013/raw` 返回 200, notes/fees 内容与该患者流B ETL 产出一致 (同列同值)

#### Scenario: CSV 命中不查库

- **WHEN** 患者 J66252 在 base CSV 中
- **THEN** 原文直接从内存 CSV 返回, 不产生任何 hub SQL 查询, 响应与本 change 之前逐字段一致

#### Scenario: 双 miss 仍 404

- **WHEN** 患者号 XXXX 在 CSV 与 hub 中均不存在
- **THEN** 返回 404 "未找到患者原始数据"

### Requirement: 开关默认关闭

hub SQL 源 MUST 由配置开关控制 (`JAVERT_HUB_RAW_ENABLED`, 默认 false)。开关关闭时系统行为 MUST 与本 change 之前完全一致 (纯 CSV, 不建 hub 连接)。

#### Scenario: 未配置部署零变化

- **WHEN** 62 部署新代码但 `.env` 未加 `JAVERT_HUB_RAW_ENABLED`
- **THEN** 工作台不连 `TP_data_hub`, 所有取数路径与改前相同

### Requirement: SQL 异常降级为未命中

hub 查询发生任何异常 (连接失败/超时/权限) 时系统 MUST 记录 warning 日志并按"hub 未命中"继续 (CSV 命中的照常返回, 双 miss 返回 404), MUST NOT 向前端抛 5xx, MUST NOT 影响 CSV 路径可用性。

#### Scenario: hub 断连不伤老患者

- **WHEN** 142 不可达, 专家点开 CSV 内患者 J66252 的原文
- **THEN** 原文正常返回, 无感知

#### Scenario: hub 断连时新患者温和失败

- **WHEN** 142 不可达, 专家点开仅存 hub 的患者
- **THEN** 返回 404 (而非 500), 服务端有 warning 日志

### Requirement: 映射单一来源

TB_* → 内部列契约映射 MUST 存在于唯一共享模块, `scripts/etl_from_data_hub.py` 与工作台 hub 源 MUST import 同一实现。提取重构后脚本对同一患者集合的 6 文件产出 MUST 与提取前内容一致 (排序后逐字节一致; fee/labs/exams 原始查询无 ORDER BY, 行序提取前后均不保证, 属原有行为)。

#### Scenario: 脚本回归内容一致

- **WHEN** 对同一患者列表分别用提取前/后的脚本跑流B ETL
- **THEN** 6 个 CSV 排序后逐字节 diff 为空 (实测 5 混合患者已过)

### Requirement: 检验与主诊断同链回退

检验 tab 数据与病案主诊断 SHALL 同样接入链式回退: 本机 CSV/文件未命中该患者时, hub 源开启则查 hub (检验 = LIS 指标⋈报告, 主诊断 = 诊断表 maindiag 行)。文件缺失或 hub 未命中 MUST 优雅降级为空列表/空值, 不阻断 raw 响应。

#### Scenario: hub 患者检验记录可见

- **WHEN** 仅存 hub 的患者有 LIS 记录, 专家打开检验 tab
- **THEN** 检验记录按 report_dt 升序展示, 异常值标记与 CSV 路径同规则

#### Scenario: 无检验数据不阻断

- **WHEN** 仅存 hub 的患者在 hub 无 LIS 行
- **THEN** 检验 tab 为空列表, 原文/费用照常展示

### Requirement: 逐患者缓存与重置

hub 查询结果 SHALL 按患者号做进程内 LRU 缓存 (容量有限, 无 TTL); `reset_loader()` MUST 一并清空 hub 缓存。

#### Scenario: 重复点开不重复查库

- **WHEN** 同一 hub 患者在缓存容量内被第二次点开
- **THEN** 不产生新的 hub SQL 查询

#### Scenario: 数据重载后缓存失效

- **WHEN** onboarding 载入新数据触发 `reset_loader()`
- **THEN** 后续取数重新走链式查找 (CSV 新 overlay 优先生效)
