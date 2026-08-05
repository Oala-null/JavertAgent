## Why

Javert 目前用提示词、确定性 precheck/verdict gate、持久化漂移防护和页面后处理分别压制模型漂移，但这些保护没有一个可持续积累的统一边界：已经确认的退费净量、命中项目、对外措辞和原文跳转问题仍可能在另一层重新出现。需要把每次已确认漂移沉淀为版本化、可执行、最小范围的 Promise，并用离线 harness 保证所有后续模型、规则、代码和部署变化都不能破坏已生效边界。

## What Changes

- 新增去标识漂移案例账本、不可静默覆盖的 Promise 版本和受控晋升流程；只有具备可复现正例、相邻反例、最小适用谓词和确认依据的 Promise 才能进入运行时。
- 新增离线确定性 Promise harness 和 CLI 门禁，验证 schema、来源引用、正/反例、版本继承、冲突、公开输出以及历史 Promise 零退化；不调用 LLM、网络或生产数据库。
- 在审计链路新增 Promise 求值与最终锁定语义。命中终局裁决 Promise 时可零 LLM 短路；后续 verdict gate、低置信降级和历史 V→C 漂移防护不得改写锁定结果，只能记录差异。
- 登记首条裁决 Promise：仅对显式纳入的“同项目净数量超过阈值才成立”的次数型规则，若存在退费且同项目净数量不大于 1，则最终锁定为 CLEAN；一次即可违规、套餐多项目和其他未登记规则不在范围内。
- 新增工作台与 2C 共用的结构化公开解释投影；保留旧字段兼容，但公开界面不再显示内部规则号、工具名、gate/run 术语或原始 evidence JSON。
- 收紧公开命中项目：费用/药品命中必须关联患者实际净正收费行；未关联的搜索词只能作为内部检索轨迹，不能显示成“定位”等命中项目。
- 公开类别按行为认定编码和名称唯一分组，并由“两库汇总”H/I 列程序化提取的版本化最小快照校验；未登记类别不得静默回退，既有串换特例必须作为显式、有依据的例外保留。
- 为原文跳转增加分阶段诊断、按 tab 懒加载、受控超时与可重试错误展示；保留既有全量 raw 接口兼容，并增加直连/代理分流的部署冒烟，消除用户可见的无解释 502。
- SQLite/SQL Server 以可空兼容字段记录 Promise ID、版本、事实摘要和最终性；旧行不回填，不用新 Promise 静默改写历史结果。

## Capabilities

### New Capabilities

- `promise-governance`: 漂移案例、最小边界、Promise 生命周期、不可变版本、晋升/替代、冲突与追溯规则。
- `promise-harness`: 离线确定性案例执行、正反例最小性、历史零退化、公开输出一致性和 CLI/CI 门禁。
- `public-audit-output`: 工作台与 2C 共用的医生可读结构化解释、真实命中项目和行为类别唯一投影。
- `source-navigation-reliability`: 原文跳转的按需取数、超时降级、错误可见性、无 PHI 诊断和部署冒烟契约。

### Modified Capabilities

- `audit-engine`: 增加终局 Promise 的 pre-LLM 求值、锁定裁决、可空追溯信息和旧结果兼容行为。
- `verdict-gate`: 明确 active terminal Promise 的优先级高于普通降级闸，且不可被后续 gate 改写。

## Impact

- **审计核心**：`src/javert/audit/runner.py`、结果模型、退费净额事实提取、verdict gate 和持久化漂移防护。
- **配置与测试资产**：新增 `configs/promises/`、去标识 Promise 案例目录、校验/运行 CLI；复用现有 `fee_netting`，不新增任意条件表达式 DSL。
- **存储**：SQLite 与 SQL Server 审计表增加可空 Promise trace 字段，迁移与双写保持旧行兼容。
- **工作台与 2C**：命中解析器、规则元数据分组、公开解释 presenter、模板和 v1/v2/v3 结果投影；API 只加字段，不删改现有字段名。
- **原文链路**：`/api/patient/{patient_id}/raw` 保持兼容，新增按 tab 请求/错误契约、阶段耗时日志和前端懒加载。
- **文档与部署**：同步 2C 契约、工作台指南、架构、62 部署/回滚、CHANGES；生产验收继续遵守 v3 submit/results 和 62 clean/HEAD 门禁。
- **隐私**：Git 内只允许语义化去标识案例；真实患者号、原始病历、未盐化 run ID、凭据和数据库连接信息不得进入 Promise、报告或测试输出。
