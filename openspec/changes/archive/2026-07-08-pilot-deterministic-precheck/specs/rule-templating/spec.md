# rule-templating — delta spec

## ADDED Requirements

### Requirement: M1 规则声明 precheck 可读的结构化 A/B 项目集

M1 (`derived_from_template: M1`) 规则的 Rule 模型 MUST 支持可选 `precheck` 结构化字段 (`a_items` / `b_items` 两个字符串列表), 供确定性预检读取, 与渲染进 `prompt_addon` 的自由文本解耦。存量 M1 规则的 `precheck` 块 MUST 可由既有 `prompt_addon` 中规整的 `A 类 (...)` / `B 类 (...)` 项目名一次性抽取生成; 抽取不到完整 A、B 项目集的规则 (已被人工改写为非标准形态者) MUST 被跳过而非猜测填充, 保留其原路径。

#### Scenario: 规整 M1 规则抽取出 precheck 块

- **WHEN** 对 `prompt_addon` 含 `A 类 (主项手术): "…" / "…"` 与 `B 类 (附属手术): "…"` 的 M1 规则跑迁移
- **THEN** 该规则 yaml 获得 `precheck.a_items` / `precheck.b_items` 两个非空列表, 内容为引号内项目名

#### Scenario: 已改写 M1 规则被跳过

- **WHEN** 对 A 或 B 类项目集抽取不到完整两组的 M1 规则 (如带跨日期比对逻辑的改写规则) 跑迁移
- **THEN** 该规则 MUST NOT 写入 `precheck` 块, 迁移输出将其列入「跳过」清单, 其审计仍走原 LLM 路径

### Requirement: precheck 字段向后兼容

新增 `precheck` 字段 MUST 为可选 (默认无), 不得破坏既有 rule yaml 的加载/写回 (`rule_loader` / `rule_writer` round-trip) 或非 M1 规则。

#### Scenario: 无 precheck 字段规则正常加载

- **WHEN** 加载一条不含 `precheck` 字段的既有规则 yaml
- **THEN** 加载成功且 `rule.precheck` 为空 (None), 该规则一切行为不变
