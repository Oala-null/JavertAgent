## Why

Javert 的完整 reasoning 适合专家追溯，却不适合作为 2C 医生端结果卡的第一阅读层。2C 需要一个稳定、短、具体且不会夸大最终裁决的公开标题，同时保留原 reasoning/evidence 作为完整分析，以支持快速扫读和可信展开。

## What Changes

- 在同一次审计裁决 JSON 中新增 `headline`，要求概括审核对象、主要违规方式或关键证据缺口，不增加第二次模型调用。
- 在全部 verdict、确定性 gate、结构化求值和技术质量门控结束后运行独立 headline 门控，确保标题与最终 verdict 一致且不泄露患者标识、规则号、工具名或内部机制。
- headline 不合格时使用确定性安全回退；标题失败不得阻断、重试或改变临床裁决、reasoning 和 evidence。
- 为预检、Promise、肿瘤结构化求值和技术故障等非普通 LLM 路径生成同样受约束的标题。
- 在 SQLite 与 SQL Server 以可空字段持久化 headline，旧行保持兼容且不回填。
- 在 v1/v2/v3、SSE、Workbench 公开投影中只增不删地返回 headline，并保持完整 reasoning/evidence 原字段和语义不变。
- 为 2C 提供可稳定消费的 additive 契约和部署顺序；新增无内容日志的门控回退计数，用于观察 prompt 质量。

## Capabilities

### New Capabilities

- `public-audit-headline-contract`: 定义模型短标题、最终裁决一致性门控、持久化、旧行回退和 v1/v2/v3 对外字段契约。

### Modified Capabilities

无。

## Impact

- 影响 `src/javert/audit/prompts/base.txt`、严格 verdict JSON Schema、runner 最终结果装配和 `AuditResult` 模型。
- 影响 SQLite/SQL Server schema、双写和所有完整结果读取路径，以及 `public_explanation`、SSE、Workbench、2C v1/v2/v3 卡片投影。
- 影响 2C 对接文档和严格字段集合测试；字段只增不删，旧客户端继续读取 reasoning/evidence。
- 下游 2C change `add-progressive-audit-disclosure` 在 schema/API 上线后消费 headline；不修改 Router、规则选择、verdict gate 的裁决逻辑或历史结果。
