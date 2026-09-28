# routing Specification

## Purpose
TBD - created by archiving change make-rules-code-portable. Update Purpose after archive.
## Requirements
### Requirement: 单闸预筛两步收敛

RuleRouter `route()` MUST 为两步 prune: (1) status/priority 高层过滤; (2) 命中过滤 (名称命中 OR 编码命中, 见 code-based-triggering). router MUST NOT 再执行任何基于患者人口学/就诊属性的 `applicable_*` 硬过滤 (visit_type/gender/age/diag_codes/departments) —— 该五段过滤为死代码 (0 条规则声明、数据源恒 None), 予以移除. 移除后 router 输出的 `final_rules` 对任意患者 MUST 是「移除前实现」的超集或相等 (只增不减, 零漏检).

#### Scenario: 移除 applicable 闸不减少召回

- **WHEN** 对 J66252 与 szx 患者分别用移除 applicable 闸前后的 router 跑同一 rule 集
- **THEN** 移除后的 `final_rules` MUST ⊇ 移除前 (0 条规则因此被多 prune 掉)

#### Scenario: status/priority 闸保留

- **WHEN** 某规则 status 非 enabled (如 abandoned) 或 priority 不在启用档
- **THEN** 该规则仍被第一步 prune 掉 (高层过滤语义不变)

### Requirement: RouterDecision 输出精简

`RouterDecision` MUST NOT 再暴露 Case-A 遗留字段 `overlapping_kept` / `javert_only_kept` (single-gate 下前者概念已废、后者与 `final_rules` 冗余). `final_rules` / `pruned_out` / `stats` 保留; stats MUST NOT 再含 `passed_applicable` / `pruned_applicable` 键. Phase-2 Track A 字段 (`java_triggered` 及 `TriggerEvidence`) 保留不动.

#### Scenario: 消费方只依赖 final_rules

- **WHEN** audit-patient 消费 RouterDecision
- **THEN** 其只读 `final_rules` / `stats`, 不再引用 `overlapping_kept` / `javert_only_kept`

