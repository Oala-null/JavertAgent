# cross-patient-stats Specification

## Purpose
TBD - created by archiving change add-cross-patient-stats. Update Purpose after archive.
## Requirements
### Requirement: 规则维度跨患者聚合

系统 SHALL 提供一个只读统计层, 按 `rule_id` 聚合 audit_runs 的**最新裁决** (每
`(rule_id, patient_id)` 取 `created_at` 最新一条), 产出每条规则的: 被审计患者数 (分母,
含 CLEAN)、V 数、I 数、V 率 (`v / 被审计患者数`)、I 率。聚合 MUST 复用与
`list_patients_with_violations` / `dashboard_stats` 相同的 latest-per-(rule,patient) 口径,
MUST NOT 另立第四处 verdict filter。V 率的分母 SHALL 明示为「被审计患者数」而非全院患者数。

#### Scenario: 同规则同患者重跑只算最新

- **WHEN** 规则 R191 对患者 P 有两条裁决 (旧 CLEAN、新 VIOLATION)
- **THEN** 聚合中 R191 对 P 只计一次, 取最新的 VIOLATION, 患者数 +1、V +1

#### Scenario: V 率分母含 CLEAN 患者

- **WHEN** R191 对 10 个患者有 latest 裁决 (3 V / 7 CLEAN)
- **THEN** 被审计患者数=10, V 率=30%, 分母不排除 CLEAN

### Requirement: 系统性违规双阈值判定

系统 SHALL 用「V 率阈值 (默认 0.5) + 最小样本数 (默认 10 患者)」双条件判定某规则为
系统性违规: `n_patients >= min_patients AND v_rate >= v_rate_threshold`。阈值 SHALL 可经
`configs/systemic_thresholds.yaml` 全局配置并按 `rule_id` 覆盖。判定 MUST 产出一句话归因
(样本数 / 违规数 / V 率)。判定逻辑 MUST 是一个纯函数, CLI 与工作台共用同一实现。

#### Scenario: 达标判系统性

- **WHEN** R191 被审计 20 患者、V 率 0.6, 阈值默认 (0.5 / 10)
- **THEN** 标记 systemic=true, 归因「20 患者中 12 例违规, V 率 60%」

#### Scenario: 样本不足不判定

- **WHEN** R191 被审计 5 患者、V 率 0.8
- **THEN** systemic=false, 归因说明「样本不足 (5 < 10 患者)」

#### Scenario: 按规则覆盖阈值

- **WHEN** `overrides.R191.v_rate = 0.7` 且 R191 实际 V 率 0.6
- **THEN** R191 不判系统性 (用 0.7 而非全局 0.5)

### Requirement: 批次切片

统计层 SHALL 支持按 `batch_tag` 过滤 (只统计某批次/院区的裁决); 缺省 (不传) 时统计全量。

#### Scenario: 只看某批次

- **WHEN** 传入 `batch_tag="szx2.0"`
- **THEN** 聚合只纳入 `batch_tag="szx2.0"` 的裁决行, 分母/分子相应收缩

### Requirement: CLI stats 子命令

系统 SHALL 提供 `javert stats` 命令, 从本地 sqlite 输出规则维度聚合表 (rule_id / 患者数 /
V / I / V 率 / 系统性), 按 V 率倒序, 并可用 `--batch-tag` / `--min-patients` / `--min-v-rate`
覆盖判定参数。命令 MUST NOT 依赖 142/工作台可用。

#### Scenario: 本地出表

- **WHEN** 本地 sqlite 有裁决, 执行 `javert stats`
- **THEN** stdout 打印按 V 率倒序的规则表 + 系统性规则数汇总, 不连 142

### Requirement: 工作台系统性违规面板与导出

工作台 dashboard SHALL 增加只读「系统性违规」区块 (规则维度列表, V 率倒序 + systemic 徽标),
金额列在真金额未接入前 SHALL 显示「不可计」。`/export` SHALL 增加「规则维度」sheet
(rule_id / 患者数 / V / I / V 率 / 金额 / systemic)。面板 MUST NOT 新增任何写路径。

#### Scenario: dashboard 展示系统性区块

- **WHEN** 已登录用户打开 /dashboard 且 142 可用
- **THEN** 页面含「系统性违规」表, 达标规则带徽标, 金额列显示「不可计」

#### Scenario: 导出含规则维度 sheet

- **WHEN** 用户导出 xlsx
- **THEN** 工作簿含「规则维度」sheet, 每行 rule_id + 患者数 + V 率 + systemic

