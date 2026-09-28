## Context

Javert 当前最终裁决 JSON 只包含 verdict、confidence、evidence 和 reasoning。reasoning 被完整持久化并通过 v1/v2/v3、SSE 和 Workbench 返回；`public_explanation.narrative` 也保留其完整中文化版本，而 `conclusion.summary` 主要是通用裁决话术，无法承担 2C 首屏“涉及什么、怎样违规”的扫读任务。

现有 runner 在模型输出后还会执行 verdict gate、结构化肿瘤求值、Promise、预检和技术质量隔离。这些步骤可能改变最终 verdict 或 reasoning，因此 headline 不能只靠 prompt，也不能在 gate 之前直接落库。SQLite 与 SQL Server 都保存完整审计结果，2C OCR 发布路径还会直接读取 SQL Server `javert_audit_runs`。

## Goals / Non-Goals

**Goals:**

- 使用同一次模型调用生成具体、短、医院可读的 headline，不增加模型往返。
- headline 与最终而非原始 verdict 保持一致；失败时安全回退且不影响裁决主链。
- 对 LLM、预检、Promise、肿瘤结构化求值和技术失败路径提供统一契约。
- headline 可持久化、可重放，并通过 v1/v2/v3/SSE/Workbench additive 输出。
- 旧数据库行、旧客户端和 reasoning/evidence 兼容语义保持不变。

**Non-Goals:**

- 不让 headline gate 改写 verdict、confidence、reasoning 或 evidence。
- 不新增独立摘要模型、异步摘要队列或读取时模型调用。
- 不从 reasoning 反解析收费、诊断或证据结构。
- 不批量回填历史 headline。
- 不修改 Router、规则选择或既有 verdict gate 判定标准。

## Decisions

### 1. headline 是同一严格裁决 JSON 的一等字段

最终模型 JSON Schema 增加必填 `headline`；base prompt、native structured output、repair、deadline 和 tool-budget 收敛提示都声明相同字段。推荐规范：

- 单行 15–60 个中文字符。
- 包含审核对象和主要违规方式/关键证据缺口。
- 多对象最多点名两个，其余使用“等 N 项”。
- 不包含患者姓名/编号、规则号、工具名、英文 verdict、gate/precheck/内部错误码。
- 不写处置建议、完整证据列表或分析过程。

示例：`维立西呱等3项用药疑似不符医保限定，涉及心功能、适应症及联合用药限制`。

备选方案是独立调用摘要模型。它增加时延、费用和新的失败面，且可能与原审计模型产生语义漂移，因此不采用。

### 2. headline finalizer 位于所有裁决门控之后

runner 先完成当前 verdict/reasoning/evidence 流程，再对模型原始 headline 执行 finalizer：

```text
LLM headline + verdict
        ↓
verdict gate / structured evaluator / quality gate
        ↓
final verdict + final reasoning
        ↓
headline syntax/privacy/verdict gate
        ├─ valid → persist
        └─ invalid or verdict changed → deterministic fallback
```

门控分三层：

1. **结构层**：非空、单行、长度、禁止控制字符和列表段落。
2. **公开安全层**：禁止内部 ID/工具/英文裁决/患者标识，复用现有公开文本清洗规则但不从散文提取事实。
3. **裁决一致层**：VIOLATION 可用明确风险措辞；INCONCLUSIVE 必须体现“疑似/待核/依据不足”；CLEAN 不得声称违规。原始 verdict 被任何 gate 改变时不直接信任原 headline。

headline 不合格只产生安全回退和计数，不触发模型 repair，不影响主裁决。

### 3. 非普通 LLM 路径使用确定性标题

预检短路、Promise locked、肿瘤结构化资格求值、无有效 verdict 和技术质量隔离可能没有可靠模型 headline。它们使用规则元数据、最终 verdict 和已存在的结构化结论生成标题，不读取 reasoning 猜事实。例如：

- `抗体筛查多次检查边界：退费后净数量未超限`
- `超医保限定支付：现有依据不足，待人工核查`
- `未形成可复核异常证据，本规则不输出风险判定`

### 4. 使用 nullable 单列持久化，不创建公共结果 JSON 大字段

`AuditResult` 增加 `headline: str = ""`。SQLite `audit_runs` 与 SQL Server `javert_audit_runs` 增加 nullable headline 文本列，所有 INSERT、同步、完整查询、历史回放和 Workbench/OCR 读取同步接线。

旧行读取为空字符串并走确定性展示回退。单列比新增 `public_json` 更容易查询和保持职责清晰；当前需求不需要建立通用公开投影存储。

### 5. 对外契约只增不删

v1 result、v2/v3 card、SSE result、Workbench 详情增加顶层 `headline`；`public_explanation` 同时增加 headline，便于新医生界面只消费公开投影。原 `reasoning/evidence/public_explanation.narrative` 不删除、不改名、不缩短。

2C 应优先使用 top-level headline，完整分析继续使用 reasoning；严格 DTO 必须允许新增字段。

### 6. 历史结果在读取时使用确定性回退

旧行 headline 为空时，公开 presenter 使用规则 `violation_type/behavior_name/question` 与最终 verdict 生成中性标题。回退不得从 reasoning 反解析项目、金额或临床事实，也不得触发模型调用。

### 7. 用计数和 golden eval 管理 prompt 质量

记录 headline 生成总数、门控回退数及原因枚举，不记录 headline、reasoning、patientId、runId 或业务原文。使用去标识结果集覆盖 VIOLATION、INCONCLUSIVE、CLEAN、多药品、gate 降级、预检、Promise、技术失败；机器门控保证格式/禁词/裁决一致，人工抽检保证“具体但不夸大”。

## Risks / Trade-offs

- [模型 headline 与 gate 后 verdict 冲突] → finalizer 在所有门控后执行；verdict 改变时使用安全回退。
- [headline 产生未经证据支持的新事实] → prompt 要求引用已收集证据，代码门控只接受表面约束，golden eval 专门检查事实忠实度；展示仍保留完整 reasoning/evidence。
- [新增列影响旧库和 OCR 直读] → nullable 幂等 schema 先部署，旧行默认空；2C 在 schema 验证后再升级查询。
- [严格 JSON 增加 malformed 风险] → structured output/repair/deadline 同步字段；headline 无效不重试整个审计，使用确定性回退。
- [过短标题失去医学限定] → 60 字上限并允许“等 N 项”聚合；完整分析一键展开。
- [公开标题泄漏内部或患者标识] → prompt、Pydantic/代码门控、公开 sanitizer 三层防护，并用禁词与去标识测试锁定。

## Migration Plan

1. 实现 nullable SQLite/SQL Server schema 和旧行读取兼容，先跑存储迁移测试。
2. 接入 AuditResult、runner/prompt、headline finalizer 和非 LLM 回退，运行 runner/gate/golden 测试。
3. 在 v1/v2/v3/SSE/Workbench additive 输出 headline，更新合同测试与 2C 对接文档。
4. 按 62 runbook 先部署代码并运行 `ensure-mssql-schema`，再重启服务；验证 v3 同时返回 headline、reasoning、evidence。
5. 2C 再部署消费与渐进式 UI。回滚时客户端忽略 headline；nullable 列保留，不执行破坏性降级。

## Open Questions

- 首发硬上限采用 60 个中文字符；是否调整只由去标识产品可用性测试决定，不按单个长案例放宽。
- Javert 专家 Workbench 是否同步改为默认 headline 属于后续可选消费，不阻塞本 change 的契约交付。
