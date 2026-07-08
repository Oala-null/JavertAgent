# rule-templating — delta spec

## ADDED Requirements

### Requirement: 模板渲染的手改保护

prompt-fit 渲染写盘时 MUST 在 rule yaml 内嵌记录本次渲染产物的 hash (`render_hash` 字段, 对 `prompt_addon` 规范化后取 sha256). 下次 prompt-fit 写盘前 MUST 比对当前 on-disk `prompt_addon` 的 hash 与已记录的 `render_hash`:

- 二者不一致 (说明 `prompt_addon` 自上次渲染后被人工修改) → MUST 拒绝覆盖并提示用 `--force` 显式绕过, 保护专家手改不被静默清除;
- `render_hash` 缺失 (旧规则或非模板来源) → 视为「未知来源」, MUST 仅警告不拦截 (向后兼容), 本次渲染后补齐 hash;
- 一致 → 正常覆盖.

`--force` MUST 无条件放行覆盖并重写 `render_hash`.

#### Scenario: 手改后拒绝覆盖

- **WHEN** 某规则曾由模板渲染 (已存 `render_hash`), 之后 `prompt_addon` 被人工修改, 再次执行 prompt-fit (无 `--force`)
- **THEN** 拒绝写盘并提示手改冲突 + `--force` 绕过方式 (exit code 非 0)

#### Scenario: --force 绕过

- **WHEN** 同上冲突场景但带 `--force`
- **THEN** 覆盖写盘并更新 `render_hash` 为新渲染产物的 hash

#### Scenario: 缺 hash 仅警告

- **WHEN** 规则无 `render_hash` 字段 (旧规则) 且 `prompt_addon` 非空, 执行 prompt-fit
- **THEN** 打印「未知来源」警告, 正常覆盖并首次补齐 `render_hash`

#### Scenario: 一致则静默覆盖

- **WHEN** 规则 `prompt_addon` 的 hash 与 `render_hash` 一致 (自渲染后未被改)
- **THEN** 正常覆盖写盘 (无警告无拦截)
